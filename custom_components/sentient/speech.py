"""Speech routing for Sentient.

One place decides *whether*, *where* and *how* the house speaks:

* quiet hours (urgent messages still get through)
* words the household doesn't want used ("sir", names, ...)
* duplicate suppression
* a short hush after chosen automations/scripts (e.g. the doorbell flow), so a
  background remark doesn't talk over a more important announcement
* the speaker in the room with the most recent motion, otherwise the default
"""

from __future__ import annotations

import asyncio
from dataclasses import dataclass
from datetime import datetime, time, timedelta
import logging
import re
from typing import Any

from homeassistant.const import STATE_ON
from homeassistant.core import HomeAssistant, State
from homeassistant.helpers import area_registry as ar
from homeassistant.helpers import device_registry as dr
from homeassistant.helpers import entity_registry as er
from homeassistant.util import dt as dt_util

from .const import (
    CONF_BANNED_WORDS,
    CONF_DEFAULT_SPEAKER,
    CONF_MOTION_WINDOW,
    CONF_QUIET_END,
    CONF_QUIET_START,
    CONF_SPEAKERS,
    CONF_SUPPRESS_ENTITIES,
    CONF_SUPPRESS_SECONDS,
    DEFAULT_BANNED_WORDS,
    DEFAULT_MOTION_WINDOW,
    DEFAULT_QUIET_END,
    DEFAULT_QUIET_START,
    DEFAULT_SUPPRESS_SECONDS,
    EVENT_HELD,
    EVENT_SPOKE,
    PRIORITY_LOW,
    PRIORITY_URGENT,
)

_LOGGER = logging.getLogger(__name__)

_PRESENCE_CLASSES = {"motion", "occupancy", "presence"}
_DUPLICATE_WINDOW = timedelta(seconds=120)
_BUSY_STATES = {"listening", "processing", "responding"}


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

    def __init__(self, hass: HomeAssistant, options: dict[str, Any]) -> None:
        self.hass = hass
        self.options = options
        self._recent: dict[str, datetime] = {}
        self._lock = asyncio.Lock()

    # ----- configuration helpers -------------------------------------------------
    @property
    def speakers(self) -> list[str]:
        return list(self.options.get(CONF_SPEAKERS) or [])

    @property
    def banned(self) -> list[str]:
        raw = self.options.get(CONF_BANNED_WORDS, DEFAULT_BANNED_WORDS) or ""
        return [w.strip() for w in raw.split(",") if w.strip()]

    def quiet_now(self) -> bool:
        return in_quiet_hours(
            dt_util.now(),
            _parse_time(self.options.get(CONF_QUIET_START), DEFAULT_QUIET_START),
            _parse_time(self.options.get(CONF_QUIET_END), DEFAULT_QUIET_END),
        )

    # ----- room awareness -------------------------------------------------------
    def _area_of(self, entity_id: str) -> str | None:
        ent_reg = er.async_get(self.hass)
        entry = ent_reg.async_get(entity_id)
        if entry is None:
            return None
        if entry.area_id:
            return entry.area_id
        if entry.device_id:
            device = dr.async_get(self.hass).async_get(entry.device_id)
            if device:
                return device.area_id
        return None

    def _presence_sensors(self, area_id: str) -> list[State]:
        """Motion/occupancy sensors in an area, directly or through their device."""
        ent_reg = er.async_get(self.hass)
        entries = list(er.async_entries_for_area(ent_reg, area_id))
        for device in dr.async_entries_for_area(dr.async_get(self.hass), area_id):
            entries.extend(
                e for e in er.async_entries_for_device(ent_reg, device.id) if e.area_id in (None, area_id)
            )
        found: dict[str, State] = {}
        for entry in entries:
            if entry.domain != "binary_sensor" or entry.entity_id in found:
                continue
            state = self.hass.states.get(entry.entity_id)
            if state is not None and state.attributes.get("device_class") in _PRESENCE_CLASSES:
                found[entry.entity_id] = state
        return list(found.values())

    def _presence(self, area_id: str) -> tuple[bool, datetime] | None:
        """(motion active now, when it last changed) for the freshest sensor in an area.

        Rooms with motion *now* beat rooms where it has cleared; among those,
        the room where motion started most recently wins — that's where the
        person has just walked into.
        """
        best: tuple[bool, datetime] | None = None
        for state in self._presence_sensors(area_id):
            score = (state.state == STATE_ON, state.last_changed)
            if best is None or score > best:
                best = score
        return best

    def pick_speaker(self) -> list[str]:
        """The speaker in the room the person most recently moved into, else the default."""
        window = timedelta(minutes=float(self.options.get(CONF_MOTION_WINDOW, DEFAULT_MOTION_WINDOW)))
        best: tuple[tuple[bool, datetime], str] | None = None
        for speaker in self.speakers:
            state = self.hass.states.get(speaker)
            if state is None or state.state == "unavailable":
                continue
            area = self._area_of(speaker)
            if not area:
                continue
            presence = self._presence(area)
            if presence is None:
                continue
            active, changed = presence
            if not active and dt_util.utcnow() - changed > window:
                continue
            if best is None or presence > best[0]:
                best = (presence, speaker)
        if best:
            return [best[1]]
        default = self.options.get(CONF_DEFAULT_SPEAKER)
        if default:
            return [default]
        return self.speakers[:1]

    def find_speaker(self, name: str) -> str | None:
        """Match an entity id, area name or speaker name to a configured speaker."""
        needle = name.strip().lower()
        if name in self.speakers:
            return name
        areas = ar.async_get(self.hass)
        for speaker in self.speakers:
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
    def _recently_triggered(self) -> str | None:
        seconds = float(self.options.get(CONF_SUPPRESS_SECONDS, DEFAULT_SUPPRESS_SECONDS))
        for entity_id in self.options.get(CONF_SUPPRESS_ENTITIES) or []:
            state = self.hass.states.get(entity_id)
            if state is None:
                continue
            last = state.attributes.get("last_triggered")
            when = dt_util.parse_datetime(last) if isinstance(last, str) else last
            when = when or state.last_changed
            if when and dt_util.utcnow() - when < timedelta(seconds=seconds):
                return entity_id
        return None

    # ----- the main entry point ---------------------------------------------------
    async def async_say(
        self,
        message: str,
        priority: str = "normal",
        targets: list[str] | None = None,
        source: str | None = None,
    ) -> SpeechResult:
        text = clean_message(message, self.banned)
        if not text:
            return self._held("empty", message, source)

        if priority != PRIORITY_URGENT:
            if self.quiet_now():
                return self._held("quiet_hours", text, source)
            blocker = self._recently_triggered()
            if blocker:
                return self._held(f"hushed_by:{blocker}", text, source)
            if priority == PRIORITY_LOW and self._recent:
                # low priority never interrupts something said in the last minute
                if any(dt_util.utcnow() - t < timedelta(seconds=60) for t in self._recent.values()):
                    return self._held("busy", text, source)

        key = text.lower()
        last = self._recent.get(key)
        if last and dt_util.utcnow() - last < _DUPLICATE_WINDOW:
            return self._held("duplicate", text, source)

        speakers = targets or self.pick_speaker()
        if not speakers:
            return self._held("no_speaker", text, source)

        async with self._lock:
            await self._wait_idle(speakers)
            self._recent = {
                k: v for k, v in self._recent.items() if dt_util.utcnow() - v < _DUPLICATE_WINDOW
            }
            self._recent[key] = dt_util.utcnow()
            await self.hass.services.async_call(
                "assist_satellite",
                "announce",
                {"message": text},
                target={"entity_id": speakers},
                blocking=True,
            )
        self.hass.bus.async_fire(
            EVENT_SPOKE, {"message": text, "speakers": speakers, "priority": priority, "source": source}
        )
        return SpeechResult(True, "spoken", text, speakers)

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
