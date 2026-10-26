"""Disco mode: chosen lights put on a show, then go back exactly as they were. No AI.

Styles: disco (quick hard colour changes), party (bright colours, short fades), rainbow (a
colour wheel sweeping across the lights) and chill (slow, dim drifting colours). Lights that
can't do colour pulse their brightness instead. What every light was doing beforehand is saved
(also to disk, so a restart mid-party still puts them back), and the show stops by itself after
the set time. Motion lights leave those lights alone while it's on.
"""

from __future__ import annotations

from datetime import datetime, timedelta
import logging
import random
from typing import Any, Callable

from homeassistant.core import CALLBACK_TYPE, Context, HomeAssistant, callback
from homeassistant.helpers import area_registry as ar, device_registry as dr, entity_registry as er
from homeassistant.helpers.event import async_call_later, async_track_time_interval
from homeassistant.helpers.storage import Store
from homeassistant.util import dt as dt_util

from .const import (
    CONF_PARTY_LIGHTS,
    CONF_PARTY_MINUTES,
    CONF_PARTY_STYLE,
    DEFAULT_PARTY_MINUTES,
    DEFAULT_PARTY_STYLE,
    DOMAIN,
    EVENT_MODE_CHANGED,
    PARTY_STYLES,
)

_LOGGER = logging.getLogger(__name__)

STYLES: dict[str, dict[str, Any]] = {
    "disco": {"every": 1.5, "fade": 0, "bright": 100, "low": 25, "pattern": "random"},
    "party": {"every": 4, "fade": 1.5, "bright": 90, "low": 45, "pattern": "random"},
    "rainbow": {"every": 3, "fade": 2.5, "bright": 80, "low": 60, "pattern": "wheel"},
    "chill": {"every": 15, "fade": 12, "bright": 40, "low": 40, "pattern": "wheel"},
}
VIVID = [0, 30, 55, 120, 165, 195, 225, 270, 300, 330]  # hues that look good on bulbs
COLOUR_MODES = {"hs", "rgb", "rgbw", "rgbww", "xy"}
_RESTORE_ATTR = {
    "color_temp": "color_temp_kelvin", "hs": "hs_color", "xy": "xy_color",
    "rgb": "rgb_color", "rgbw": "rgbw_color", "rgbww": "rgbww_color",
}


def can_colour(state) -> bool:
    return bool(state is not None and COLOUR_MODES & set(state.attributes.get("supported_color_modes") or []))


class Party:
    """Disco mode for the house."""

    def __init__(self, hass: HomeAssistant, entry_id: str, options: dict[str, Any], runtime: Callable[[], Any]) -> None:
        self.hass = hass
        self.options = options
        self._rt = runtime
        self._store: Store[dict[str, Any]] = Store(hass, 1, f"{DOMAIN}.{entry_id}.party")
        self.active = False
        self.style = DEFAULT_PARTY_STYLE
        self.lights: list[str] = []
        self.until: datetime | None = None
        self._saved: dict[str, dict[str, Any]] = {}
        self._step = 0
        self._tick_unsub: CALLBACK_TYPE | None = None
        self._end_unsub: CALLBACK_TYPE | None = None
        # every change hangs off this, so the rest of the house sees it as automatic, not someone at a switch
        self._parent = Context()

    # ----- start-up ----------------------------------------------------------------
    async def async_start(self) -> None:
        """Put lights back if Home Assistant restarted in the middle of a party."""
        data = await self._store.async_load() or {}
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
        """The lights to use: a room's lights, the set ones, or the asking device's room."""
        if area:
            return self._area_lights(area)
        chosen = [e for e in self.options.get(CONF_PARTY_LIGHTS) or [] if self.hass.states.get(e)]
        if chosen:
            return chosen
        if device_id and (device := dr.async_get(self.hass).async_get(device_id)) and device.area_id:
            return self._area_lights(device.area_id)
        return []

    def _area_lights(self, area: str) -> list[str]:
        areas = ar.async_get(self.hass)
        found = areas.async_get_area(area) or areas.async_get_area_by_name(area)
        if found is None:
            text = area.strip().lower().removeprefix("the ")
            found = next((a for a in areas.async_list_areas() if a.name.lower() == text or text in a.name.lower()), None)
        if found is None:
            return []
        ents, devs = er.async_get(self.hass), dr.async_get(self.hass)
        out = []
        for entry in ents.entities.values():
            if entry.domain != "light" or entry.disabled_by or entry.hidden_by:
                continue
            area_id = entry.area_id or (d.area_id if entry.device_id and (d := devs.async_get(entry.device_id)) else None)
            if area_id == found.id and self.hass.states.get(entry.entity_id) is not None:
                out.append(entry.entity_id)
        return sorted(out)

    # ----- on and off ----------------------------------------------------------------
    async def async_party(
        self, style: str | None = None, minutes: float | None = None, lights: list[str] | None = None
    ) -> dict[str, Any]:
        style = style if style in STYLES else (self.options.get(CONF_PARTY_STYLE) or DEFAULT_PARTY_STYLE)
        if style not in STYLES:
            style = DEFAULT_PARTY_STYLE
        lights = [e for e in (lights or self.lights_for()) if e.startswith("light.") and self.hass.states.get(e)]
        if not lights:
            return {"error": "No lights to use. Choose disco lights in Sentient's settings, or say which room."}
        if minutes is None:
            minutes = float(self.options.get(CONF_PARTY_MINUTES, DEFAULT_PARTY_MINUTES) or 0)
        # a new set of lights joining an existing party: save the newcomers too
        for e in lights:
            if e not in self._saved:
                self._saved[e] = self._snapshot(e)
        for e in self.lights:
            if e not in lights and e in self._saved:  # dropped from the party: put back now
                await self._async_restore([e])
        await self._store.async_save({"saved": self._saved})
        was_on = self.active
        self.active, self.style, self.lights = True, style, lights
        self._pause_motion(lights)
        self._cancel()
        self.until = dt_util.utcnow() + timedelta(minutes=minutes) if minutes and minutes > 0 else None
        if self.until:
            self._end_unsub = async_call_later(self.hass, minutes * 60, self._ended)
        self._step = 0
        self._tick(dt_util.utcnow())
        self._tick_unsub = async_track_time_interval(self.hass, self._tick, timedelta(seconds=STYLES[style]["every"]))
        names = [st.name for e in lights if (st := self.hass.states.get(e))]
        if (rt := self._rt()) is not None:
            rt.timeline.add(
                "mode", f"Disco mode {'changed to' if was_on else 'on'}: {style}",
                f"{len(lights)} light{'s' if len(lights) != 1 else ''}" + (f", until {dt_util.as_local(self.until):%H:%M}" if self.until else ""),
            )
        self.hass.bus.async_fire(EVENT_MODE_CHANGED, {"party": True, "style": style})
        return {"on": True, "style": style, "lights": names,
                "until": dt_util.as_local(self.until).strftime("%H:%M") if self.until else None}

    async def async_end(self, why: str = "") -> dict[str, Any]:
        if not self.active and not self._saved:
            return {"on": False, "note": "Disco mode wasn't on"}
        self._cancel()
        self.active = False
        await self._async_restore()
        self._resume_motion(self.lights)
        count = len(self.lights)
        self.lights, self._saved, self.until = [], {}, None
        await self._store.async_remove()
        if (rt := self._rt()) is not None:
            rt.timeline.add("mode", "Disco mode off", why or f"{count} light{'s' if count != 1 else ''} back as they were")
        self.hass.bus.async_fire(EVENT_MODE_CHANGED, {"party": False})
        return {"on": False, "restored": count}

    @callback
    def _ended(self, _now: datetime) -> None:
        self._end_unsub = None
        self.hass.async_create_task(self.async_end("time's up; lights back as they were"), eager_start=False)

    @callback
    def _cancel(self) -> None:
        for name in ("_tick_unsub", "_end_unsub"):
            if (unsub := getattr(self, name)) is not None:
                unsub()
                setattr(self, name, None)

    # ----- the show ----------------------------------------------------------------
    @callback
    def _tick(self, _now: datetime) -> None:
        if not self.active:
            return
        cfg = STYLES[self.style]
        n = len(self.lights)
        if cfg["pattern"] == "random":
            hues = random.sample(VIVID, k=min(n, len(VIVID))) + [random.choice(VIVID) for _ in range(max(0, n - len(VIVID)))]
        else:
            base = (self._step * (360 / 24 if self.style != "chill" else 360 / 12)) % 360
            hues = [(base + i * 360 / max(n, 1)) % 360 for i in range(n)]
        high = self._step % 2 == 0
        self._step += 1
        for light, hue in zip(self.lights, hues):
            st = self.hass.states.get(light)
            if st is None or st.state == "unavailable":
                continue
            data: dict[str, Any] = {"entity_id": light}
            if can_colour(st):
                data["hs_color"] = [round(hue), 100 if self.style != "chill" else 80]
                data["brightness_pct"] = cfg["bright"]
            else:
                data["brightness_pct"] = cfg["bright"] if high else cfg["low"]
            if cfg["fade"]:
                data["transition"] = cfg["fade"]
            self._call("turn_on", data)

    @callback
    def _call(self, service: str, data: dict[str, Any]) -> None:
        context = Context(parent_id=self._parent.id)
        self.hass.async_create_task(
            self.hass.services.async_call("light", service, data, blocking=False, context=context), eager_start=True
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
            "lights": [st.name for e in self.lights if (st := self.hass.states.get(e))],
            "until": dt_util.as_local(self.until).strftime("%H:%M") if self.until else None,
        }
