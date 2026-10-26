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
from dataclasses import dataclass
from datetime import datetime, time, timedelta
import logging
import re
from typing import Any

from homeassistant.core import HomeAssistant
from homeassistant.helpers import area_registry as ar
from homeassistant.util import dt as dt_util

from .const import (
    CONF_FALLBACK_SPEAKERS,
    CONF_ALL_OCCUPIED,
    DEFAULT_ALL_OCCUPIED,
    CONF_BANNED_WORDS,
    CONF_DEFAULT_SPEAKER,
    CONF_QUIET_END,
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
    DEFAULT_CHIME,
    PRIORITY_LOW,
    PRIORITY_URGENT,
)
from .delivery import delivered
from .presence import ActivityTracker, area_of

_LOGGER = logging.getLogger(__name__)

_DUPLICATE_WINDOW = timedelta(seconds=120)
_BUSY_STATES = {"listening", "processing", "responding"}
_START_CONVERSATION = 2  # AssistSatelliteEntityFeature.START_CONVERSATION


@dataclass
class SpeechResult:
    """What happened to a request to speak."""

    spoken: bool
    reason: str
    message: str
    speakers: list[str]

    def as_dict(self) -> dict[str, Any]:
        return {
            "spoken": self.spoken,
            "reason": self.reason,
            "message": self.message,
            "speakers": self.speakers,
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
    text = re.sub(r"\s+([,.!?])", r"\1", text)
    text = re.sub(r"^[\s,.;:-]+", "", text)
    text = re.sub(r"\s{2,}", " ", text).strip()
    if text:
        text = text[0].upper() + text[1:]
    return text


class SpeechRouter:
    """Decides whether, where and how to speak."""

    def __init__(
        self, hass: HomeAssistant, options: dict[str, Any], tracker: ActivityTracker | None = None
    ) -> None:
        self.hass = hass
        self.options = options
        self.tracker = tracker
        self._recent: dict[str, datetime] = {}
        self._said: deque[tuple[datetime, str]] = deque(maxlen=20)
        self._lock = asyncio.Lock()
        self.hush_until: datetime | None = None  # "be quiet for an hour"
        self.hold_until: datetime | None = None  # just announced something important: no chatter for a moment
        self.guest_mode = False
        self.mutes = None  # set by the runtime: things people asked not to hear about  # visitors in: no remarks or offers, keep personal things private

    # ----- configuration helpers -------------------------------------------------
    @property
    def speakers(self) -> list[str]:
        return list(self.options.get(CONF_SPEAKERS) or [])

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

    def hushed(self) -> bool:
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

    def pick_speaker(self) -> list[str]:
        """Speakers where people are right now (busiest first), else the default.

        Backup-only speakers are used only when none of the main speakers' rooms
        has anyone in it.
        """
        live = [
            s for s in self.speakers
            if (st := self.hass.states.get(s)) is not None and st.state != "unavailable"
        ]
        backup = set(self.options.get(CONF_FALLBACK_SPEAKERS) or [])
        if self.tracker is not None:
            for group in ([s for s in live if s not in backup], [s for s in live if s in backup]):
                chosen = self._occupied(group)
                if chosen:
                    return chosen
        default = self.options.get(CONF_DEFAULT_SPEAKER)
        if default:
            return [default]
        return self.speakers[:1]

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

    def find_speaker(self, name: str) -> str | None:
        """Match an entity id, area name or speaker name to a configured speaker."""
        needle = name.strip().lower()
        every = self.speakers + [s for s in self.options.get(CONF_FALLBACK_SPEAKERS) or [] if s not in self.speakers]
        if name in every:
            return name
        areas = ar.async_get(self.hass)
        for speaker in every:
            area_id = self._area_of(speaker)
            area = areas.async_get_area(area_id) if area_id else None
            state = self.hass.states.get(speaker)
            friendly = (state.attributes.get("friendly_name") or "") if state else ""
            haystack = " ".join([speaker, friendly, area.name if area else ""]).lower()
            if needle and needle in haystack:
                return speaker
        return None

    def describe_rooms(self) -> str:
        """Human-readable speaker/area map, for the AI's context."""
        areas = ar.async_get(self.hass)
        parts = []
        for speaker in self.speakers:
            area_id = self._area_of(speaker)
            area = areas.async_get_area(area_id) if area_id else None
            parts.append(f"{speaker} ({area.name if area else 'no area'})")
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

    def _gate(self, text: str, priority: str, source: str | None) -> SpeechResult | None:
        """Shared rules; returns a held result, or None when it may speak."""
        if not text:
            return self._held("empty", text, source)
        if priority != PRIORITY_URGENT:
            if self.mutes is not None and (muted := self.mutes.text_muted(text)):
                return self._held(f"muted:{muted}", text, source)
            if self.hushed():
                return self._held("hushed", text, source)
            if self.quiet_now():
                return self._held("quiet_hours", text, source)
            blocker = self._recently_triggered()
            if blocker:
                return self._held(f"hushed_by:{blocker}", text, source)
            if priority == PRIORITY_LOW and self._recent:
                # low priority never interrupts something said in the last minute
                if any(dt_util.utcnow() - t < timedelta(seconds=60) for t in self._recent.values()):
                    return self._held("busy", text, source)
        last = self._recent.get(text.lower())
        if source != "critical" and last and dt_util.utcnow() - last < _DUPLICATE_WINDOW:
            return self._held("duplicate", text, source)
        return None

    def _remember_said(self, text: str) -> None:
        now = dt_util.utcnow()
        self._recent = {k: v for k, v in self._recent.items() if now - v < _DUPLICATE_WINDOW}
        self._recent[text.lower()] = now
        self._said.appendleft((now, text))

    async def async_say(
        self,
        message: str,
        priority: str = "normal",
        targets: list[str] | None = None,
        source: str | None = None,
    ) -> SpeechResult:
        text = clean_message(message, self.banned)
        if (held := self._gate(text, priority, source)) is not None:
            return held
        speakers = targets or self.pick_speaker()
        if not speakers:
            return self._held("no_speaker", text, source)

        async with self._lock:
            await self._wait_idle(speakers)
            self._remember_said(text)
            async with delivered(self.hass, speakers, self.volume_now(), self.duck_level):
                await self.hass.services.async_call(
                    "assist_satellite",
                    "announce",
                    {"message": text, "preannounce": self.chime},
                    target={"entity_id": speakers},
                    blocking=True,
                )
        self.hass.bus.async_fire(
            EVENT_SPOKE, {"message": text, "speakers": speakers, "priority": priority, "source": source}
        )
        return SpeechResult(True, "spoken", text, speakers)

    async def async_ask(
        self,
        question: str,
        priority: str = "normal",
        targets: list[str] | None = None,
        source: str | None = None,
        extra_prompt: str | None = None,
        preannounce: bool | None = None,
    ) -> SpeechResult:
        """Ask out loud and open the microphone, so the reply goes to that speaker's voice agent.

        Only one speaker can listen, so it's the busiest room's. Speakers that can't
        start a conversation get the question as a plain announcement instead.
        """
        text = clean_message(question, self.banned)
        if (held := self._gate(text, priority, source)) is not None:
            return held
        candidates = targets or self.pick_speaker()
        listening = [
            s for s in candidates
            if (st := self.hass.states.get(s)) is not None
            and int(st.attributes.get("supported_features") or 0) & _START_CONVERSATION
        ]
        if not listening:
            return await self.async_say(text, priority, candidates, source)
        speaker = listening[0]
        prompt = (
            f'You have just asked the person: "{text}". Handle their reply: if they say yes, '
            "do it with your tools and confirm in a few words; if no, acknowledge briefly."
        )
        if extra_prompt:
            prompt += " " + extra_prompt
        async with self._lock:
            await self._wait_idle([speaker])
            self._remember_said(text)
            await self.hass.services.async_call(
                "assist_satellite",
                "start_conversation",
                {
                    "start_message": text,
                    "extra_system_prompt": prompt,
                    "preannounce": self.chime if preannounce is None else preannounce,
                },
                target={"entity_id": speaker},
                blocking=True,
            )
        self.hass.bus.async_fire(
            EVENT_SPOKE,
            {"message": text, "speakers": [speaker], "priority": priority, "source": source, "question": True},
        )
        return SpeechResult(True, "asked", text, [speaker])

    async def _wait_idle(self, speakers: list[str], timeout: float = 15) -> None:
        """Don't talk over a conversation that's already happening."""
        loop_until = dt_util.utcnow() + timedelta(seconds=timeout)
        while dt_util.utcnow() < loop_until:
            if not any(
                (s := self.hass.states.get(e)) is not None and s.state in _BUSY_STATES for e in speakers
            ):
                return
            await asyncio.sleep(0.5)

    def _held(self, reason: str, text: str, source: str | None) -> SpeechResult:
        _LOGGER.debug("Held speech (%s): %s", reason, text)
        self.hass.bus.async_fire(EVENT_HELD, {"message": text, "reason": reason, "source": source})
        return SpeechResult(False, reason, text, [])
