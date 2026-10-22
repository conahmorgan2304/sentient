"""Motion lights: a light comes on with movement after dark and goes off once it stops.

Each light has its own rule (stored, edited from the sidebar page):
- the motion sensors that wake it, and how long after the last movement it goes off;
- whether it only works between sunset and sunrise;
- whether it stays off during quiet hours (some lights, like a landing, may not want to);
- its brightness, and a dimmer brightness for late at night.

No AI. A light someone switched on by hand is left alone, and if someone changes a light the
house switched on, the house stops managing it until it's next off.
"""

from __future__ import annotations

from dataclasses import asdict, dataclass, field
from datetime import datetime, time
import logging
from typing import Any
import uuid

from homeassistant.const import STATE_ON
from homeassistant.core import CALLBACK_TYPE, Context, Event, EventStateChangedData, HomeAssistant, callback
from homeassistant.helpers.event import async_call_later, async_track_state_change_event
from homeassistant.helpers.storage import Store
from homeassistant.helpers.sun import is_up
from homeassistant.util import dt as dt_util

from .const import DOMAIN
from .speech import SpeechRouter, in_quiet_hours

_LOGGER = logging.getLogger(__name__)

STORAGE_VERSION = 1
DEFAULT_NIGHT_START = "23:00"
DEFAULT_NIGHT_END = "06:00"
EVENT_MOTION_LIGHTS = f"{DOMAIN}_motion_lights"


@dataclass
class MotionRule:
    """One light and what wakes it."""

    light: str
    motion: list[str]
    enabled: bool = True
    respect_quiet: bool = True
    only_dark: bool = True
    off_after: float = 3  # minutes after the last movement
    brightness: int = 100  # percent; ignored for switches
    night_brightness: int = 10  # percent late at night; 0 = no dimming
    id: str = field(default_factory=lambda: uuid.uuid4().hex[:6])
    last: str | None = None  # what happened last, for the page
    last_time: str | None = None

    @classmethod
    def from_dict(cls, data: dict[str, Any]) -> MotionRule:
        known = {k: v for k, v in data.items() if k in cls.__dataclass_fields__}
        motion = known.get("motion") or []
        known["motion"] = [motion] if isinstance(motion, str) else list(motion)
        return cls(**known)


def _time(value: str | None, fallback: str) -> time:
    try:
        return time.fromisoformat(value or fallback)
    except ValueError:
        return time.fromisoformat(fallback)


class MotionLights:
    """Runs every motion rule."""

    def __init__(self, hass: HomeAssistant, entry_id: str, speech: SpeechRouter) -> None:
        self.hass = hass
        self.speech = speech
        self._store: Store[dict[str, Any]] = Store(hass, STORAGE_VERSION, f"{DOMAIN}.{entry_id}.motion_lights")
        self.rules: dict[str, MotionRule] = {}
        self.night_start = DEFAULT_NIGHT_START
        self.night_end = DEFAULT_NIGHT_END
        self._ours: dict[str, set[str]] = {}  # light -> context ids of our own changes
        self._timers: dict[str, CALLBACK_TYPE] = {}
        self._unsub: CALLBACK_TYPE | None = None

    # ----- storage ---------------------------------------------------------------
    async def async_load(self) -> None:
        data = await self._store.async_load() or {}
        self.rules = {}
        for raw in data.get("rules", []):
            try:
                rule = MotionRule.from_dict(raw)
            except TypeError:
                continue
            self.rules[rule.id] = rule
        self.night_start = data.get("night_start", DEFAULT_NIGHT_START)
        self.night_end = data.get("night_end", DEFAULT_NIGHT_END)

    def _save(self) -> None:
        self._store.async_delay_save(
            lambda: {
                "rules": [asdict(r) for r in self.rules.values()],
                "night_start": self.night_start,
                "night_end": self.night_end,
            },
            2,
        )
        self.hass.bus.async_fire(EVENT_MOTION_LIGHTS, {"count": len(self.rules)})

    # ----- editing ---------------------------------------------------------------
    def upsert(self, data: dict[str, Any]) -> MotionRule:
        rule_id = data.get("id")
        old = self.rules.get(rule_id) if rule_id else None
        merged = {**(asdict(old) if old else {}), **data}
        if not merged.get("light") or not merged.get("motion"):
            raise ValueError("A motion light needs a light and at least one motion sensor")
        if old is None:
            merged.pop("id", None)
            if any(r.light == merged["light"] for r in self.rules.values()):
                raise ValueError("That light already has a motion rule")
        rule = MotionRule.from_dict(merged)
        rule.off_after = max(0.5, min(240.0, float(rule.off_after)))
        rule.brightness = max(1, min(100, int(rule.brightness)))
        rule.night_brightness = max(0, min(100, int(rule.night_brightness)))
        self.rules[rule.id] = rule
        self._save()
        self._resubscribe()
        return rule

    def remove(self, rule_id: str) -> bool:
        rule = self.rules.pop(rule_id, None)
        if rule is None:
            return False
        self._cancel_timer(rule.light)
        self._ours.pop(rule.light, None)
        self._save()
        self._resubscribe()
        return True

    def set_night(self, start: str, end: str) -> None:
        self.night_start = _time(start, DEFAULT_NIGHT_START).strftime("%H:%M")
        self.night_end = _time(end, DEFAULT_NIGHT_END).strftime("%H:%M")
        self._save()

    @property
    def managed_lights(self) -> set[str]:
        return {r.light for r in self.rules.values()}

    # ----- running ----------------------------------------------------------------
    @callback
    def async_start(self) -> None:
        self._resubscribe()

    @callback
    def async_stop(self) -> None:
        if self._unsub:
            self._unsub()
            self._unsub = None
        for unsub in self._timers.values():
            unsub()
        self._timers.clear()

    @callback
    def _resubscribe(self) -> None:
        if self._unsub:
            self._unsub()
            self._unsub = None
        watched = set()
        for rule in self.rules.values():
            watched.update(rule.motion)
            watched.add(rule.light)
        if watched:
            self._unsub = async_track_state_change_event(self.hass, sorted(watched), self._changed)

    def late_night(self, now: datetime | None = None) -> bool:
        return in_quiet_hours(
            now or dt_util.now(), _time(self.night_start, DEFAULT_NIGHT_START), _time(self.night_end, DEFAULT_NIGHT_END)
        )

    def _dark(self) -> bool:
        sun = self.hass.states.get("sun.sun")
        if sun is not None:
            return sun.state == "below_horizon"
        return not is_up(self.hass)

    def _note(self, rule: MotionRule, what: str) -> None:
        rule.last, rule.last_time = what, dt_util.utcnow().isoformat()
        self._save()

    def _motion_now(self, rule: MotionRule) -> bool:
        return any((st := self.hass.states.get(e)) is not None and st.state == STATE_ON for e in rule.motion)

    @callback
    def _changed(self, event: Event[EventStateChangedData]) -> None:
        entity_id = event.data["entity_id"]
        new = event.data["new_state"]
        if new is None:
            return
        for rule in list(self.rules.values()):
            if entity_id == rule.light:
                self._light_changed(rule, event)
            elif entity_id in rule.motion and rule.enabled:
                if new.state == STATE_ON:
                    self._on_motion(rule)
                elif not self._motion_now(rule):
                    self._on_still(rule)

    @callback
    def _light_changed(self, rule: MotionRule, event: Event[EventStateChangedData]) -> None:
        if rule.light not in self._ours:
            return
        new = event.data["new_state"]
        if new is None or new.state != STATE_ON:
            self._release(rule.light)
            return
        if event.context.id not in self._ours[rule.light]:
            # someone changed it by hand: theirs now, so don't switch it off on them
            self._release(rule.light)
            self._note(rule, "taken over by hand")

    @callback
    def _release(self, light: str) -> None:
        self._ours.pop(light, None)
        self._cancel_timer(light)

    @callback
    def _cancel_timer(self, light: str) -> None:
        if (unsub := self._timers.pop(light, None)) is not None:
            unsub()

    @callback
    def _on_motion(self, rule: MotionRule) -> None:
        self._cancel_timer(rule.light)
        light = self.hass.states.get(rule.light)
        if light is None:
            return
        if rule.light in self._ours:
            if light.state == STATE_ON:
                return  # already ours and on: just keep it on
            self._ours.pop(rule.light, None)
        if light.state == STATE_ON:
            return  # someone else's light
        if rule.only_dark and not self._dark():
            return
        if rule.respect_quiet and self.speech.quiet_now():
            self._note(rule, "stayed off: quiet hours")
            return
        late = self.late_night() and rule.night_brightness > 0
        self._turn(rule, True, rule.night_brightness if late else rule.brightness)
        self._note(rule, f"on at {rule.night_brightness}% (late night)" if late else "on with motion")

    @callback
    def _on_still(self, rule: MotionRule) -> None:
        if rule.light not in self._ours:
            return
        self._cancel_timer(rule.light)

        @callback
        def _off(_now: datetime) -> None:
            self._timers.pop(rule.light, None)
            if rule.light in self._ours and not self._motion_now(rule):
                self._turn(rule, False)
                self._note(rule, "off: no movement")

        self._timers[rule.light] = async_call_later(self.hass, rule.off_after * 60, _off)

    @callback
    def _turn(self, rule: MotionRule, on: bool, brightness: int | None = None) -> None:
        context = Context()
        self._ours.setdefault(rule.light, set()).add(context.id)
        domain = rule.light.split(".", 1)[0]
        service_domain = domain if domain in ("light", "switch", "input_boolean", "fan") else "homeassistant"
        data: dict[str, Any] = {"entity_id": rule.light}
        if on and domain == "light" and brightness:
            data["brightness_pct"] = brightness
        self.hass.async_create_task(
            self.hass.services.async_call(
                service_domain, "turn_on" if on else "turn_off", data, blocking=False, context=context
            ),
            eager_start=True,
        )

    # ----- for the page ------------------------------------------------------------------
    def listing(self) -> dict[str, Any]:
        rules = []
        for rule in self.rules.values():
            item = asdict(rule)
            item["managing"] = rule.light in self._ours
            item["timer"] = rule.light in self._timers
            rules.append(item)
        return {
            "rules": rules,
            "night_start": self.night_start,
            "night_end": self.night_end,
            "dark": self._dark(),
            "quiet": self.speech.quiet_now(),
            "late_night": self.late_night(),
        }
