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

from .const import (
    CONF_AI_TASK_ENTITY,
    CONF_CAMERA_COOLDOWN,
    CONF_DAILY_AI_LIMIT,
    DEFAULT_CAMERA_COOLDOWN,
    DEFAULT_DAILY_AI_LIMIT,
    DOMAIN,
    EVENT_WATCH_CHANGED,
    EVENT_WATCH_FIRED,
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
_IGNORED = {STATE_UNAVAILABLE, STATE_UNKNOWN}


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
    id: str = field(default_factory=lambda: uuid.uuid4().hex[:6])
    created: str = field(default_factory=lambda: dt_util.utcnow().isoformat())
    fired: int = 0
    checks: int = 0
    last_checked: str | None = None
    last_result: str | None = None

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
            "last_result": self.last_result,
        }


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
        self.options = options
        self.speech = speech
        self._store: Store[dict[str, Any]] = Store(hass, STORAGE_VERSION, f"{DOMAIN}.{entry_id}.watches")
        self._watches: dict[str, Watch] = {}
        self._busy: set[str] = set()
        self._unsubs: list = []
        self._timers: dict[str, Any] = {}
        self.spots: Any = None  # set by the runtime
        self.ai_day: str = dt_util.now().date().isoformat()
        self.ai_checks_today = 0

    # ----- lifecycle ---------------------------------------------------------------
    async def async_start(self) -> None:
        data = await self._store.async_load() or {}
        for item in data.get("watches", []):
            try:
                watch = Watch(**item)
            except TypeError:
                continue
            self._watches[watch.id] = watch
        if data.get("ai_day") == self.ai_day:
            self.ai_checks_today = int(data.get("ai_checks", 0))
        now = dt_util.utcnow()
        for watch in [w for w in self._watches.values() if w.kind == KIND_REMINDER]:
            if watch.until_dt > now:
                self._schedule(watch)
            elif now - watch.until_dt < LATE_REMINDER:
                self.hass.async_create_task(self._async_fire(watch, None, None))
            else:
                self._watches.pop(watch.id, None)
        self._expire()
        self._unsubs.append(self.hass.bus.async_listen(EVENT_STATE_CHANGED, self._on_state_changed))
        self._unsubs.append(async_track_time_interval(self.hass, self._tick, timedelta(minutes=1)))

    @callback
    def async_stop(self) -> None:
        while self._unsubs:
            self._unsubs.pop()()
        for unsub in self._timers.values():
            unsub()
        self._timers.clear()

    @callback
    def _save(self) -> None:
        self._store.async_delay_save(
            lambda: {
                "watches": [w.as_dict() for w in self._watches.values()],
                "ai_day": self.ai_day,
                "ai_checks": self.ai_checks_today,
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
    ) -> Watch:
        is_camera = bool(question) or entity.startswith("camera.")
        entity_id = resolve_entity(self.hass, entity, "camera" if is_camera else None) or (
            resolve_entity(self.hass, entity) if is_camera else None
        )
        if entity_id is None:
            raise HomeAssistantError(f"I can't find anything called '{entity}'")
        kind = KIND_CAMERA if entity_id.startswith("camera.") else KIND_STATE
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
        )
        self._watches[watch.id] = watch
        self._save()
        return watch

    def add_reminder(
        self,
        message: str,
        *,
        until: str | None = None,
        minutes: float | None = None,
        speaker: str | None = None,
        priority: str = "urgent",
    ) -> Watch:
        """Say something at a time. People asked for it, so by default it gets through quiet hours."""
        message = " ".join(message.split())
        if not message:
            raise HomeAssistantError("What should I remind you about?")
        if not until and not minutes:
            raise HomeAssistantError("When should I remind you?")
        when = parse_until(until, minutes, 1)
        watch = Watch(
            what=message[:120], kind=KIND_REMINDER, entity_id="", until=when.isoformat(),
            message=message, priority=priority, speaker=speaker,
        )
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
        watch = self._watches.get(id_or_words)
        if watch is None:
            words = set(re.findall(r"[a-z0-9]+", id_or_words.lower())) - {"the", "a", "watch", "watching", "on", "for"}
            best, score = None, 0
            for w in self._watches.values():
                hay = set(re.findall(r"[a-z0-9]+", f"{w.what} {w.entity_id} {w.look_for or ''}".lower()))
                s = len(words & hay)
                if s > score:
                    best, score = w, s
            watch = best
        if watch is not None:
            self._watches.pop(watch.id, None)
            if (unsub := self._timers.pop(watch.id, None)) is not None:
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
            _LOGGER.debug("Watch expired: %s", w.what)
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
                if self._state_met(watch, new) and not (old is not None and self._state_met(watch, old)):
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
        try:
            value = float(state.state)
        except ValueError:
            return False
        if watch.above is not None and value <= watch.above:
            return False
        if watch.below is not None and value >= watch.below:
            return False
        return True

    def _cooldown(self) -> timedelta:
        return timedelta(seconds=float(self.options.get(CONF_CAMERA_COOLDOWN, DEFAULT_CAMERA_COOLDOWN)))

    async def _async_check_camera(self, watch: Watch) -> None:
        if watch.id in self._busy or watch.id not in self._watches:
            return
        if watch.until_dt <= dt_util.utcnow():
            self._expire()
            return
        last = dt_util.parse_datetime(watch.last_checked) if watch.last_checked else None
        if last and dt_util.utcnow() - last < self._cooldown():
            return
        if not self.spend():
            watch.last_result = "skipped: daily AI limit reached"
            self._save()
            return
        self._busy.add(watch.id)
        try:
            # give the camera a moment so the subject is in frame
            await asyncio.sleep(1)
            watch.last_checked = dt_util.utcnow().isoformat()
            watch.checks += 1
            answer, description = await self._async_ask(watch)
            watch.last_result = description or ("yes" if answer else "no")
            self._save()
            if answer:
                await self._async_fire(watch, description, None)
        except HomeAssistantError as err:
            _LOGGER.warning("Camera watch '%s' could not check: %s", watch.what, err)
            watch.last_result = f"error: {err}"
            self._save()
        finally:
            self._busy.discard(watch.id)

    async def _async_ask(self, watch: Watch) -> tuple[bool, str | None]:
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
            },
            "attachments": [await self._attachment(watch.entity_id, watch.spot)],
        }
        if ai_entity := self.options.get(CONF_AI_TASK_ENTITY):
            data["entity_id"] = ai_entity
        response = await self.hass.services.async_call(
            "ai_task", "generate_data", data, blocking=True, return_response=True
        )
        result = (response or {}).get("data") or {}
        if isinstance(result, str):  # model ignored the structure
            return result.strip().lower().startswith(("yes", "true")), None
        answer = result.get("answer")
        if isinstance(answer, str):
            answer = answer.strip().lower() in {"true", "yes"}
        return bool(answer), (result.get("description") or "").strip() or None

    def spend(self) -> bool:
        """Count one background AI check; False once today's limit is used up."""
        today = dt_util.now().date().isoformat()
        if today != self.ai_day:
            self.ai_day, self.ai_checks_today = today, 0
        limit = int(self.options.get(CONF_DAILY_AI_LIMIT, DEFAULT_DAILY_AI_LIMIT))
        if self.ai_checks_today >= limit:
            return False
        self.ai_checks_today += 1
        return True

    async def _attachment(self, camera: str, spot: str | None) -> dict[str, str]:
        if self.spots is None:
            return {"media_content_id": f"media-source://camera/{camera}", "media_content_type": "image/jpeg"}
        key = None
        if spot:
            from .spots import _key

            key = _key(spot)
        return await self.spots.async_attachment(camera, key)

    async def async_look(self, camera: str, question: str, spot: str | None = None) -> dict[str, Any]:
        """One on-demand look through a camera: 'what's going on in the garden?'."""
        entity_id = resolve_entity(self.hass, camera, "camera") or resolve_entity(self.hass, camera)
        if entity_id is None or not entity_id.startswith("camera."):
            raise HomeAssistantError(f"I can't find a camera called '{camera}'")
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
        self.spend()  # someone asked, so always look; it still counts towards today's total
        self._save()
        response = await self.hass.services.async_call(
            "ai_task", "generate_data", data, blocking=True, return_response=True
        )
        answer = (response or {}).get("data")
        return {"camera": self.hass.states.get(entity_id).name, "answer": str(answer or "").strip()}

    async def _async_fire(self, watch: Watch, description: str | None, state: State | None) -> None:
        if watch.id not in self._watches:
            return
        watch.fired += 1
        if not watch.repeat:
            self._watches.pop(watch.id, None)
        self._save()
        message = watch.message or description or self._default_message(watch, state)
        targets = None
        if watch.speaker:
            found = self.speech.find_speaker(watch.speaker)
            targets = [found] if found else None
        self.hass.bus.async_fire(
            EVENT_WATCH_FIRED, {"id": watch.id, "what": watch.what, "message": message, "entity_id": watch.entity_id}
        )
        await self.speech.async_say(message, watch.priority, targets, source=f"watch:{watch.what}")

    def _default_message(self, watch: Watch, state: State | None) -> str:
        if state is not None:
            unit = state.attributes.get("unit_of_measurement") or ""
            return f"{state.name} is now {state.state}{(' ' + unit) if unit else ''}."
        return f"Heads up: {watch.what}."
