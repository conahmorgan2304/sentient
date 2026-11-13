"""Motion lights: a light comes on with movement after dark and goes off once it stops.

Each light has its own rule (stored, edited from the sidebar page):
- the motion sensors that wake it, and how long after the last movement it goes off;
- whether it only works between sunset and sunrise;
- whether it stays off during quiet hours, with an optional window for this light;
- its brightness, and a dimmer brightness for late at night.

No AI. A light someone switched on by hand is left alone, and if someone changes a light the
house switched on, the house stops managing it until it's next off.
"""

from __future__ import annotations

from dataclasses import asdict, dataclass, field
from datetime import datetime, time, timedelta
import logging
from typing import Any
import uuid

from homeassistant.const import STATE_ON
from homeassistant.core import CALLBACK_TYPE, Event, EventStateChangedData, HomeAssistant, callback
from homeassistant.helpers.event import async_call_later, async_track_state_change_event, async_track_time_change
from homeassistant.helpers.storage import Store
from homeassistant.helpers.sun import is_up
from homeassistant.util import dt as dt_util

from .const import DOMAIN
from .speech import SpeechRouter, in_quiet_hours

_LOGGER = logging.getLogger(__name__)

STORAGE_VERSION = 1
DEFAULT_NIGHT_START = "23:00"
DEFAULT_NIGHT_END = "06:00"
SETTLE = timedelta(seconds=30)  # a light reporting back after our command is not a person
EVENT_MOTION_LIGHTS = f"{DOMAIN}_motion_lights"
EVENT_MOTION_LIGHT = f"{DOMAIN}_motion_light"


@dataclass
class MotionRule:
    """One light and what wakes it."""

    light: str
    motion: list[str]
    enabled: bool = True
    respect_quiet: bool = True
    protect_voice_override: bool = False  # opt in: don't immediately undo a recent guarded voice command
    only_dark: bool = True
    off_after: float = 3  # minutes after the last movement
    brightness: int = 100  # percent; ignored for switches
    night_brightness: int = 10  # percent late at night; 0 = no dimming
    lux_sensor: str | None = None  # optional: dark means this reads below lux_below
    lux_below: float = 30
    id: str = field(default_factory=lambda: uuid.uuid4().hex[:6])
    last: str | None = None  # what happened last, for the page
    last_time: str | None = None
    quiet_start: str | None = None
    quiet_end: str | None = None

    def quiet_window(self) -> tuple[time, time] | None:
        if not (self.quiet_start or self.quiet_end):
            return None
        try:
            start = time.fromisoformat(self.quiet_start or "")
            end = time.fromisoformat(self.quiet_end or "")
        except (TypeError, ValueError):
            raise ValueError("Set both quiet times, or clear both to use the house hours") from None
        if any(t.tzinfo or t.second or t.microsecond for t in (start, end)) or start == end:
            raise ValueError("Quiet times must be different local times in hours and minutes")
        return start, end

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
        self._sent: dict[str, datetime] = {}  # light -> when we last switched it
        self._timers: dict[str, CALLBACK_TYPE] = {}
        self._listeners: list[CALLBACK_TYPE] = []
        self.paused: set[str] = set()  # lights something else is running for now (disco mode)

    # ----- storage ---------------------------------------------------------------
    async def async_load(self) -> None:
        data = await self._store.async_load() or {}
        self.rules = {}
        for raw in data.get("rules", []):
            try:
                rule = MotionRule.from_dict(raw)
            except TypeError:
                continue
            try:
                rule.quiet_window()
            except ValueError:
                rule.quiet_start = rule.quiet_end = None
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
        if type(rule.protect_voice_override) is not bool:
            raise ValueError("Protect voice overrides must be on or off")
        window = rule.quiet_window()
        if window:
            start, end = window
            rule.quiet_start = start.isoformat(timespec="minutes")
            rule.quiet_end = end.isoformat(timespec="minutes")
        else:
            rule.quiet_start = rule.quiet_end = None
        rule.off_after = max(0.5, min(240.0, float(rule.off_after)))
        rule.brightness = max(1, min(100, int(rule.brightness)))
        rule.night_brightness = max(0, min(100, int(rule.night_brightness)))
        self.rules[rule.id] = rule
        if old and (old.light != rule.light or not rule.enabled):
            self._release(old.light)
        self._save()
        self._resubscribe()
        self._off_during_quiet(rule)
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

    def find(self, name: str) -> MotionRule | None:
        """A rule by id, entity id or (part of) the light's name."""
        text = name.strip().lower()
        best = None
        for rule in self.rules.values():
            st = self.hass.states.get(rule.light)
            label = (st.name if st else rule.light).lower()
            if text in (rule.id, rule.light.lower(), label):
                return rule
            if text and (text in label or label in text or text in rule.light):
                best = best or rule
        return best

    def set_enabled(self, rule: MotionRule, enabled: bool) -> None:
        rule.enabled = enabled
        if not enabled:
            self._release(rule.light)
        self._save()

    @callback
    def pause(self, lights: list[str]) -> None:
        """Leave these lights alone (and forget we were running them) until resumed."""
        for light in lights:
            self._release(light)
        self.paused.update(lights)

    @callback
    def resume(self, lights: list[str]) -> None:
        self.paused.difference_update(lights)

    def describe(self) -> list[dict[str, Any]]:
        out = []
        for rule in self.rules.values():
            st = self.hass.states.get(rule.light)
            out.append({
                "light": st.name if st else rule.light,
                "on": rule.enabled,
                "stays_off_in_quiet_hours": rule.respect_quiet,
                "quiet_start": rule.quiet_start if rule.respect_quiet else None,
                "quiet_end": rule.quiet_end if rule.respect_quiet else None,
                "only_after_dark": rule.only_dark,
                "off_after_minutes": rule.off_after,
                "late_night_brightness": rule.night_brightness or None,
                "last": rule.last,
            })
        return out

    @property
    def managed_lights(self) -> set[str]:
        return {r.light for r in self.rules.values()}

    # ----- running ----------------------------------------------------------------
    @callback
    def async_start(self) -> None:
        self._resubscribe()

    @callback
    def async_stop(self) -> None:
        self._clear_listeners()
        for unsub in self._timers.values():
            unsub()
        self._timers.clear()

    def _clear_listeners(self) -> None:
        for unsubscribe in self._listeners:
            unsubscribe()
        self._listeners.clear()

    @callback
    def _resubscribe(self) -> None:
        self._clear_listeners()
        watched = set()
        starts = set()
        for rule in self.rules.values():
            watched.update(rule.motion)
            watched.add(rule.light)
            if rule.respect_quiet and (window := rule.quiet_window()):
                starts.add(window[0])
        if watched:
            self._listeners.append(async_track_state_change_event(self.hass, sorted(watched), self._changed))
        for start in sorted(starts):
            self._listeners.append(async_track_time_change(
                self.hass, self._quiet_started, hour=start.hour, minute=start.minute, second=0,
            ))

    @callback
    def _quiet_started(self, _now: datetime) -> None:
        for rule in self.rules.values():
            self._off_during_quiet(rule)

    def _custom_quiet(self, rule: MotionRule) -> bool:
        window = rule.quiet_window() if rule.respect_quiet else None
        return bool(window and in_quiet_hours(dt_util.now(), *window))

    @callback
    def _off_during_quiet(self, rule: MotionRule) -> None:
        if not rule.enabled or rule.light not in self._ours or rule.light in self.paused or not self._custom_quiet(rule):
            return
        state = self.hass.states.get(rule.light)
        if state is None or state.state != STATE_ON:
            return
        if state.context.user_id is not None and state.context.id not in self._ours[rule.light]:
            self._release(rule.light)
            self._note(rule, "taken over by hand")
            return
        if self._turn(rule, False):
            self._note(rule, "off: this light's quiet hours")
        self._release(rule.light)

    def late_night(self, now: datetime | None = None) -> bool:
        return in_quiet_hours(
            now or dt_util.now(), _time(self.night_start, DEFAULT_NIGHT_START), _time(self.night_end, DEFAULT_NIGHT_END)
        )

    def _dark(self, rule: MotionRule | None = None) -> bool:
        if rule is not None and rule.lux_sensor:
            st = self.hass.states.get(rule.lux_sensor)
            try:
                return float(st.state) < float(rule.lux_below)  # type: ignore[union-attr]
            except (AttributeError, TypeError, ValueError):
                pass  # sensor missing or unknown: fall back to the sun
        sun = self.hass.states.get("sun.sun")
        if sun is not None:
            return sun.state == "below_horizon"
        return not is_up(self.hass)

    def _note(self, rule: MotionRule, what: str) -> None:
        now = dt_util.utcnow()
        repeat = what.startswith("stayed off") and rule.last == what and rule.last_time and \
            now - (dt_util.parse_datetime(rule.last_time) or now) < timedelta(minutes=30)
        rule.last, rule.last_time = what, now.isoformat()
        if repeat:  # "stayed off: quiet hours" every few minutes all night isn't news for the timeline
            self._save()
            return
        self._save()
        st = self.hass.states.get(rule.light)
        self.hass.bus.async_fire(
            EVENT_MOTION_LIGHT, {"light": rule.light, "name": st.name if st else rule.light, "what": what}
        )

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
        if event.context.id in self._ours[rule.light]:
            self._off_during_quiet(rule)
            return
        if event.context.user_id is not None:
            self._release(rule.light)
            self._note(rule, "taken over by hand")
            return
        old = event.data["old_state"]
        if old is None or old.state in ("unavailable", "unknown"):
            return  # the light reconnecting, not a person
        sent = self._sent.get(rule.light)
        if sent is not None and dt_util.utcnow() - sent < SETTLE:
            return  # the light reporting back after our own command (brightness, colour, a refresh)
        if old.state == STATE_ON:
            a, b = old.attributes.get("brightness"), new.attributes.get("brightness")
            if a is None or b is None or abs(int(a) - int(b)) < 26:
                return  # only colour, effect or a tiny brightness wobble changed: not a person
        # someone changed it: theirs now, so don't switch it off on them
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
        if rule.light in self.paused:
            return
        self._cancel_timer(rule.light)
        light = self.hass.states.get(rule.light)
        if light is None:
            return
        if self._custom_quiet(rule):
            if light.state == STATE_ON:
                self._off_during_quiet(rule)
            else:
                self._note(rule, "stayed off: this light's quiet hours")
            return
        if rule.light in self._ours:
            if light.state == STATE_ON:
                return  # already ours and on: just keep it on
            self._ours.pop(rule.light, None)
        if light.state == STATE_ON:
            return  # someone else's light
        if rule.only_dark and not self._dark(rule):
            return
        if rule.respect_quiet and rule.quiet_start is None and self.speech.quiet_hours_now():
            self._note(rule, "stayed off: quiet hours")
            return
        late = self.late_night() and rule.night_brightness > 0
        if self._turn(rule, True, rule.night_brightness if late else rule.brightness):
            self._note(rule, f"on at {rule.night_brightness}% (late night)" if late else "on with motion")

    @callback
    def _on_still(self, rule: MotionRule) -> None:
        if rule.light not in self._ours or rule.light in self.paused:
            return
        self._cancel_timer(rule.light)

        @callback
        def _off(_now: datetime) -> None:
            self._timers.pop(rule.light, None)
            if rule.light in self._ours and rule.light not in self.paused and not self._motion_now(rule):
                if self._turn(rule, False):
                    self._note(rule, "off: no movement")

        self._timers[rule.light] = async_call_later(self.hass, rule.off_after * 60, _off)

    @callback
    def _turn(self, rule: MotionRule, on: bool, brightness: int | None = None) -> bool:
        from .never import blocked

        if blocked(self.hass, rule.light):
            return False  # on the "never touch" list
        # Per-rule opt-in, reversible actions only. Require fresh, recent
        # dispatched guarded-voice intent; unsupported/unverified cases
        # behave exactly as before. This path never changes alarms or locks.
        intents = getattr(self, "intents", None)
        if on and rule.protect_voice_override and intents is not None:
            advisory = intents.preview(rule.light, "turn_on",
                                      "motion_lights")
            if advisory["decision"] == "recommend_hold" and rule.light.startswith(("light.", "switch.")):
                self._note(rule, "held: recent higher-priority command")
                return False
        from .own import own_context

        context = own_context(self.hass, "motion lights")
        self._ours.setdefault(rule.light, set()).add(context.id)
        self._sent[rule.light] = dt_util.utcnow()
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
        if (intents := getattr(self, "intents", None)) is not None:
            intents.observe(rule.light, "turn_on" if on else "turn_off", "motion_lights")
        return True

    # ----- for the page ------------------------------------------------------------------
    def listing(self) -> dict[str, Any]:
        rules = []
        for rule in self.rules.values():
            item = asdict(rule)
            item["managing"] = rule.light in self._ours
            item["timer"] = rule.light in self._timers
            item["quiet_now"] = rule.respect_quiet and (
                self._custom_quiet(rule) if rule.quiet_start is not None else self.speech.quiet_hours_now()
            )
            rules.append(item)
        return {
            "rules": rules,
            "night_start": self.night_start,
            "night_end": self.night_end,
            "dark": self._dark(),
            "quiet": self.speech.quiet_hours_now(),
            "late_night": self.late_night(),
        }
