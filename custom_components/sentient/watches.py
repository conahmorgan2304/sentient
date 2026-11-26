"""Watches: "keep an eye on X until Y and tell me if Z".

Two kinds, both event-driven:

* **state** — free. Fires when an entity reaches a state or crosses a number
  ("tell me when the washing machine finishes", "...if the bedroom goes above 24").
* **camera** — uses the configured AI task, but only when that camera's own
  motion/object sensors trigger, never on a timer, and with a cooldown.

Watches persist across restarts and expire on their own.
"""

from __future__ import annotations

import asyncio
from dataclasses import asdict, dataclass, field
from datetime import datetime, timedelta
import logging
import re
from typing import TYPE_CHECKING, Any
import uuid

from homeassistant.const import EVENT_STATE_CHANGED, STATE_ON, STATE_UNAVAILABLE, STATE_UNKNOWN
from homeassistant.core import Event, EventStateChangedData, HomeAssistant, State, callback
from homeassistant.exceptions import HomeAssistantError
from homeassistant.helpers import area_registry as ar
from homeassistant.helpers import entity_registry as er
from homeassistant.helpers.event import async_track_point_in_utc_time, async_track_time_interval
from homeassistant.helpers.storage import Store
from homeassistant.util import dt as dt_util

from .language import write_in_line
from .ai import async_generate
from .const import (
    CONF_AI_TASK_ENTITY,
    CONF_SECOND_OPINION,
    CONF_LOOK_CACHE,
    CONF_CAMERA_PREFILTER,
    DEFAULT_LOOK_CACHE,
    CONF_CAMERA_COOLDOWN,
    CONF_DAILY_AI_LIMIT,
    DEFAULT_CAMERA_COOLDOWN,
    DEFAULT_DAILY_AI_LIMIT,
    DOMAIN,
    EVENT_WATCH_CHANGED,
    EVENT_WATCH_FIRED,
    EVENT_WATCH_EXPIRED,
)

if TYPE_CHECKING:
    from .speech import SpeechRouter

_LOGGER = logging.getLogger(__name__)

STORAGE_VERSION = 1
KIND_STATE = "state"
KIND_CAMERA = "camera"
KIND_REMINDER = "reminder"
LATE_REMINDER = timedelta(minutes=15)  # still say a reminder missed during a restart
DEFAULT_STATE_HOURS = 12
DEFAULT_CAMERA_HOURS = 2
MAX_HOURS = 24 * 7
_TRIGGER_CLASSES = {"motion", "occupancy", "presence"}
PREFILTER_CHANGED = 0.06  # share of the picture that must look different before the AI is asked again
_IGNORED = {STATE_UNAVAILABLE, STATE_UNKNOWN}
CONF_WATCH_ACTIONS = "watch_actions"  # setting: watches and reminders can do things, not just say them


@dataclass
class Watch:
    """One thing being kept an eye on."""

    what: str
    kind: str
    entity_id: str
    until: str
    to_state: str | None = None
    above: float | None = None
    below: float | None = None
    question: str | None = None
    look_for: str | None = None
    message: str | None = None
    repeat: bool = False
    priority: str = "normal"
    speaker: str | None = None
    triggers: list[str] = field(default_factory=list)
    spot: str | None = None
    do: list[dict[str, Any]] = field(default_factory=list)  # actions to carry out when it fires ("watches can act")
    only_if: str | None = None  # ...and only if this is in only_if_state when it fires
    only_if_state: str | None = None
    hold_minutes: float | None = None  # the state must last this long ("open for 10 minutes")
    id: str = field(default_factory=lambda: uuid.uuid4().hex[:6])
    created: str = field(default_factory=lambda: dt_util.utcnow().isoformat())
    fired: int = 0
    checks: int = 0
    second_opinions: int = 0
    last_checked: str | None = None
    last_result: str | None = None
    delivery_retries: int = 0

    def as_dict(self) -> dict[str, Any]:
        return asdict(self)

    @property
    def until_dt(self) -> datetime:
        return dt_util.parse_datetime(self.until) or dt_util.utcnow()

    def summary(self) -> dict[str, Any]:
        """Friendly view for people and AI: local times, no internals."""
        return {
            "id": self.id,
            "what": self.what,
            "kind": self.kind,
            "until": local_when(self.until_dt),
            "minutes_left": max(0, round((self.until_dt - dt_util.utcnow()).total_seconds() / 60)),
            "times_fired": self.fired,
            "checks": self.checks,
            "second_opinions": self.second_opinions,
            "last_result": self.last_result,
            **({"then": self.do_words} if self.do else {}),
            **({"only_if": self.only_if_words or f"{self.only_if} is {self.only_if_state or 'on'}"} if self.only_if else {}),
            **({"for_minutes": self.hold_minutes} if self.hold_minutes else {}),
        }

    do_words: str = ""  # the actions in plain words, for the voice and the page
    only_if_words: str = ""  # the condition in plain words ("the back door is open")


_OBJECTS = {"car": "car", "cars": "car", "van": "van", "vans": "van", "person": "person", "people": "person",
            "someone": "person", "anyone": "person", "somebody": "person", "cat": "cat", "cats": "cat", "dog": "dog",
            "dogs": "dog", "fox": "fox", "foxes": "fox", "bike": "bike", "motorbike": "motorbike", "parcel": "parcel",
            "parcels": "parcel", "delivery": "delivery driver", "lorry": "lorry", "truck": "truck"}


def _object_in(text: str | None) -> str | None:
    """'watch for any more cars appearing' -> 'car' (what a camera should look for), or None."""
    for word in re.findall(r"[a-z]+", (text or "").lower()):
        if word in _OBJECTS:
            return _OBJECTS[word]
    return None


def _camera_near(hass: HomeAssistant, entity_id: str) -> str | None:
    """The one camera in the same area as *entity_id* (a drive sensor -> the drive camera), else None."""
    from homeassistant.helpers import device_registry as dr
    from homeassistant.helpers import entity_registry as er

    ents, devs = er.async_get(hass), dr.async_get(hass)

    def area(eid: str) -> str | None:
        entry = ents.async_get(eid)
        if entry is None:
            return None
        if entry.area_id:
            return entry.area_id
        device = devs.async_get(entry.device_id) if entry.device_id else None
        return device.area_id if device else None

    where = area(entity_id)
    if where is None:
        return None
    found = [st.entity_id for st in hass.states.async_all("camera") if area(st.entity_id) == where]
    return found[0] if len(found) == 1 else None


def local_when(when: datetime) -> str:
    """'5:18pm today', '7:30am tomorrow' or '9am on Friday 23 October', in local time.

    12-hour wording because voice models read it aloud more reliably than 17:18.
    """
    local = dt_util.as_local(when)
    hour = local.hour % 12 or 12
    clock = f"{hour}{'' if local.minute == 0 else f':{local.minute:02d}'}{'am' if local.hour < 12 else 'pm'}"
    days = (local.date() - dt_util.now().date()).days
    if days == 0:
        return f"{clock} today"
    if days == 1:
        return f"{clock} tomorrow"
    if days == -1:
        return f"{clock} yesterday"
    return f"{clock} on {local:%A} {local.day} {local:%B}"


def parse_until(value: str | None, minutes: float | None, default_hours: float) -> datetime:
    """'18:00', '6pm', an ISO time, or N minutes from now -> an aware UTC datetime."""
    now = dt_util.utcnow()
    if minutes:
        return now + timedelta(minutes=min(float(minutes), MAX_HOURS * 60))
    if value:
        text = value.strip().lower().replace(".", ":")
        parsed = dt_util.parse_datetime(text)
        if parsed:
            return dt_util.as_utc(parsed if parsed.tzinfo else parsed.replace(tzinfo=dt_util.get_default_time_zone()))
        m = re.fullmatch(r"(\d{1,2})(?::(\d{2}))?\s*(am|pm)?", text)
        if m:
            hour, minute, ampm = int(m.group(1)), int(m.group(2) or 0), m.group(3)
            if ampm == "pm" and hour < 12:
                hour += 12
            if ampm == "am" and hour == 12:
                hour = 0
            if hour < 24 and minute < 60:
                local_now = dt_util.now()
                target = local_now.replace(hour=hour, minute=minute, second=0, microsecond=0)
                if target <= local_now:
                    target += timedelta(days=1)
                return dt_util.as_utc(target)
    return now + timedelta(hours=default_hours)


def resolve_entity(hass: HomeAssistant, text: str, prefer_domain: str | None = None) -> str | None:
    """Entity id, friendly name or area name -> entity id."""
    text = (text or "").strip()
    if not text:
        return None
    if prefer_domain == "camera":
        from .security_voice import camera_named

        if camera := camera_named(hass, text):
            return camera
        # Watch creation still accepts an exact non-camera entity and resolves its nearby camera.
        return text if hass.states.get(text) is not None else None
    if hass.states.get(text) is not None:
        return text
    needle = text.lower().removeprefix("the ").strip()
    states = hass.states.async_all(prefer_domain) if prefer_domain else hass.states.async_all()
    exact = [s for s in states if (s.name or "").lower() == needle]
    if exact:
        return exact[0].entity_id
    words = needle.split()
    partial = [
        s for s in states
        if all(w in f"{(s.name or '').lower()} {s.entity_id}" for w in words)
    ]
    if partial:
        # shortest name is usually the main entity ("Garden" over "Garden HD stream")
        return min(partial, key=lambda s: len(s.name or s.entity_id)).entity_id
    if prefer_domain:
        areas = ar.async_get(hass)
        for area in areas.async_list_areas():
            if area.name.lower() == needle:
                ent_reg = er.async_get(hass)
                for entry in er.async_entries_for_area(ent_reg, area.id):
                    if entry.domain == prefer_domain:
                        return entry.entity_id
    return None


def camera_triggers(hass: HomeAssistant, camera: str, look_for: str | None) -> list[str]:
    """Motion/object sensors on the camera's own device; object-specific ones if they exist."""
    ent_reg = er.async_get(hass)
    entry = ent_reg.async_get(camera)
    if entry is None or entry.device_id is None:
        return []
    sensors = []
    for e in er.async_entries_for_device(ent_reg, entry.device_id):
        if e.domain != "binary_sensor" or e.disabled_by:
            continue
        state = hass.states.get(e.entity_id)
        device_class = (state.attributes.get("device_class") if state else None) or e.device_class or e.original_device_class
        if device_class in _TRIGGER_CLASSES:
            sensors.append(e.entity_id)
    if look_for:
        word = look_for.lower().rstrip("s")
        specific = [s for s in sensors if word in s.lower()]
        if specific:
            return specific
    motion = [
        s for s in sensors
        if ((st := hass.states.get(s)) is not None and st.attributes.get("device_class") == "motion")
    ]
    return motion or sensors


class WatchManager:
    """Owns all watches for a Sentient entry."""

    def __init__(self, hass: HomeAssistant, entry_id: str, options: dict[str, Any], speech: SpeechRouter) -> None:
        self.hass = hass
        self._looks: dict[tuple[str, str, str], tuple[datetime, dict[str, Any]]] = {}  # recent camera answers
        self._cache_generation = 0
        self._stopped = False
        self._prints: dict[str, list[int]] = {}  # check/watch -> thumbnail at its last AI look
        self.skipped = 0  # AI looks saved because the picture hadn't changed
        self.options = options
        self.speech = speech
        self._store: Store[dict[str, Any]] = Store(hass, STORAGE_VERSION, f"{DOMAIN}.{entry_id}.watches")
        self._watches: dict[str, Watch] = {}
        self._busy: set[str] = set()
        self._delivering: dict[str, Watch] = {}  # cancellable one-shot reminders in flight
        self._unsubs: list = []
        self._timers: dict[str, Any] = {}
        self._holds: dict[str, Any] = {}  # watch id -> timer waiting for a state to last
        self._held: set[str] = set()  # repeating watches that already fired for the current stretch
        self.spots: Any = None  # set by the runtime
        self.ai_day: str = dt_util.now().date().isoformat()
        self._ai_checks = 0
        self.gate: Any = None  # the runtime's AIGate: the meter and today's limit
        self.runtime_getter: Any = None  # set by the runtime, for the timeline

    # ----- lifecycle ---------------------------------------------------------------
    async def async_start(self) -> None:
        self._stopped = False
        data = await self._store.async_load() or {}
        for item in data.get("watches", []):
            try:
                watch = Watch(**item)
            except TypeError:
                continue
            self._watches[watch.id] = watch
        if data.get("ai_day") == self.ai_day:
            self._ai_checks = int(data.get("ai_checks", 0))
        now = dt_util.utcnow()
        for watch in [w for w in self._watches.values() if w.kind == KIND_REMINDER]:
            if watch.until_dt > now:
                self._schedule(watch)
            elif now - watch.until_dt < LATE_REMINDER:
                self.hass.async_create_task(self._async_fire(watch, None, None))
            else:
                self._watches.pop(watch.id, None)
        self._expire()
        for watch in [w for w in self._watches.values() if w.kind == KIND_STATE and w.hold_minutes]:
            if (st := self.hass.states.get(watch.entity_id)) is not None and self._state_met(watch, st):
                self._hold(watch)  # it was already so before the restart: count the time again from now
        self._unsubs.append(self.hass.bus.async_listen(EVENT_STATE_CHANGED, self._on_state_changed))
        self._unsubs.append(async_track_time_interval(self.hass, self._tick, timedelta(minutes=1)))

    @callback
    def async_stop(self) -> None:
        self._stopped = True
        self._delivering.clear()
        self.clear_camera_cache()
        while self._unsubs:
            self._unsubs.pop()()
        for unsub in self._timers.values():
            unsub()
        self._timers.clear()
        for unsub in self._holds.values():
            unsub()
        self._holds.clear()

    @callback
    def _save(self) -> None:
        self._store.async_delay_save(
            lambda: {
                "watches": [w.as_dict() for w in self._watches.values()],
                "ai_day": self.ai_day,
                "ai_checks": self._ai_checks,
            },
            2,
        )
        self.hass.bus.async_fire(EVENT_WATCH_CHANGED, {"count": len(self._watches)})

    @property
    def watches(self) -> list[Watch]:
        return sorted(self._watches.values(), key=lambda w: w.until)

    # ----- create / cancel ------------------------------------------------------------
    def add(
        self,
        what: str,
        entity: str,
        *,
        to_state: str | None = None,
        above: float | None = None,
        below: float | None = None,
        question: str | None = None,
        look_for: str | None = None,
        message: str | None = None,
        until: str | None = None,
        minutes: float | None = None,
        repeat: bool = False,
        priority: str = "normal",
        speaker: str | None = None,
        wake_on: str | list[str] | None = None,
        spot: str | None = None,
        do: list[dict[str, Any]] | None = None,
        only_if: str | None = None,
        only_if_state: str | None = None,
        hold_minutes: float | None = None,
    ) -> Watch:
        # something to look for, with nothing to wait for on a device, is a camera watch: "watch for cars by the
        # gate" named the gate, and the gate sensor was picked instead of the gate camera (21 Nov, three retries)
        is_camera = bool(question) or entity.startswith("camera.") or (
            bool(look_for) and to_state is None and above is None and below is None)
        entity_id = resolve_entity(self.hass, entity, "camera" if is_camera else None) or (
            resolve_entity(self.hass, entity) if is_camera else None
        )
        if entity_id is None:
            raise HomeAssistantError(f"I can't find anything called '{entity}'")
        # 23 Nov: "watch for any more cars appearing next to me" took three retries — first a sensor with nothing to
        # wait for, then a camera with no question. What to look for is in the request itself.
        seen = look_for or _object_in(what)
        if (not entity_id.startswith("camera.") and seen and to_state is None and above is None and below is None
                and (camera := _camera_near(self.hass, entity_id)) is not None):
            entity_id = camera
        kind = KIND_CAMERA if entity_id.startswith("camera.") else KIND_STATE
        if kind == KIND_CAMERA and not question and seen:
            question = f"Can you see a {seen.strip()}?"
        if kind == KIND_CAMERA and not question:
            raise HomeAssistantError("A camera watch needs a question, e.g. 'Are the cats near the rabbit pen?'")
        if kind == KIND_STATE and to_state is None and above is None and below is None:
            raise HomeAssistantError("Say what to wait for: a state, or a number to go above or below")
        triggers: list[str] = []
        if kind == KIND_CAMERA:
            if wake_on:
                for item in [wake_on] if isinstance(wake_on, str) else wake_on:
                    found = resolve_entity(self.hass, item, "binary_sensor")
                    if found is None or not found.startswith("binary_sensor."):
                        raise HomeAssistantError(f"I can't find a sensor called '{item}' to wake on")
                    triggers.append(found)
            else:
                triggers = camera_triggers(self.hass, entity_id, look_for)
        spot_key = None
        if kind == KIND_CAMERA and self.spots is not None:
            if spot:
                if self.spots.get(entity_id, spot) is None:
                    raise HomeAssistantError(f"There's no spot called '{spot}' on that camera yet")
                spot_key = spot
            else:
                spot_key = self.spots.find_in(entity_id, f"{what} {question or ''}")
        when = parse_until(until, minutes, DEFAULT_CAMERA_HOURS if kind == KIND_CAMERA else DEFAULT_STATE_HOURS)
        watch = Watch(
            what=" ".join(what.split())[:120] or entity_id,
            kind=kind,
            entity_id=entity_id,
            until=when.isoformat(),
            to_state=to_state,
            above=above,
            below=below,
            question=question,
            look_for=look_for,
            message=message,
            repeat=repeat,
            priority=priority,
            speaker=speaker,
            triggers=triggers,
            spot=spot_key,
            do=list(do or []),
            only_if=only_if,
            only_if_state=only_if_state,
            hold_minutes=hold_minutes if kind == KIND_STATE and hold_minutes and hold_minutes > 0 else None,
        )
        self._words(watch)
        self._watches[watch.id] = watch
        self._save()
        if watch.hold_minutes and (st := self.hass.states.get(entity_id)) is not None and self._state_met(watch, st):
            self._hold(watch)  # already so: "if the back door's open for ten minutes" counts from now
        return watch

    def add_reminder(
        self,
        message: str,
        *,
        until: str | None = None,
        minutes: float | None = None,
        speaker: str | None = None,
        priority: str = "urgent",
        do: list[dict[str, Any]] | None = None,
        only_if: str | None = None,
        only_if_state: str | None = None,
    ) -> Watch:
        """Say something at a time. People asked for it, so by default it gets through quiet hours."""
        message = " ".join((message or "").split())
        if not message and not do:
            raise HomeAssistantError("What should I remind you about?")
        if not until and not minutes:
            raise HomeAssistantError("When should I remind you?")
        when = parse_until(until, minutes, 1)
        watch = Watch(
            what=(message or "a check")[:120], kind=KIND_REMINDER, entity_id="", until=when.isoformat(),
            message=message or None, priority=priority, speaker=speaker,
            do=list(do or []), only_if=only_if, only_if_state=only_if_state,
        )
        self._words(watch)
        self._watches[watch.id] = watch
        self._schedule(watch)
        self._save()
        return watch

    def _schedule(self, watch: Watch) -> None:
        @callback
        def _due(_now: datetime) -> None:
            self._timers.pop(watch.id, None)
            self.hass.async_create_task(self._async_fire(watch, None, None))

        self._timers[watch.id] = async_track_point_in_utc_time(self.hass, _due, watch.until_dt)

    def cancel(self, id_or_words: str) -> Watch | None:
        watch = self._watches.get(id_or_words) or self._delivering.get(id_or_words)
        if watch is None:
            words = set(re.findall(r"[a-z0-9]+", id_or_words.lower())) - {"the", "a", "watch", "watching", "on", "for"}
            best, score = None, 0
            for w in (*self._watches.values(), *self._delivering.values()):
                hay = set(re.findall(r"[a-z0-9]+", f"{w.what} {w.entity_id} {w.look_for or ''}".lower()))
                s = len(words & hay)
                if s > score:
                    best, score = w, s
            watch = best
        if watch is not None:
            self._watches.pop(watch.id, None)
            self._delivering.pop(watch.id, None)
            if (unsub := self._timers.pop(watch.id, None)) is not None:
                unsub()
            if (unsub := self._holds.pop(watch.id, None)) is not None:
                unsub()
            self._save()
        return watch

    @callback
    def _tick(self, _now: datetime) -> None:
        self._expire()

    def _expire(self) -> None:
        now = dt_util.utcnow()
        gone = [w for w in self._watches.values() if w.until_dt <= now and w.kind != KIND_REMINDER]
        for w in gone:
            self._watches.pop(w.id, None)
            if (unsub := self._holds.pop(w.id, None)) is not None:
                unsub()
            _LOGGER.debug("Watch expired: %s", w.what)
            if not w.fired and now - w.until_dt < timedelta(minutes=30):  # not one long gone over a restart
                self.hass.bus.async_fire(EVENT_WATCH_EXPIRED, {"id": w.id, "what": w.what, "entity_id": w.entity_id})
        if gone:
            self._save()

    # ----- reacting ------------------------------------------------------------------
    @callback
    def _on_state_changed(self, event: Event[EventStateChangedData]) -> None:
        if not self._watches:
            return
        new = event.data["new_state"]
        if new is None or new.state in _IGNORED:
            return
        old = event.data["old_state"]
        entity_id = new.entity_id
        for watch in list(self._watches.values()):
            if watch.kind == KIND_STATE and watch.entity_id == entity_id:
                met = self._state_met(watch, new)
                if watch.hold_minutes:
                    if not met:
                        self._held.discard(watch.id)  # a new stretch can fire again (repeating watches)
                        if (unsub := self._holds.pop(watch.id, None)) is not None:
                            unsub()  # it changed back before the time was up
                    elif watch.id not in self._holds and watch.id not in self._held:
                        self._hold(watch)
                elif met and not (old is not None and self._state_met(watch, old)):
                    self.hass.async_create_task(self._async_fire(watch, None, new))
            elif watch.kind == KIND_CAMERA and entity_id in watch.triggers and new.state == STATE_ON:
                if old is None or old.state != STATE_ON:
                    self.hass.async_create_task(self._async_check_camera(watch))

    @staticmethod
    def _state_met(watch: Watch, state: State) -> bool:
        if state.state in _IGNORED:
            return False
        if watch.to_state is not None:
            return state.state.lower() == watch.to_state.lower()
        from .numeric import finite_float

        value = finite_float(state.state)
        if value is None:
            return False
        if watch.above is not None and value <= watch.above:
            return False
        if watch.below is not None and value >= watch.below:
            return False
        return True

    def _hold(self, watch: Watch) -> None:
        @callback
        def _held(_now: datetime) -> None:
            self._holds.pop(watch.id, None)
            state = self.hass.states.get(watch.entity_id)
            if state is not None and self._state_met(watch, state):
                self._held.add(watch.id)  # once per stretch, however often the device reports in meanwhile
                self.hass.async_create_task(self._async_fire(watch, None, state))

        if (unsub := self._holds.pop(watch.id, None)) is not None:
            unsub()
        self._holds[watch.id] = async_track_point_in_utc_time(
            self.hass, _held, dt_util.utcnow() + timedelta(minutes=float(watch.hold_minutes or 0)))

    def _words(self, watch: Watch) -> None:
        from .acting import state_phrase, thing, words

        if watch.do:
            watch.do_words = words(self.hass, watch.do)
        if watch.only_if:
            watch.only_if_words = f"{thing(self.hass, watch.only_if)} is {state_phrase(self.hass, watch.only_if, watch.only_if_state or 'on')}"

    def _condition_met(self, watch: Watch) -> bool:
        if not watch.only_if:
            return True
        from .rules import state_word

        state = self.hass.states.get(watch.only_if)
        if state is None or state.state in _IGNORED:
            return False
        want = state_word(watch.only_if_state or "on")
        return state.state.lower() == want.lower()

    @property
    def runtime(self) -> Any:
        try:
            return self.runtime_getter() if self.runtime_getter is not None else None
        except AttributeError:  # not finished starting
            return None

    @property
    def acting(self) -> bool:
        return bool(self.options.get(CONF_WATCH_ACTIONS, False))

    async def _async_act(self, watch: Watch) -> str:
        """Carry out what the watch was told to do; the sentence that says how it went."""
        if not watch.do:
            return ""
        if not self.acting:
            return "I didn't do the rest, because watches that act are switched off in Settings."
        from .acting import async_carry_out

        generation, options = self._cache_generation, self.options
        privacy = getattr(self.speech, "_privacy_generation", 0)
        def valid():
            return (not self._stopped and self.acting and self.options is options
                    and generation == self._cache_generation
                    and privacy == getattr(self.speech, "_privacy_generation", 0))
        outcome = await async_carry_out(self.hass, watch.do, f"watch: {watch.what}", before_run=valid)
        if (rt := getattr(self, "runtime", None)) is not None and getattr(rt, "timeline", None) is not None:
            rt.timeline.add("watch", f"Watch acted: {watch.what}", outcome["said"])
        return outcome["said"]

    def _cooldown(self) -> timedelta:
        return timedelta(seconds=float(self.options.get(CONF_CAMERA_COOLDOWN, DEFAULT_CAMERA_COOLDOWN)))

    async def _async_check_camera(self, watch: Watch) -> None:
        if self._stopped or watch.id in self._busy or self._watches.get(watch.id) is not watch:
            return
        if watch.until_dt <= dt_util.utcnow():
            self._expire()
            return
        last = dt_util.parse_datetime(watch.last_checked) if watch.last_checked else None
        if last and dt_util.utcnow() - last < self._cooldown():
            return
        generation, options = self._cache_generation, self.options
        def current():
            return (generation == self._cache_generation and self.options is options
                    and self._watches.get(watch.id) is watch and watch.until_dt > dt_util.utcnow())
        prefilter_key = f"watch:{watch.id}"
        evaluated = False
        self._busy.add(watch.id)
        try:
            if self._budget_blocked():
                watch.last_result = "skipped: daily AI limit reached"
                self._save()
                return
            # give the camera a moment so the subject is in frame
            await asyncio.sleep(1)
            if not current():
                return
            same = await self.unchanged(prefilter_key, watch.entity_id, watch.spot)
            if not current():
                return
            if same:
                evaluated = True
                watch.last_result = "skipped: the picture hasn't changed"
                self._save()
                return
            attachment = await self._attachment(watch.entity_id, watch.spot)
            if not current():
                return
            if not self.spend("watches"):
                watch.last_result = "skipped: daily AI limit reached"
                self._save()
                return
            watch.last_checked = dt_util.utcnow().isoformat()
            watch.checks += 1
            watch.last_result = "unknown: no current result"
            self._save()
            answer, description, unsure = await self._async_ask(watch, None, attachment)
            if not current():
                return
            second = self.options.get(CONF_SECOND_OPINION)
            if unsure and second and second != self.options.get(CONF_AI_TASK_ENTITY) and self.spend("watches"):
                watch.second_opinions += 1
                answer, description, unsure = await self._async_ask(watch, second, attachment)
            if not current():
                return
            if unsure:
                watch.last_result = "unknown: the picture is unclear"
                self._save()
                return
            evaluated = True
            watch.last_result = description or ("yes" if answer else "no")
            self._save()
            if answer:
                await self._async_fire(watch, description, None)
        except HomeAssistantError as err:
            if not current():
                return
            _LOGGER.warning("Camera watch '%s' could not check: %s", watch.what, err)
            watch.last_result = f"error: {err}"
            self._save()
        finally:
            if not evaluated:
                self._prints.pop(prefilter_key, None)
            self._busy.discard(watch.id)

    def _budget_blocked(self) -> bool:
        if self.gate is not None:
            return self.gate.capped or self.gate.over_budget("watches")
        if dt_util.now().date().isoformat() != self.ai_day:
            return False
        return self._ai_checks >= int(self.options.get(CONF_DAILY_AI_LIMIT, DEFAULT_DAILY_AI_LIMIT))

    async def _async_ask(self, watch: Watch, ai_entity: str | None = None, attachment: dict | None = None) -> tuple[bool, str | None, bool]:
        generation, options = self._cache_generation, self.options
        data: dict[str, Any] = {
            "task_name": f"Sentient watch: {watch.what}"[:100],
            "instructions": (
                f"You are checking a home camera image. Question: {watch.question} "
                "Answer true only if the image clearly shows it. If true, add one short, natural "
                "sentence suitable for speaking aloud, without camera names, IDs or times."
            ),
            "structure": {
                "answer": {"description": "True if the image clearly shows it", "required": True,
                           "selector": {"boolean": {}}},
                "description": {"description": "One short spoken sentence, or empty", "required": False,
                                "selector": {"text": {}}},
                "unsure": {"description": "True if the picture is too dark, blurry or ambiguous to be sure",
                           "required": False, "selector": {"boolean": {}}},
            },
            "attachments": [attachment if attachment is not None else await self._attachment(watch.entity_id, watch.spot)],
        }
        if (generation != self._cache_generation or self.options is not options
                or self._watches.get(watch.id) is not watch or watch.until_dt <= dt_util.utcnow()):
            raise HomeAssistantError('Camera watch changed while preparing; no AI request was sent')
        if ai_entity := ai_entity or self.options.get(CONF_AI_TASK_ENTITY):
            data["entity_id"] = ai_entity
        data["instructions"] += write_in_line(self.hass, self.options, spoken=True)
        response = await async_generate(self.hass, data)
        result = response.get("data") if isinstance(response, dict) else None
        if isinstance(result, str):  # tolerate only an unambiguous legacy yes/no, never arbitrary prose
            word = result.strip().lower().rstrip(".! ")
            if word in {"yes", "true", "no", "false"}:
                return word in {"yes", "true"}, None, False
        if (not isinstance(result, dict) or type(result.get("answer")) is not bool
                or type(result.get("unsure", False)) is not bool
                or not isinstance(result.get("description", ""), (str, type(None)))):
            raise HomeAssistantError("Invalid camera AI result; nothing was announced")
        return result["answer"], (result.get("description") or "").strip() or None, result.get("unsure", False)

    @property
    def ai_checks_today(self) -> int:
        return self.gate.used if self.gate is not None else self._ai_checks

    def spend(self, purpose: str = "camera", force: bool = False) -> bool:
        """Count one background AI check; False once today's limit is used up (never when forced)."""
        if self.gate is not None:
            return self.gate.spend(purpose, force)
        today = dt_util.now().date().isoformat()
        if today != self.ai_day:
            self.ai_day, self._ai_checks = today, 0
        limit = int(self.options.get(CONF_DAILY_AI_LIMIT, DEFAULT_DAILY_AI_LIMIT))
        if self._ai_checks >= limit:
            return False
        self._ai_checks += 1
        return True

    def clear_camera_cache(self):
        self._cache_generation += 1
        self._looks.clear()
        self._prints.clear()

    async def unchanged(self, key: str, camera: str, spot: str | None) -> bool:
        """The picture (or the spot) looks just as it did at this check's last AI look — no need to ask again.

        A quick local comparison of tiny thumbnails, ignoring overall brightness. Off in the settings, or
        no picture to compare: always look.
        """
        if not self.options.get(CONF_CAMERA_PREFILTER, True) or self.spots is None:
            return False
        from .spots import _key, changed_share

        generation, options = self._cache_generation, self.options
        fingerprint = await self.spots.async_fingerprint(camera, _key(spot) if spot else None, whole=True)
        if generation != self._cache_generation or self.options is not options:
            return False
        if fingerprint is None:
            return False
        before = self._prints.get(key)
        if before is not None and changed_share(fingerprint, before) < PREFILTER_CHANGED:
            self.skipped += 1
            return True
        self._prints.pop(key, None)
        self._prints[key] = fingerprint
        while len(self._prints) > 200:
            self._prints.pop(next(iter(self._prints)))
        return False

    async def _attachment(self, camera: str, spot: str | None) -> dict[str, str]:
        if self.spots is None:
            from .camwake import async_wake

            await async_wake(self.hass, self.options, camera)
            return {"media_content_id": f"media-source://camera/{camera}", "media_content_type": "image/jpeg"}
        key = None
        if spot:
            from .spots import _key

            key = _key(spot)
        return await self.spots.async_attachment(camera, key)

    async def async_look(self, camera: str, question: str, spot: str | None = None) -> dict[str, Any]:
        """One on-demand look through a camera: 'what's going on in the garden?'.

        A spot named in the question (or by another name for it — "the drive", "the driveway")
        decides the camera, whatever camera was asked for: it's the one that can see it.
        """
        generation, options = self._cache_generation, self.options
        spot_key = None
        if self.spots is not None:
            spot_key = self.spots.find_any(spot) if spot else None
            spot_key = spot_key or self.spots.find_any(question) or self.spots.find_any(camera)
        if spot_key:
            entity_id = self.spots.spots[spot_key]["camera"]
            spot = self.spots.spots[spot_key]["name"]
        else:
            entity_id = resolve_entity(self.hass, camera, "camera") or resolve_entity(self.hass, camera)
        if entity_id is None or not entity_id.startswith("camera."):
            raise HomeAssistantError(f"I can't find a camera called '{camera}'")
        key = (entity_id, spot or "", " ".join(question.lower().split()).strip(" ?.!"))
        minutes = float(self.options.get(CONF_LOOK_CACHE, DEFAULT_LOOK_CACHE) or 0)
        if minutes > 0 and (hit := self._looks.get(key)) and dt_util.utcnow() - hit[0] < timedelta(minutes=minutes):
            return {**hit[1], "seen_minutes_ago": round((dt_util.utcnow() - hit[0]).total_seconds() / 60, 1)}
        data: dict[str, Any] = {
            "task_name": f"Sentient look: {question}"[:100],
            "instructions": (
                f"Look at this home camera image and answer: {question} "
                "Reply in one or two short spoken sentences. Describe only what is visible; "
                "if it's too dark or unclear, say so. No camera names, IDs or timestamps."
            ),
            "attachments": [
                await self._attachment(
                    entity_id, spot or (self.spots.find_in(entity_id, question) if self.spots else None)
                )
            ],
        }
        if ai_entity := self.options.get(CONF_AI_TASK_ENTITY):
            data["entity_id"] = ai_entity
        if generation != self._cache_generation or self.options is not options:
            raise HomeAssistantError('Camera request changed while preparing; please ask again')
        self.spend("question", force=True)  # someone asked, so always look; it still counts towards today's total
        self._save()
        response = await async_generate(self.hass, data)
        if generation != self._cache_generation or self.options is not options:
            raise HomeAssistantError('Camera request was invalidated; please ask again')
        state = self.hass.states.get(entity_id)
        if state is None:
            raise HomeAssistantError('The camera is no longer available')
        answer = (response or {}).get("data")
        out = {"camera": state.name, "answer": str(answer or "").strip()}
        if spot:
            out["spot"] = spot
        if out["answer"]:
            now = dt_util.utcnow()
            self._looks = {k: v for k, v in self._looks.items() if now - v[0] < timedelta(minutes=30)}
            self._looks[key] = (now, out)
        return out

    async def _async_fire(self, watch: Watch, description: str | None, state: State | None) -> None:
        if self._stopped or self._watches.get(watch.id) is not watch:
            return
        if watch.kind != KIND_REMINDER and watch.until_dt <= dt_util.utcnow():
            self._expire()
            return
        if watch.only_if and not self._condition_met(watch):
            # "at 11, if the back door's still open…": it isn't, so there's nothing to say or do
            if not watch.repeat or watch.kind == KIND_REMINDER:
                self._watches.pop(watch.id, None)
                self._save()
            if (rt := getattr(self, "runtime", None)) is not None and getattr(rt, "timeline", None) is not None:
                rt.timeline.add("watch", f"Checked: {watch.what}", "the condition wasn't met, so nothing was done")
            return
        watch.fired += 1
        if not watch.repeat or watch.kind == KIND_REMINDER:
            self._watches.pop(watch.id, None)
            self._held.discard(watch.id)
        self._save()
        acted = await self._async_act(watch) if watch.do else ""
        if watch.do and not watch.message and not description:
            message = acted  # what it did is the news
        else:
            message = watch.message or description or self._default_message(watch, state)
            if acted:
                message = f"{message.rstrip()} {acted}".strip()
        if not message:
            return
        targets = None
        if watch.speaker:
            found = self.speech.find_speaker(watch.speaker)
            targets = [found] if found else None
        self.hass.bus.async_fire(
            EVENT_WATCH_FIRED, {"id": watch.id, "what": watch.what, "message": message, "entity_id": watch.entity_id,
                                "kind": watch.kind}
        )
        kind = "reminder" if watch.kind == KIND_REMINDER else "watch"
        pure_reminder = watch.kind == KIND_REMINDER and not watch.do
        if pure_reminder:
            self._delivering[watch.id] = watch
        try:
            result = await self.speech.async_say(message, watch.priority, targets, source=f"{kind}:{watch.what}")
        finally:
            still_current = self._delivering.pop(watch.id, None) is watch
        if (pure_reminder and still_current and not self._stopped
                and result is not None and not result.spoken and watch.delivery_retries < 2
                and (result.reason == "no_speaker" or (
                    result.reason == "delivery_failed" and result.deliveries
                    and all(not r.get("dispatched", True) for r in result.deliveries)))):
            # Retry only a pure reminder that was definitely not dispatched.
            # Never replay an associated action or an uncertain speaker request.
            watch.delivery_retries += 1
            watch.until = (dt_util.utcnow() + timedelta(minutes=2)).isoformat()
            self._watches[watch.id] = watch
            self._schedule(watch)
            self._save()

    def _default_message(self, watch: Watch, state: State | None) -> str:
        if state is not None:
            unit = state.attributes.get("unit_of_measurement") or ""
            return f"{state.name} is now {state.state}{(' ' + unit) if unit else ''}."
        return f"Heads up: {watch.what}."
