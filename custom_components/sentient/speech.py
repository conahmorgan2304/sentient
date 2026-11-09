"""Speech routing for Sentient.

One place decides *whether*, *where* and *how* the house speaks:

* quiet hours (urgent messages still get through)
* words the household doesn't want used ("sir", names, ...)
* duplicate suppression
* a short hush after chosen automations/scripts (e.g. the doorbell flow), so a
  background remark doesn't talk over a more important announcement
* the speaker in the busiest room (see presence.py), otherwise the default
"""

from __future__ import annotations

import asyncio
from collections import deque
from dataclasses import dataclass, field
from datetime import datetime, time, timedelta
import logging
import math
from time import monotonic
import re
from collections.abc import Awaitable, Callable
from typing import Any

from homeassistant.core import HomeAssistant, callback
from homeassistant.exceptions import HomeAssistantError
from homeassistant.helpers import area_registry as ar, entity_registry as er
from homeassistant.helpers.storage import Store
from homeassistant.util import dt as dt_util

from .const import CONF_ALEXA_TTS_ONLY, CONF_READ_AHEAD, CONF_READING_TTS, CONF_READING_VOICE
from .const import CONF_REMARK_REPEAT_HOURS, DEFAULT_REMARK_REPEAT_HOURS, DOMAIN
from .const import CONF_SPEECH_DELIVERY_HISTORY, CONF_PERSONA, DEFAULT_PERSONA
from .const import (
    CONF_FALLBACK_SPEAKERS,
    CONF_ALL_OCCUPIED,
    DEFAULT_ALL_OCCUPIED,
    CONF_BANNED_WORDS,
    CONF_DEFAULT_SPEAKER,
    CONF_QUIET_END,
    CONF_BUSY_ENTITIES,
    CONF_QUIET_START,
    CONF_SPEAKERS,
    CONF_SUPPRESS_ENTITIES,
    CONF_SUPPRESS_SECONDS,
    DEFAULT_BANNED_WORDS,
    DEFAULT_QUIET_END,
    DEFAULT_QUIET_START,
    DEFAULT_SUPPRESS_SECONDS,
    EVENT_HELD,
    EVENT_SPOKE,
    CONF_CHIME,
    CONF_SPEAK_VOLUME,
    CONF_NIGHT_VOLUME,
    CONF_DUCK_MEDIA,
    CONF_ROOM_VOLUME,
    CONF_FULL_VOLUME_SPEAKERS,
    CONF_TTS_ENTITY,
    CONF_MEDIA_SPEAKERS,
    CONF_MEDIA_IMPORTANT_ONLY,
    DEFAULT_CHIME,
    PRIORITY_LOW,
    PRIORITY_URGENT,
)
from .delivery import RoomLevels, delivered, playing_nearby
from .language import async_in_house_language, selected_language
from .reasons import reason_for
from .presence import ActivityTracker, area_of

_LOGGER = logging.getLogger(__name__)

_DUPLICATE_WINDOW = timedelta(seconds=120)
DELIVERY_TIMEOUT = 60
_BUSY_STATES = {"listening", "processing", "responding"}
_START_CONVERSATION = 2  # AssistSatelliteEntityFeature.START_CONVERSATION
# what still goes to the Echos when they're kept for important things only
IMPORTANT_SOURCES = ("alarm:", "alarm test", "reminder:", "critical", "alexa:")
# answers to someone who just asked, and the briefing for someone who's just got up (in their room only):
# never held for quiet hours
REPLY_SOURCES = ("alexa:", "briefing:up")


def important(source: str | None) -> bool:
    return str(source or "").startswith(IMPORTANT_SOURCES)


INSTANT_SECONDS = 2.0  # an announcement handed back faster than this hasn't been spoken yet
QUESTION_OPEN = timedelta(seconds=90)  # a reply this soon after the house asked is an answer to it
READING_WORDS_PER_SECOND = 2.5  # a steady reading voice, about 150 words a minute


def reading_seconds(text: str) -> float:
    """Roughly how long a text-to-speech voice takes to read this out, with a short breath after."""
    return len(text.split()) / READING_WORDS_PER_SECOND + 0.8


@dataclass
class SpeechResult:
    """What happened to a request to speak."""

    spoken: bool
    reason: str
    message: str
    speakers: list[str]
    deliveries: list[dict[str, Any]] = field(default_factory=list)

    def as_dict(self) -> dict[str, Any]:
        return {
            "spoken": self.spoken,
            "reason": self.reason,
            "message": self.message,
            "speakers": self.speakers,
            **({"deliveries": self.deliveries} if self.deliveries else {}),
        }


def _parse_time(value: str | None, fallback: str) -> time:
    try:
        return time.fromisoformat(value or fallback)
    except ValueError:
        return time.fromisoformat(fallback)


def in_quiet_hours(now: datetime, start: time, end: time) -> bool:
    """True when now falls inside quiet hours (handles windows over midnight)."""
    current = now.time()
    if start == end:
        return False
    if start < end:
        return start <= current < end
    return current >= start or current < end


def tts_language(hass: HomeAssistant, engine: str, preferred: str | None = None) -> str | None:
    """The house's language as this text-to-speech service knows it (en-GB, en, or its own default)."""
    try:
        from homeassistant.components.tts.helper import get_engine_instance

        inst = get_engine_instance(hass, engine)
    except Exception:  # noqa: BLE001 - tts not loaded
        return None
    if inst is None:
        return None
    langs = list(getattr(inst, "supported_languages", None) or [])
    base = (preferred or hass.config.language or "en").replace("_", "-")
    wants = [base] + ([f"{base}-{hass.config.country}"] if "-" not in base and hass.config.country else [])
    low = {lang.lower(): lang for lang in langs}
    for want in reversed(wants):  # en-GB before en
        if want.lower() in low:
            return low[want.lower()]
    prefix = base.split("-")[0].lower()
    near = [lang for lang in langs if lang.lower().split("-")[0] == prefix]
    return near[0] if near else getattr(inst, "default_language", None)


def tts_media_id(hass: HomeAssistant, text: str, engine: str, language: str | None, options: dict | None) -> str:
    """Audio of this text from a chosen text-to-speech service, as a media id a satellite can announce."""
    from homeassistant.components.tts import generate_media_source_id

    return generate_media_source_id(hass, text, engine, language, options, cache=True)


def _platform(hass: HomeAssistant, entity_id: str) -> str | None:
    from homeassistant.helpers import entity_registry as er

    entry = er.async_get(hass).async_get(entity_id)
    return entry.platform if entry else None


def _remark_key(text: str) -> str:
    return re.sub(r"[^a-z0-9 ]", "", text.lower()).strip()


def clean_message(message: str, banned: list[str]) -> str:
    """Strip unwanted forms of address and tidy the sentence."""
    text = message
    for word in banned:
        if not word:
            continue
        w = re.escape(word)
        text = re.sub(rf"(?i)^\s*\b{w}\b[\s,]*", "", text)  # "Sir, the gate..."
        text = re.sub(rf"(?i),\s*\b{w}\b", "", text)  # "...is open, sir."
        text = re.sub(rf"(?i)\s*\b{w}\b", "", text)  # "Good evening sir, ..."
    # say temperatures the way a person would: "18.2 degrees" -> "18 degrees"
    text = re.sub(r"(?<![\d.])(-?\d+\.\d+)(\s*(?:°\s*C?|degrees?\b))",
                  lambda m: f"{math.floor(float(m.group(1)) + 0.5)}{m.group(2)}", text)
    text = re.sub(r"\s+([,.!?])", r"\1", text)
    text = re.sub(r"^[\s,.;:-]+", "", text)
    text = re.sub(r"\s{2,}", " ", text).strip()
    if text:
        text = text[0].upper() + text[1:]
    return text


# what a switch, calendar or phone Do Not Disturb sensor says when the household is busy
HOUSE_BUSY_STATES = {"on", "true", "busy", "priority_only", "alarms_only", "total_silence", "do_not_disturb", "dnd"}


class SpeechRouter:
    """Decides whether, where and how to speak."""

    def __init__(
        self, hass: HomeAssistant, options: dict[str, Any], tracker: ActivityTracker | None = None
    ) -> None:
        self.hass = hass
        self.options = options
        self.tracker = tracker
        self._recent: dict[str, datetime] = {}
        self._remarks: dict[str, datetime] = {}  # remark -> when last said; kept across restarts
        self.silent_runs: dict[str, int] = {}  # Echo -> announcements in a row it showed no sign of playing
        self._remark_store: Store[dict[str, str]] | None = None
        self.failures: dict[str, tuple[datetime, str]] = {}  # destination -> (when it last failed, why)
        self._privacy_generation = 0
        self._said: deque[tuple[datetime, str]] = deque(maxlen=20)
        self.reading_fallbacks: deque[tuple[datetime, str, str]] = deque(maxlen=50)  # the reading voice failing
        self._questions: dict[str, dict[str, Any]] = {}  # satellite -> the house's open question and what to do with the answer
        self._delivered_recent: dict[tuple[str, str], datetime] = {}
        self.deliveries: deque[dict[str, Any]] = deque(maxlen=20)
        self._lock = asyncio.Lock()
        self.hush_until: datetime | None = None  # "be quiet for an hour"
        self.hold_until: datetime | None = None  # just announced something important: no chatter for a moment
        self.guest_mode = False
        self.rooms = RoomLevels()  # each room's usual loudness, for speaking up over noise
        self._prepared: dict[str, Any] = {}  # reading voice audio being made ahead
        self.phone = None  # set by the runtime: asks yes/no questions on phones when nobody's near a speaker
        self.profiles = None  # set by the runtime: room profiles (own quiet time, volume, no offers)
        self.holiday = None  # set by the runtime: holiday mode (only safety alerts are said out loud)
        self.on_unheard = None  # set by the runtime: (text, priority, source, held_reason) for messages nobody heard
        self.nobody_about = False  # the last pick found nobody in any speaker's room
        self.mutes = None  # set by the runtime: things people asked not to hear about  # visitors in: no remarks or offers, keep personal things private

    # ----- remembering what was said, across restarts -----------------------------------
    async def async_load_remarks(self, entry_id: str) -> None:
        """Restore what was said in the last day, so a restart or update doesn't let the kettle remark come round again."""
        self._remark_store = Store(self.hass, 1, f"{DOMAIN}.{entry_id}.remarks")
        try:
            saved = await self._remark_store.async_load() or {}
        except Exception:  # noqa: BLE001 - a damaged file just means starting fresh
            saved = {}
        now = dt_util.utcnow()
        for key, stamp in saved.items():
            when = dt_util.parse_datetime(stamp) if isinstance(stamp, str) else None
            if when is not None and now - when < timedelta(hours=24) and key not in self._remarks:
                self._remarks[key] = when

    def save_remarks(self) -> None:
        if self._remark_store is not None:
            self._remark_store.async_delay_save(lambda: {k: v.isoformat() for k, v in self._remarks.items()}, 5)

    # ----- configuration helpers -------------------------------------------------
    @property
    def media_on(self) -> bool:
        """Media players (Echos…) are used as speakers; off keeps to the voice speakers."""
        return bool(self.options.get(CONF_MEDIA_SPEAKERS, True))

    @property
    def media_important_only(self) -> bool:
        """Echos only for alarms, reminders and safety alerts; everything else on the voice speakers."""
        return bool(self.options.get(CONF_MEDIA_IMPORTANT_ONLY, False))

    def usable(self, entity_ids: list[str] | None, important: bool = True) -> list[str]:
        media = self.media_on and (important or not self.media_important_only)
        return [e for e in entity_ids or [] if media or not e.startswith("media_player.")]

    @property
    def speakers(self) -> list[str]:
        return self.usable(self.options.get(CONF_SPEAKERS))

    def volume_now(self) -> float | None:
        """Speaker volume for speaking now (0..1), or None to leave it alone."""
        key = CONF_NIGHT_VOLUME if self.quiet_hours_now() and self.options.get(CONF_NIGHT_VOLUME) else CONF_SPEAK_VOLUME
        value = self.options.get(key)
        return float(value) / 100 if value else None

    @property
    def duck_level(self) -> float | None:
        return 0.15 if self.options.get(CONF_DUCK_MEDIA, True) else None

    @property
    def chime(self) -> bool:
        """Play the satellite's chime before speaking."""
        return bool(self.options.get(CONF_CHIME, DEFAULT_CHIME))

    @property
    def banned(self) -> list[str]:
        raw = self.options.get(CONF_BANNED_WORDS, DEFAULT_BANNED_WORDS) or ""
        return [w.strip() for w in raw.split(",") if w.strip()]

    def busy(self) -> bool:
        """A chosen "busy" signal is on: a meeting, a Do Not Disturb phone, a focus switch."""
        for entity_id in self.options.get(CONF_BUSY_ENTITIES) or []:
            st = self.hass.states.get(entity_id)
            if st is not None and st.state.lower() in HOUSE_BUSY_STATES:
                return True
        return False

    def hushed(self) -> bool:
        if self.busy():
            return True  # held like a hush; urgent things still speak
        if self.hush_until is None:
            return False
        if dt_util.utcnow() >= self.hush_until:
            self.hush_until = None
            return False
        return True

    def quiet_now(self) -> bool:
        """Quiet hours, or hushed by someone."""
        return self.hushed() or self.quiet_hours_now()

    def quiet_hours_now(self) -> bool:
        return in_quiet_hours(
            dt_util.now(),
            _parse_time(self.options.get(CONF_QUIET_START), DEFAULT_QUIET_START),
            _parse_time(self.options.get(CONF_QUIET_END), DEFAULT_QUIET_END),
        )

    # ----- room awareness -------------------------------------------------------
    def _area_of(self, entity_id: str) -> str | None:
        return area_of(self.hass, entity_id)

    def pick_speaker(self, important: bool = True) -> list[str]:
        """Speakers where people are right now (busiest first), else the default.

        Backup-only speakers are used only when none of the main speakers' rooms
        has anyone in it.
        """
        speakers = self.usable(self.options.get(CONF_SPEAKERS), important)
        live = [
            s for s in speakers
            if (st := self.hass.states.get(s)) is not None and st.state != "unavailable"
        ]
        backup = set(self.usable(self.options.get(CONF_FALLBACK_SPEAKERS), important))
        self.nobody_about = False
        if self.tracker is not None:
            for group in ([s for s in live if s not in backup], [s for s in live if s in backup]):
                chosen = self._occupied(group)
                if chosen:
                    return chosen
            self.nobody_about = True
        default = self.options.get(CONF_DEFAULT_SPEAKER)
        if default and self.usable([default], important):
            return [default]
        return speakers[:1]

    def _occupied(self, speakers: list[str]) -> list[str]:
        by_area: dict[str, str] = {}
        for speaker in speakers:
            area = self._area_of(speaker)
            if area and area not in by_area:
                by_area[area] = speaker
        if not by_area or self.tracker is None:
            return []
        if self.options.get(CONF_ALL_OCCUPIED, DEFAULT_ALL_OCCUPIED):
            areas = self.tracker.occupied_areas(set(by_area))
        else:
            areas = [a] if (a := self.tracker.best_area(set(by_area))) else []
        return [by_area[a] for a in areas]

    FILLER = frozenset({"the", "a", "in", "on", "speaker", "speakers", "echo", "alexa", "room", "downstairs", "please", "dot", "show"})

    @classmethod
    def _squash(cls, text: str) -> str:
        """Lower-case and drop spaces, underscores and punctuation ('Living Room' == 'livingroom')."""
        return re.sub(r"[^a-z0-9]", "", text.lower())

    @classmethod
    def _core(cls, text: str) -> str:
        words = [w for w in re.split(r"[^a-z0-9]+", text.lower()) if w and w not in cls.FILLER]
        return "".join(words)

    def find_speaker(self, name: str) -> str | None:
        """Match an entity id, area name or speaker name to a configured speaker.

        Spoken names are forgiving: 'living room', 'livingroom' and 'the living room speaker' all
        match the same speaker, and an exact name or area beats a partial match.
        """
        needle = name.strip().lower()
        every = self.speakers + [s for s in self.usable(self.options.get(CONF_FALLBACK_SPEAKERS)) if s not in self.speakers]
        if name in every:
            return name
        if not needle:
            return None
        areas = ar.async_get(self.hass)
        wanted = {self._squash(needle), self._core(needle)} - {""}
        best: tuple[int, int] | None = None
        found: str | None = None
        for order, speaker in enumerate(every):
            area_id = self._area_of(speaker)
            area = areas.async_get_area(area_id) if area_id else None
            state = self.hass.states.get(speaker)
            friendly = (state.name or "") if state else ""  # the composed name (2026.11 naming), not the old attribute
            object_id = speaker.split(".", 1)[-1]
            reg = er.async_get(self.hass).async_get(speaker)
            aliases = [a for a in (reg.aliases if reg else ()) if isinstance(a, str)]  # what people call it in Assist
            names = [n for n in (friendly, area.name if area else "", object_id, *aliases) if n]
            literal = {self._squash(n) for n in names}
            exact = literal | {self._core(n) for n in names}
            exact.discard("")
            haystack = self._squash(" ".join([speaker, friendly, area.name if area else ""]))
            if self._squash(needle) in literal:
                score = 3
            elif wanted & exact:
                score = 2
            elif any(w in haystack for w in wanted):
                score = 1
            else:
                continue
            # Prefer a speaker that is actually reachable, then the earlier one in the list.
            alive = 0 if state is None or state.state == "unavailable" else 1
            key = (score, alive)
            if best is None or key > best:
                best, found = key, speaker
        return found

    def describe_rooms(self) -> str:
        """Human-readable speaker/area map, for the AI's context."""
        areas = ar.async_get(self.hass)
        parts = []
        every = self.speakers + [s for s in self.usable(self.options.get(CONF_FALLBACK_SPEAKERS)) if s not in self.speakers]
        for speaker in every:
            area_id = self._area_of(speaker)
            area = areas.async_get_area(area_id) if area_id else None
            state = self.hass.states.get(speaker)
            friendly = state.name if state else None
            parts.append(f"{speaker} ({friendly or 'unnamed'}, {area.name if area else 'no area'})")
        return ", ".join(parts)

    # ----- suppression ----------------------------------------------------------
    def hold(self, seconds: float) -> None:
        """Keep everyday chatter back for a little while (after the doorbell, the gate…)."""
        until = dt_util.utcnow() + timedelta(seconds=seconds)
        if self.hold_until is None or until > self.hold_until:
            self.hold_until = until

    def _recently_triggered(self) -> str | None:
        if self.hold_until is not None and dt_util.utcnow() < self.hold_until:
            return "announcement"
        seconds = float(self.options.get(CONF_SUPPRESS_SECONDS, DEFAULT_SUPPRESS_SECONDS))
        for entity_id in self.options.get(CONF_SUPPRESS_ENTITIES) or []:
            state = self.hass.states.get(entity_id)
            if state is None:
                continue
            last = state.attributes.get("last_triggered")
            # only a real run counts; a restart or toggle must not hush the house
            when = dt_util.parse_datetime(last) if isinstance(last, str) else last
            if when and dt_util.utcnow() - when < timedelta(seconds=seconds):
                return entity_id
        return None

    # ----- the main entry points ---------------------------------------------------
    def recently_triggered(self) -> str | None:
        return self._recently_triggered()

    def recent_said(self, limit: int = 5) -> list[str]:
        return [text for _, text in list(self._said)[:limit]]

    def _gate(self, text: str, priority: str, source: str | None, check_duplicate: bool = True) -> SpeechResult | None:
        """Shared rules; returns a held result, or None when it may speak."""
        if not text:
            return self._held("empty", text, source)
        if self.holiday is not None and not self.holiday.speaks(source) and not str(source or "").startswith(REPLY_SOURCES):
            return self._held("holiday", text, source)  # nobody's home to hear it: it goes to the phones
        if priority != PRIORITY_URGENT and not str(source or "").startswith(REPLY_SOURCES):
            if self.mutes is not None and (muted := self.mutes.text_muted(text)):
                return self._held(f"muted:{muted}", text, source)
            feedback = getattr(self.mutes, "feedback", None) if self.mutes is not None else None
            if feedback is not None and feedback.source_quiet(source):
                return self._held("thumbs_down", text, source)
            if self.hushed():
                return self._held("hushed", text, source, priority)
            if self.quiet_now():
                return self._held("quiet_hours", text, source, priority)
            blocker = self._recently_triggered()
            if blocker:
                return self._held(f"hushed_by:{blocker}", text, source)
            if priority == PRIORITY_LOW and self._recent:
                # low priority never interrupts something said in the last minute
                if any(dt_util.utcnow() - t < timedelta(seconds=60) for t in self._recent.values()):
                    return self._held("busy", text, source)
        if source == "notice" and (hours := float(self.options.get(CONF_REMARK_REPEAT_HOURS, DEFAULT_REMARK_REPEAT_HOURS) or 0)):
            last = self._remarks.get(_remark_key(text))
            if last is not None and dt_util.utcnow() - last < timedelta(hours=hours):
                return self._held("said_recently", text, source)  # "The kettle has finished boiling." once is enough
        if check_duplicate and source != "critical" and (last := self._recent.get(text.lower())) is not None and dt_util.utcnow() - last < _DUPLICATE_WINDOW:
            return self._held("duplicate", text, source)
        return None

    def _remember_said(self, text: str, spoken: str | None = None) -> None:
        now = dt_util.utcnow()
        key = _remark_key(text)
        self._remarks = {k: v for k, v in self._remarks.items() if now - v < timedelta(hours=24)}
        self._remarks[key] = now
        self.save_remarks()
        self._recent = {k: v for k, v in self._recent.items() if now - v < _DUPLICATE_WINDOW}
        self._recent[text.lower()] = now
        self._said.appendleft((now, spoken if spoken is not None else text))

    async def async_say(
        self,
        message: str,
        priority: str = "normal",
        targets: list[str] | None = None,
        source: str | None = None,
        *, _spoken: str | None = None,
    ) -> SpeechResult:
        generation = self._privacy_generation
        text = clean_message(message, self.banned)
        if (held := self._gate(text, priority, source, check_duplicate=False)) is not None:
            return held
        # "Echos: alarms and reminders only" is for the house's own chatter; a speaker someone named is theirs to get
        vital = important(source) or (source == "conversation" and bool(targets))
        if source != "critical":  # safety alerts always use every speaker
            targets = self.usable(targets, vital) or None  # Echos switched off: their messages go where people are
        speakers = targets or self.pick_speaker(vital)
        if self.profiles is not None and speakers:
            allowed = self.profiles.allowed(speakers, priority, source)
            if not allowed:
                return self._held("room_quiet", text, source)  # those rooms' own quiet time
            speakers = allowed
        if not speakers:
            return self._held("no_speaker", text, source)
        unheard = not targets and self.nobody_about
        spoken = _spoken if _spoken is not None else await async_in_house_language(
            self.hass, self.options, text, urgent=source == "critical" or priority == PRIORITY_URGENT
        )

        async with self._lock:
            await self._wait_idle(speakers)
            if generation != self._privacy_generation:
                return SpeechResult(False, "cancelled", "", [])
            if (held := self._gate(text, priority, source, check_duplicate=False)) is not None:
                return held
            now = dt_util.utcnow()
            self._delivered_recent = {k: v for k, v in self._delivered_recent.items() if now - v < _DUPLICATE_WINDOW}
            speakers = list(dict.fromkeys(speakers))
            if source != "critical":
                speakers = [s for s in speakers if (text.lower(), s) not in self._delivered_recent]
            if not speakers:
                return self._held("duplicate", text, source)
            volume = await self._volumes(speakers)
            async with delivered(self.hass, speakers, volume, self.duck_level):
                if generation != self._privacy_generation:
                    return SpeechResult(False, "cancelled", "", [])
                receipts = await self._speak(spoken, speakers, original=text)
            # Compatibility for injected/test delivery backends that return no receipts.
            receipts = receipts if receipts is not None else [{'entity_id': s, 'status': 'accepted'} for s in speakers]
            accepted = [r['entity_id'] for r in receipts if r['status'] == 'accepted']
            if generation != self._privacy_generation:
                return SpeechResult(bool(accepted), 'spoken' if accepted else 'failed', spoken, accepted, receipts)
            for speaker in accepted:
                self._delivered_recent[(text.lower(), speaker)] = dt_util.utcnow()
            self._remember_said(text, spoken)
        speakers = accepted
        reason = "spoken" if len(accepted) == len(receipts) else "partial"
        self.hass.bus.async_fire(
            EVENT_SPOKE,
            {"message": spoken, "speakers": speakers, "priority": priority, "source": source, "unheard": unheard or None,
             "deliveries": receipts, "why": reason_for(source)},
        )
        if unheard and self.on_unheard is not None:
            self.on_unheard(text, priority, source, None)
        return SpeechResult(True, reason, spoken, speakers, receipts)

    async def async_read_aloud(self, text: str, speaker: str, story: bool = False) -> bool:
        """One passage of a book or a recipe step someone asked for: straight to that speaker, not logged as
        something the house said, and not held for quiet hours (they asked). Waits until it has been read.

        A story uses the reading voice from Settings when one is chosen (not on Echos — Alexa only has its own).
        Some satellites (a browser tablet, for one) accept an announcement and return straight away, so the
        wait is also paced by the passage's length — otherwise the next passage would cut this one off."""
        loop = asyncio.get_running_loop()
        text = clean_message(text, self.banned)
        started = took = 0.0
        try:
            async with self._lock:
                await self._wait_idle([speaker])
                volume = await self._volumes([speaker])
                async with delivered(self.hass, [speaker], volume, self.duck_level):
                    started = loop.time()
                    receipts = None
                    if story and (engine := self.options.get(CONF_READING_TTS)):
                        receipts = await self._speak_voice(text, speaker, engine, self.options.get(CONF_READING_VOICE))
                    if receipts is None:  # no reading voice, or this speaker can't take one
                        receipts = await self._speak(text, [speaker], chime=False)
                    took = loop.time() - started
        except HomeAssistantError:
            return False
        if not any(r["status"] == "accepted" for r in receipts or []):
            return False
        if took < INSTANT_SECONDS:
            # handed straight back (a browser tablet, or a media player still starting): wait for the speech —
            # a speaker that reports when it has finished (a voice satellite) needs no padding, and padding it
            # only left silence whenever it finished early
            if speaker.startswith("media_player."):
                await asyncio.sleep(INSTANT_SECONDS)
            else:
                await asyncio.sleep(max(0.0, reading_seconds(text) - took))
        for _ in range(int(reading_seconds(text)) + 30):  # and then until the speaker says it has finished
            st = self.hass.states.get(speaker)
            if st is None or st.state not in ("playing", "responding"):
                break
            await asyncio.sleep(1)
        return True

    @callback
    def prepare_reading(self, text: str, speaker: str) -> None:
        """Make the next piece's audio in the reading voice while this one is read, so a slow voice leaves no gap."""
        engine = self.options.get(CONF_READING_TTS)
        if not engine or not self.options.get(CONF_READ_AHEAD, True) or self.hass.states.get(engine) is None:
            return
        if speaker.startswith("media_player.") and _platform(self.hass, speaker) == "alexa_media":
            return
        text = clean_message(text, self.banned)
        voice = self.options.get(CONF_READING_VOICE)
        key = f"{engine}|{voice}|{text}"
        if key in self._prepared:
            return
        try:
            media_id = tts_media_id(self.hass, text, engine, tts_language(self.hass, engine, selected_language(self.hass, self.options) if self.options.get("speak_house_language") else None), {"voice": voice} if voice else None)
        except (HomeAssistantError, ValueError):
            return
        self._prepared[key] = self.hass.async_create_background_task(self._make_audio(media_id), "sentient read ahead")
        while len(self._prepared) > 4:
            self._prepared.pop(next(iter(self._prepared)))

    async def _make_audio(self, media_id: str) -> None:
        from homeassistant.components.tts import async_get_media_source_audio

        try:
            async with asyncio.timeout(120):
                await async_get_media_source_audio(self.hass, media_id)  # cached: the real request is then instant
        except Exception as err:  # noqa: BLE001 - only a head start; reading carries on without it
            _LOGGER.debug("Couldn't make the next piece's audio ahead: %s", err)

    async def _speak_voice(self, text: str, speaker: str, engine: str, voice: str | None) -> list[dict[str, Any]] | None:
        """Speak with a chosen text-to-speech service and voice; None when this speaker can't (an Echo)."""
        if self.hass.states.get(engine) is None or (speaker.startswith("media_player.") and _platform(self.hass, speaker) == "alexa_media"):
            return None
        language = tts_language(self.hass, engine, selected_language(self.hass, self.options) if self.options.get("speak_house_language") else None)
        options = {"voice": voice} if voice else None
        try:
            async with asyncio.timeout(reading_seconds(text) * 3 + 45):  # making the audio can be slow (free voices)
                if speaker.startswith("assist_satellite."):
                    media_id = tts_media_id(self.hass, text, engine, language, options)
                    await self.hass.services.async_call("assist_satellite", "announce", {
                        "message": text, "media_id": media_id, "preannounce": False}, target={"entity_id": [speaker]}, blocking=True)
                else:
                    await self.hass.services.async_call("tts", "speak", {
                        "media_player_entity_id": [speaker], "message": text, "cache": True,
                        **({"language": language} if language else {}), **({"options": options} if options else {}),
                    }, target={"entity_id": engine}, blocking=True)
        except (HomeAssistantError, TimeoutError, ValueError) as err:
            _LOGGER.warning("The reading voice (%s) couldn't speak on %s: %s — using the usual voice", engine, speaker,
                            str(err) or type(err).__name__)
            self.reading_fallbacks.append((dt_util.utcnow(), engine, speaker))
            return None
        return [{"entity_id": speaker, "status": "accepted"}]

    async def async_try_voice(self, speaker: str, engine: str | None, voice: str | None) -> dict[str, Any]:
        """Settings → Books: hear a line in a reading voice before choosing it."""
        from .const import READING_SAMPLE

        if engine:
            receipts = await self._speak_voice(READING_SAMPLE, speaker, engine, voice)
            if receipts is None:
                return {"ok": False, "why": "that voice couldn't speak on this speaker (Echos only have Alexa's own voice)"}
        else:
            receipts = await self._speak(READING_SAMPLE, [speaker], chime=False)
        return {"ok": any(r["status"] == "accepted" for r in receipts or []), "speaker": speaker}

    async def _speak(self, text: str, speakers: list[str], chime: bool | None = None, *, original: str | None = None) -> list[dict[str, Any]]:
        from .voice_timing import measure

        with measure('speech_delivery'):
            return await self._speak_timed(text, speakers, chime, original=original)

    async def _speak_timed(self, text: str, speakers: list[str], chime: bool | None = None, *, original: str | None = None) -> list[dict[str, Any]]:
        """One request per destination; acceptance is not independent proof of audible sound."""
        generation = self._privacy_generation
        chime = self.chime if chime is None else chime
        rows = {eid: {'entity_id': eid, 'status': 'cancelled',
                      'error': 'Delivery interrupted; playback outcome is unknown'} for eid in dict.fromkeys(speakers)}
        seen_before = {eid: st.last_updated for eid in rows if eid.startswith('media_player.') and (st := self.hass.states.get(eid))}

        async def send(eid):
            row = rows[eid]
            started = monotonic()
            row.update(stage='routing', dispatched=False, playback='unverified')
            row['status'] = 'failed'
            row.pop('error', None)
            try:
                if eid.startswith('assist_satellite.'):
                    domain, service = 'assist_satellite', 'announce'
                    data, target = {'message': text, 'preannounce': chime}, {'entity_id': [eid]}
                elif eid.startswith('media_player.') and _platform(self.hass, eid) == 'alexa_media':
                    domain, service = 'notify', 'alexa_media'
                    # Each call targets one Echo; use its entity ID directly.
                    kind = 'tts' if self.options.get(CONF_ALEXA_TTS_ONLY, False) or not chime else 'announce'
                    data, target = {'message': text, 'target': eid, 'data': {'type': kind}}, None
                elif eid.startswith('media_player.') and (tts := self.options.get(CONF_TTS_ENTITY)):
                    domain, service = 'tts', 'speak'
                    data, target = {'media_player_entity_id': [eid], 'message': text, 'cache': True}, {'entity_id': tts}
                else:
                    row.update(status='unroutable', error='No usable announcement or TTS service is configured')
                    return row
                row['service'] = f'{domain}.{service}'
                if not self.hass.services.has_service(domain, service):
                    row.update(status='unroutable', error=f'{domain}.{service} is unavailable')
                    return row
                state = self.hass.states.get(eid)
                if state is not None and state.state == 'unavailable':
                    row.update(status='unavailable', error='Speaker reports unavailable')
                    return row
                row.update(stage='service_call', dispatched=True)
                async with asyncio.timeout(DELIVERY_TIMEOUT):
                    await self.hass.services.async_call(domain, service, data, target=target, blocking=True)
                row.update(status='accepted', stage='service_completed')
                if generation == self._privacy_generation:
                    self._delivered_recent[((original if original is not None else text).lower(), eid)] = dt_util.utcnow()
            except asyncio.CancelledError:
                row.update(status='cancelled', error='Delivery interrupted; playback outcome is unknown')
                raise
            except TimeoutError:
                row.update(status='timed_out', error='Delivery timed out; playback outcome is unknown')
            except Exception as err:  # one failed destination must not cancel another
                row['error'] = (str(err) or type(err).__name__)[:200]
            finally:
                row['duration_ms'] = round((monotonic() - started) * 1000)
            return row

        try:
            receipts = await asyncio.gather(*(send(eid) for eid in rows))
        except asyncio.CancelledError:
            if generation == self._privacy_generation:
                self._record_deliveries(text, list(rows.values()))
            raise
        if generation == self._privacy_generation:
            self._record_deliveries(text, receipts)
            if (self.options.get('speech_failure_notification', False) and not self.guest_mode
                    and any(r['status'] != 'accepted' for r in receipts)):
                from homeassistant.components import persistent_notification

                persistent_notification.async_create(
                    self.hass, 'Speaker delivery was not confirmed for: '
                    + ', '.join(r['entity_id'] for r in receipts if r['status'] != 'accepted')
                    + '. Playback may still occur; speech has not been retried.',
                    title='Sentient speaker delivery', notification_id='sentient_speech_failure')
        if not any(r['status'] == 'accepted' for r in receipts):
            raise HomeAssistantError('; '.join(r.get('error', r['status']) for r in receipts) or 'No usable speech destination')
        if any(r['status'] == 'accepted' and r['entity_id'].startswith('media_player.') for r in receipts):
            await asyncio.sleep(min(15.0, 2.0 + len(text.split()) / 2.6))
            for row in receipts:  # a sign (not proof) that the Echo did something: its state moved while it was told to speak
                if row['status'] == 'accepted' and row['entity_id'].startswith('media_player.'):
                    st = self.hass.states.get(row['entity_id'])
                    moved = st is not None and (st.state == 'playing' or st.last_updated > seen_before.get(row['entity_id'], st.last_updated))
                    row['playback'] = 'state_changed' if moved else 'no_change'
                    self.silent_runs[row['entity_id']] = 0 if moved else self.silent_runs.get(row['entity_id'], 0) + 1
        return receipts

    def _record_deliveries(self, text, receipts):
        now = dt_util.utcnow()
        for row in receipts:
            if row['status'] == 'accepted':
                self.failures.pop(row['entity_id'], None)
            else:
                self.failures[row['entity_id']] = (now, row.get('error', row['status']))
        record = {'time': now.isoformat(), 'message': text[:300], 'results': receipts,
                  'note': 'Accepted means Home Assistant completed the service call; audibility is unverified.'}
        if self.options.get(CONF_SPEECH_DELIVERY_HISTORY, True):
            self.deliveries.appendleft(record)
            self.hass.bus.async_fire('sentient_speech_delivery')

    async def _volumes(self, speakers: list[str]) -> float | dict[str, float] | None:
        """The level to speak at: the usual one, raised in a noisy room (not in quiet hours),
        and full volume on the speakers chosen for that (outside quiet hours)."""
        out = await self._room_volumes(speakers)
        if self.profiles is not None and (own := {s: v for s in speakers if (v := self.profiles.volume(s)) is not None}):
            out = {**({s: out for s in speakers} if not isinstance(out, dict) else out), **own}
        full = set(self.options.get(CONF_FULL_VOLUME_SPEAKERS) or []) & set(speakers)
        if not full or self.quiet_hours_now():
            return out
        per = dict(out) if isinstance(out, dict) else {s: out for s in speakers}
        per.update({s: 1.0 for s in full})
        return per

    async def _room_volumes(self, speakers: list[str]) -> float | dict[str, float] | None:
        base = self.volume_now()
        if not self.options.get(CONF_ROOM_VOLUME, True) or self.quiet_hours_now():
            return base
        listen = speakers
        if self.duck_level is not None:  # music here is turned down anyway: no need to shout over it
            busy = set(playing_nearby(self.hass, speakers))
            listen = [s for s in speakers if not any(self._area_of(p) == self._area_of(s) for p in busy)]
        try:
            raised = await self.rooms.async_volumes(self.hass, listen, base)
        except Exception as err:  # noqa: BLE001 - never stop the message for this
            _LOGGER.debug("Room listen failed: %s", err)
            return base
        if not raised:
            return base
        return {s: raised.get(s, base) for s in speakers}

    async def async_ask(
        self,
        question: str,
        priority: str = "normal",
        targets: list[str] | None = None,
        source: str | None = None,
        extra_prompt: str | None = None,
        preannounce: bool | None = None,
        on_answer: Callable[[str], Awaitable[str]] | None = None,
    ) -> SpeechResult:
        """Ask out loud and open the microphone, so the reply goes to that speaker's voice agent.

        With *on_answer*, a plain yes / no / "it already does that" reply is handled here without the AI:
        on_answer('yes' | 'no' | 'done') does it and returns what to say back.

        Only one speaker can listen, so it's the busiest room's. Speakers that can't
        start a conversation get the question as a plain announcement instead.
        """
        generation = self._privacy_generation
        text = clean_message(question, self.banned)
        if (held := self._gate(text, priority, source)) is not None:
            return held
        vital = important(source)
        candidates = self.usable(targets, vital) or self.pick_speaker(vital)
        if self.profiles is not None and candidates:
            candidates = self.profiles.allowed(candidates, priority, source)
            if not candidates:
                return self._held("room_quiet", text, source)
        listening = [
            s for s in candidates
            if s.startswith("assist_satellite.") and (st := self.hass.states.get(s)) is not None
            and int(st.attributes.get("supported_features") or 0) & _START_CONVERSATION
        ]
        spoken = await async_in_house_language(self.hass, self.options, text, urgent=source == "critical" or priority == PRIORITY_URGENT)
        if generation != self._privacy_generation:
            return SpeechResult(False, "cancelled", "", [])
        if on_answer is not None and not targets and self.nobody_about and self.phone is not None and self.phone.can_ask():
            # nobody near a speaker: ask on the phones, with Yes / No buttons
            if await self.phone.async_ask(spoken, on_answer):
                if generation != self._privacy_generation:
                    return SpeechResult(True, "asked_phone", spoken, [])
                self.hass.bus.async_fire(EVENT_SPOKE, {"message": spoken, "speakers": [], "priority": priority, "source": source,
                                                       "question": True, "phone": True, "why": reason_for(source)})
                return SpeechResult(True, "asked_phone", spoken, [])
        if not listening:
            return await self.async_say(text, priority, candidates, source, _spoken=spoken)
        speaker = listening[0]
        prompt = (
            f'You have just asked the person: "{spoken}". Handle their reply: if they say yes, '
            "do it with your tools and confirm in a few words; if no, acknowledge briefly."
        )
        if extra_prompt:
            prompt += " " + extra_prompt
        async with self._lock:
            await self._wait_idle([speaker])
            if generation != self._privacy_generation:
                return SpeechResult(False, "cancelled", "", [])
            if (held := self._gate(text, priority, source)) is not None:
                return held
            await self.hass.services.async_call(
                "assist_satellite",
                "start_conversation",
                {
                    "start_message": spoken,
                    "extra_system_prompt": prompt,
                    "preannounce": self.chime if preannounce is None else preannounce,
                },
                target={"entity_id": speaker},
                blocking=True,
            )
            if generation != self._privacy_generation:
                return SpeechResult(True, "asked", spoken, [speaker])
            self._remember_said(text, spoken)
        if on_answer is not None:
            self._questions[speaker] = {"until": dt_util.utcnow() + QUESTION_OPEN, "on_answer": on_answer, "asked": spoken}
        self.hass.bus.async_fire(
            EVENT_SPOKE,
            {"message": spoken, "speakers": [speaker], "priority": priority, "source": source, "question": True,
             "why": reason_for(source)},
        )
        return SpeechResult(True, "asked", spoken, [speaker])

    async def async_take_answer(self, satellite: str | None, text: str) -> str | None:
        """If this satellite was just asked one of the house's questions and the reply is a plain answer,
        act on it and return what to say back; None to let the AI handle it."""
        from .answers import classify

        question = self._questions.get(satellite or "")
        if question is None:
            return None
        if dt_util.utcnow() > question["until"]:
            self._questions.pop(satellite, None)
            return None
        from .sessions import is_conversation_end

        self._questions.pop(satellite, None)  # a correction or topic change also cancels the old request
        from .language import selected_language

        lang = selected_language(self.hass, self.options) if self.options.get("speak_house_language") else None
        kind = "no" if is_conversation_end(text, question["asked"], self.options.get(CONF_PERSONA) or DEFAULT_PERSONA, language=lang) else classify(text, language=lang)
        if kind is None and self.options.get('automatic_language'):
            from .language import async_language_rejection

            if await async_language_rejection(self.hass, self.options, text):
                kind = 'no'
        if kind is None:
            return None
        return await question["on_answer"](kind)

    async def _wait_idle(self, speakers: list[str], timeout: float = 15) -> None:
        """Don't talk over a conversation that's already happening."""
        loop_until = dt_util.utcnow() + timedelta(seconds=timeout)
        while dt_util.utcnow() < loop_until:
            if not any(
                (s := self.hass.states.get(e)) is not None and s.state in _BUSY_STATES for e in speakers
            ):
                return
            await asyncio.sleep(0.5)

    def _held(self, reason: str, text: str, source: str | None, priority: str | None = None) -> SpeechResult:
        _LOGGER.debug("Held speech (%s): %s", reason, text)
        self.hass.bus.async_fire(EVENT_HELD, {"message": text, "reason": reason, "source": source, "why": reason_for(source)})
        if reason in ("quiet_hours", "hushed") and priority and self.on_unheard is not None:
            self.on_unheard(text, priority, source, reason)
        return SpeechResult(False, reason, text, [])
