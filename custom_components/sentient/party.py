"""Light shows (disco mode): chosen lights put on a show, then go back exactly as they were. No AI.

Styles:
* disco — quick hard colour changes
* party — bright colours with short fades
* rainbow — a colour wheel sweeping across the lights
* chill — slow, dim drifting colours
* halloween — flickering orange, purple and green
* christmas — red, green and warm white, twinkling

Lights that can't do colour pulse their brightness instead; lights that only go on and off sit
it out. It can use the chosen lights, a room's lights, or every light in the house.

With a loudness sensor (for example a voice speaker's microphone through ESPHome's
sound_level sensor), the lively styles follow the room: louder is brighter, and a jump in
volume — a beat — changes colour straight away.

What every light was doing is saved first (also to disk, so a restart mid-show still puts them
back), and the show stops by itself after the set time. Motion lights leave those lights alone
meanwhile.
"""

from __future__ import annotations

from datetime import datetime, timedelta
import logging
import random
import time as _time
from typing import Any, Callable

from homeassistant.core import CALLBACK_TYPE, Context, Event, HomeAssistant, callback
from homeassistant.helpers import area_registry as ar, device_registry as dr, entity_registry as er
from homeassistant.helpers.event import (
    async_call_later,
    async_track_state_change_event,
    async_track_time_interval,
)
from homeassistant.helpers.storage import Store
from homeassistant.util import dt as dt_util

from .const import (
    CONF_PARTY_EXCLUDE,
    CONF_PARTY_LIGHTS,
    CONF_PARTY_MINUTES,
    CONF_PARTY_SOUND,
    CONF_PARTY_SOUND_SWITCH,
    CONF_PARTY_STYLE,
    CONF_PARTY_WHOLE_HOUSE,
    DEFAULT_PARTY_MINUTES,
    DEFAULT_PARTY_STYLE,
    DOMAIN,
    EVENT_MODE_CHANGED,
    PARTY_STYLES,
)

_LOGGER = logging.getLogger(__name__)

VIVID = [(h, 100) for h in (0, 30, 55, 120, 165, 195, 225, 270, 300, 330)]  # colours that look good on bulbs
STYLES: dict[str, dict[str, Any]] = {
    "disco": {"every": 1.5, "fade": 0, "bright": 100, "low": 25, "pattern": "random", "palette": VIVID, "react": True},
    "party": {"every": 4, "fade": 1.5, "bright": 90, "low": 45, "pattern": "random", "palette": VIVID, "react": True},
    "rainbow": {"every": 3, "fade": 2.5, "bright": 80, "low": 60, "pattern": "wheel", "react": True},
    "chill": {"every": 15, "fade": 12, "bright": 40, "low": 40, "pattern": "wheel", "sat": 80, "react": False},
    "halloween": {"every": 2.5, "fade": 0.8, "bright": 85, "low": 15, "pattern": "flicker",
                  "palette": [(25, 100), (280, 100), (110, 100)], "react": True},
    "christmas": {"every": 5, "fade": 3, "bright": 90, "low": 55, "pattern": "twinkle",
                  "palette": [(0, 100), (120, 100), (35, 25)], "react": False},
}
EVERYWHERE = {"everywhere", "every room", "all", "all lights", "every light", "whole house", "the whole house", "house", "the house"}
COLOUR_MODES = {"hs", "rgb", "rgbw", "rgbww", "xy"}
_RESTORE_ATTR = {
    "color_temp": "color_temp_kelvin", "hs": "hs_color", "xy": "xy_color",
    "rgb": "rgb_color", "rgbw": "rgbw_color", "rgbww": "rgbww_color",
}
BEAT_DB = 4.0  # this much above the running average is a beat
BEAT_GAP = 0.35  # seconds; no faster than this, so bulbs and the network keep up
BEAT_LIGHTS = 4  # lights changed per beat (taking turns), so a whole-house show doesn't flood the network


def _plain(state) -> bool:
    """A light that can only go on and off: nothing to show, so it sits the show out."""
    return set(state.attributes.get("supported_color_modes") or ["onoff"]) <= {"onoff"}


def can_colour(state) -> bool:
    return bool(state is not None and COLOUR_MODES & set(state.attributes.get("supported_color_modes") or []))


def _group(state) -> bool:
    return isinstance(state.attributes.get("entity_id"), (list, tuple))  # a light group: its members join directly


class Party:
    """Light shows for the house."""

    def __init__(self, hass: HomeAssistant, entry_id: str, options: dict[str, Any], runtime: Callable[[], Any]) -> None:
        self.hass = hass
        self.options = options
        self._rt = runtime
        self._store: Store[dict[str, Any]] = Store(hass, 1, f"{DOMAIN}.{entry_id}.party")
        self.active = False
        self.style = DEFAULT_PARTY_STYLE
        self.lights: list[str] = []
        self.until: datetime | None = None
        self.listening = False  # following the room's sound
        self._saved: dict[str, dict[str, Any]] = {}
        self._step = 0
        self._turn = 0
        self._tick_unsub: CALLBACK_TYPE | None = None
        self._end_unsub: CALLBACK_TYPE | None = None
        self._sound_unsub: CALLBACK_TYPE | None = None
        self._avg: float | None = None
        self._last_beat = 0.0
        # every change hangs off this, so the rest of the house sees it as automatic, not someone at a switch
        self._parent = Context()

    # ----- start-up ----------------------------------------------------------------
    async def async_start(self) -> None:
        """Put lights back if Home Assistant restarted in the middle of a show."""
        data = await self._store.async_load() or {}
        await self._async_sound_switch(False)  # in case a show was cut short by a restart
        if saved := data.get("saved"):
            self._saved = saved
            self.lights = list(saved)
            await self._async_restore()
            self._saved = {}
            await self._store.async_remove()

    @callback
    def async_stop(self) -> None:
        self._cancel()

    # ----- which lights ----------------------------------------------------------------
    def lights_for(self, area: str | None = None, device_id: str | None = None) -> list[str]:
        """A room's lights, every light ('everywhere'), the chosen ones, or the asking device's room."""
        if area:
            if area.strip().lower() in EVERYWHERE:
                return self.house_lights()
            return self._area_lights(area)
        if self.options.get(CONF_PARTY_WHOLE_HOUSE):
            return self.house_lights()
        chosen = [e for e in self.options.get(CONF_PARTY_LIGHTS) or [] if self.hass.states.get(e)]
        if chosen:
            return chosen
        if device_id and (device := dr.async_get(self.hass).async_get(device_id)) and device.area_id:
            return self._area_lights(device.area_id)
        return []

    def _usable(self, area_id: str | None = None) -> list[str]:
        ents, devs = er.async_get(self.hass), dr.async_get(self.hass)
        skip = set(self.options.get(CONF_PARTY_EXCLUDE) or [])
        out = []
        for entry in ents.entities.values():
            if entry.domain != "light" or entry.disabled_by or entry.hidden_by or entry.entity_category:
                continue
            if entry.entity_id in skip:
                continue
            st = self.hass.states.get(entry.entity_id)
            if st is None or st.state == "unavailable" or _plain(st) or _group(st):
                continue
            if area_id is not None:
                where = entry.area_id or (d.area_id if entry.device_id and (d := devs.async_get(entry.device_id)) else None)
                if where != area_id:
                    continue
            out.append(entry.entity_id)
        return sorted(out)

    def house_lights(self) -> list[str]:
        return self._usable()

    def _area_lights(self, area: str) -> list[str]:
        areas = ar.async_get(self.hass)
        found = areas.async_get_area(area) or areas.async_get_area_by_name(area)
        if found is None:
            text = area.strip().lower().removeprefix("the ")
            found = next((a for a in areas.async_list_areas() if a.name.lower() == text or text in a.name.lower()), None)
        return self._usable(found.id) if found else []

    # ----- on and off ----------------------------------------------------------------
    async def async_party(
        self, style: str | None = None, minutes: float | None = None, lights: list[str] | None = None
    ) -> dict[str, Any]:
        style = (style or "").strip().lower()
        if style not in STYLES:
            style = self.options.get(CONF_PARTY_STYLE) or DEFAULT_PARTY_STYLE
        if style not in STYLES:
            style = DEFAULT_PARTY_STYLE
        lights = [e for e in (lights or self.lights_for()) if e.startswith("light.") and self.hass.states.get(e)]
        if not lights:
            return {"error": "No lights to use. Choose show lights in Sentient's settings, or say which room (or 'everywhere')."}
        if minutes is None:
            minutes = float(self.options.get(CONF_PARTY_MINUTES, DEFAULT_PARTY_MINUTES) or 0)
        for e in lights:  # save each light the first time it joins
            if e not in self._saved:
                self._saved[e] = self._snapshot(e)
        dropped = [e for e in self.lights if e not in lights and e in self._saved]
        if dropped:
            await self._async_restore(dropped)
            self._resume_motion(dropped)
        await self._store.async_save({"saved": self._saved})
        was_on, old_style = self.active, self.style
        self.active, self.style, self.lights = True, style, lights
        self._pause_motion(lights)
        self._cancel()
        self.until = dt_util.utcnow() + timedelta(minutes=minutes) if minutes and minutes > 0 else None
        if self.until:
            self._end_unsub = async_call_later(self.hass, minutes * 60, self._ended)
        self._step = 0
        self._tick(dt_util.utcnow())
        self._listen()
        every = STYLES[style]["every"] * (2 if self.listening else 1)  # with sound, the beats do most of the work
        self._tick_unsub = async_track_time_interval(self.hass, self._tick, timedelta(seconds=every))
        names = [st.name for e in lights if (st := self.hass.states.get(e))]
        if (rt := self._rt()) is not None:
            rt.timeline.add(
                "mode", f"Light show {'changed to' if was_on and old_style != style else 'on'}: {style}",
                f"{len(lights)} light{'s' if len(lights) != 1 else ''}"
                + (", following the music" if self.listening else "")
                + (f", until {dt_util.as_local(self.until):%H:%M}" if self.until else ""),
            )
        self.hass.bus.async_fire(EVENT_MODE_CHANGED, {"party": True, "style": style})
        return {"on": True, "style": style, "lights": names, "following_sound": self.listening,
                "until": dt_util.as_local(self.until).strftime("%H:%M") if self.until else None}

    async def async_end(self, why: str = "") -> dict[str, Any]:
        if not self.active and not self._saved:
            return {"on": False, "note": "No light show was on"}
        self._cancel()
        self.active = False
        await self._async_restore()
        self._resume_motion(self.lights)
        count = len(self.lights)
        self.lights, self._saved, self.until = [], {}, None
        await self._store.async_remove()
        if (rt := self._rt()) is not None:
            rt.timeline.add("mode", "Light show off", why or f"{count} light{'s' if count != 1 else ''} back as they were")
        self.hass.bus.async_fire(EVENT_MODE_CHANGED, {"party": False})
        return {"on": False, "restored": count}

    @callback
    def _ended(self, _now: datetime) -> None:
        self._end_unsub = None
        self.hass.async_create_task(self.async_end("time's up; lights back as they were"), eager_start=False)

    @callback
    def _cancel(self) -> None:
        for name in ("_tick_unsub", "_end_unsub", "_sound_unsub"):
            if (unsub := getattr(self, name)) is not None:
                unsub()
                setattr(self, name, None)
        if self.listening:
            self.listening = False
            self.hass.async_create_task(self._async_sound_switch(False), eager_start=False)

    def _sound_switch(self) -> str | None:
        """The switch that makes the loudness sensor report: the one set, or one on the same device."""
        if chosen := self.options.get(CONF_PARTY_SOUND_SWITCH):
            return chosen
        sensor = self.options.get(CONF_PARTY_SOUND)
        ents = er.async_get(self.hass)
        if not sensor or (entry := ents.async_get(sensor)) is None or not entry.device_id:
            return None
        for other in er.async_entries_for_device(ents, entry.device_id):
            if other.domain == "switch" and ("loudness" in other.entity_id or "sound_level" in other.entity_id):
                return other.entity_id
        return None

    async def _async_sound_switch(self, on: bool) -> None:
        if (switch := self._sound_switch()) is None or (st := self.hass.states.get(switch)) is None:
            return
        if (st.state == "on") == on or st.state == "unavailable":
            return
        try:
            await self.hass.services.async_call(
                "switch", "turn_on" if on else "turn_off", {"entity_id": switch},
                blocking=False, context=Context(parent_id=self._parent.id),
            )
        except Exception as err:  # noqa: BLE001
            _LOGGER.debug("Could not switch %s: %s", switch, err)

    # ----- following the room's sound ---------------------------------------------------
    @callback
    def _listen(self) -> None:
        sensor = self.options.get(CONF_PARTY_SOUND)
        if not sensor or not STYLES[self.style]["react"] or self.hass.states.get(sensor) is None:
            return
        self._avg, self._last_beat = None, 0.0
        self._sound_unsub = async_track_state_change_event(self.hass, [sensor], self._heard)
        self.listening = True
        self.hass.async_create_task(self._async_sound_switch(True), eager_start=False)

    @callback
    def _heard(self, event: Event) -> None:
        new = event.data.get("new_state")
        try:
            level = float(new.state)  # type: ignore[union-attr]
        except (AttributeError, TypeError, ValueError):
            return
        if self._avg is None:
            self._avg = level
            return
        jump = level - self._avg
        self._avg = self._avg * 0.85 + level * 0.15
        now = _time.monotonic()
        if jump >= BEAT_DB and now - self._last_beat >= BEAT_GAP:
            self._last_beat = now
            self._beat(min(1.0, max(0.0, (jump - BEAT_DB) / 12 + 0.5)))

    @callback
    def _beat(self, strength: float) -> None:
        """A beat: the next few lights jump to a new colour, brighter the louder it was."""
        if not self.active or not self.lights:
            return
        cfg = STYLES[self.style]
        n = len(self.lights)
        k = min(n, BEAT_LIGHTS)
        chosen = [self.lights[(self._turn + i) % n] for i in range(k)]
        self._turn = (self._turn + k) % n
        bright = round(cfg["low"] + (cfg["bright"] - cfg["low"]) * strength)
        for light in chosen:
            st = self.hass.states.get(light)
            if st is None or st.state == "unavailable" or _plain(st):
                continue
            data: dict[str, Any] = {"entity_id": light, "brightness_pct": max(1, bright)}
            if can_colour(st):
                hue, sat = self._colour()
                data["hs_color"] = [hue, sat]
            self._call(data)

    def _colour(self) -> tuple[int, int]:
        cfg = STYLES[self.style]
        if palette := cfg.get("palette"):
            return random.choice(palette)
        return random.randrange(0, 360, 15), cfg.get("sat", 100)

    # ----- the show ----------------------------------------------------------------
    @callback
    def _tick(self, _now: datetime) -> None:
        if not self.active:
            return
        cfg = STYLES[self.style]
        n = len(self.lights)
        pattern = cfg["pattern"]
        if pattern == "random":
            pal = cfg["palette"]
            colours = random.sample(pal, k=min(n, len(pal))) + [random.choice(pal) for _ in range(max(0, n - len(pal)))]
        elif pattern == "wheel":
            base = (self._step * (360 / 24 if self.style != "chill" else 360 / 12)) % 360
            colours = [(round((base + i * 360 / max(n, 1)) % 360), cfg.get("sat", 100)) for i in range(n)]
        elif pattern == "twinkle":
            pal = cfg["palette"]
            colours = [pal[(i + self._step) % len(pal)] for i in range(n)]
        else:  # flicker
            colours = [random.choice(cfg["palette"]) for _ in range(n)]
        high = self._step % 2 == 0
        self._step += 1
        for light, (hue, sat) in zip(self.lights, colours):
            st = self.hass.states.get(light)
            if st is None or st.state == "unavailable" or _plain(st):
                continue
            if pattern in ("flicker", "twinkle"):
                bright = random.randint(cfg["low"], cfg["bright"])
            elif can_colour(st):
                bright = cfg["bright"]
            else:
                bright = cfg["bright"] if high else cfg["low"]
            data: dict[str, Any] = {"entity_id": light, "brightness_pct": bright}
            if can_colour(st):
                data["hs_color"] = [hue, sat]
            if cfg["fade"]:
                data["transition"] = cfg["fade"]
            self._call(data)

    @callback
    def _call(self, data: dict[str, Any]) -> None:
        context = Context(parent_id=self._parent.id)
        self.hass.async_create_task(
            self.hass.services.async_call("light", "turn_on", data, blocking=False, context=context), eager_start=True
        )

    # ----- saving and putting back ----------------------------------------------------
    def _snapshot(self, light: str) -> dict[str, Any]:
        st = self.hass.states.get(light)
        if st is None or st.state != "on":
            return {"on": False}
        snap: dict[str, Any] = {"on": True, "brightness": st.attributes.get("brightness")}
        mode = st.attributes.get("color_mode")
        if (attr := _RESTORE_ATTR.get(mode)) and st.attributes.get(attr) is not None:
            value = st.attributes.get(attr)
            snap[attr] = list(value) if isinstance(value, (list, tuple)) else value
        return snap

    async def _async_restore(self, only: list[str] | None = None) -> None:
        for light in only or list(self._saved):
            snap = self._saved.pop(light, None) if only else self._saved.get(light)
            if not snap or self.hass.states.get(light) is None:
                continue
            context = Context(parent_id=self._parent.id)
            try:
                if not snap.get("on"):
                    await self.hass.services.async_call("light", "turn_off", {"entity_id": light}, blocking=True, context=context)
                    continue
                data: dict[str, Any] = {"entity_id": light}
                if snap.get("brightness"):
                    data["brightness"] = snap["brightness"]
                for attr in _RESTORE_ATTR.values():
                    if snap.get(attr) is not None:
                        data[attr] = snap[attr]
                await self.hass.services.async_call("light", "turn_on", data, blocking=True, context=context)
            except Exception as err:  # noqa: BLE001 - one bad light shouldn't stop the rest
                _LOGGER.debug("Could not put %s back: %s", light, err)

    # ----- motion lights step aside ------------------------------------------------------
    def _pause_motion(self, lights: list[str]) -> None:
        if (rt := self._rt()) is not None and (ml := getattr(rt, "motion_lights", None)) is not None:
            ml.pause(lights)

    def _resume_motion(self, lights: list[str]) -> None:
        if (rt := self._rt()) is not None and (ml := getattr(rt, "motion_lights", None)) is not None:
            ml.resume(lights)

    def describe(self) -> dict[str, Any]:
        return {
            "on": self.active, "style": self.style if self.active else None,
            "styles": PARTY_STYLES,
            "following_sound": self.listening,
            "lights": [st.name for e in self.lights if (st := self.hass.states.get(e))],
            "until": dt_util.as_local(self.until).strftime("%H:%M") if self.until else None,
        }
