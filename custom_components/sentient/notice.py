"""Noticing: the house speaks up on its own, cheaply.

The user picks the things worth noticing. When they change:

1. free rules run first — quiet hours, hush periods, an hourly limit on
   remarks, the daily AI limit, and jitter filtering for numbers;
2. changes arriving close together are batched into one decision;
3. only then is the (cheapest) AI task asked whether anything is worth saying,
   with the house rules, memories and what was said recently as context;
4. it can reply with a remark, or a question that opens the microphone so the
   person's answer goes straight to their voice agent.
"""

from __future__ import annotations

from collections import deque
from dataclasses import dataclass
from datetime import datetime, timedelta
import logging
from typing import TYPE_CHECKING, Any

from homeassistant.const import EVENT_STATE_CHANGED, STATE_UNAVAILABLE, STATE_UNKNOWN
from homeassistant.core import Event, EventStateChangedData, HomeAssistant, callback
from homeassistant.exceptions import HomeAssistantError
from homeassistant.helpers import area_registry as ar
from homeassistant.helpers.event import async_call_later
from homeassistant.util import dt as dt_util

from .const import (
    CONF_AI_TASK_ENTITY,
    CONF_CHATTINESS,
    CONF_HOUSE_RULES,
    CONF_NOTICE_ENTITIES,
    CONF_PERSONA,
    CHATTINESS_LIMITS,
    DEFAULT_CHATTINESS,
    DEFAULT_PERSONA,
    EVENT_NOTICED,
)
from .presence import area_of

if TYPE_CHECKING:
    from .memory import MemoryStore
    from .speech import SpeechRouter
    from .watches import WatchManager

_LOGGER = logging.getLogger(__name__)

BATCH_SECONDS = 20
NUMERIC_GAP = timedelta(minutes=15)
OTHER_GAP = timedelta(seconds=30)
_IGNORED = {STATE_UNAVAILABLE, STATE_UNKNOWN, None}


@dataclass
class Change:
    entity_id: str
    name: str
    area: str | None
    old: str | None
    new: str
    unit: str
    when: datetime

    def describe(self) -> str:
        where = f" ({self.area})" if self.area else ""
        unit = f" {self.unit}" if self.unit else ""
        was = f" from {self.old}{unit}" if self.old not in _IGNORED else ""
        return f"{dt_util.as_local(self.when):%H:%M} {self.name}{where} changed{was} to {self.new}{unit}"


def _number(value: str | None) -> float | None:
    try:
        return float(value)  # type: ignore[arg-type]
    except (TypeError, ValueError):
        return None


class Noticer:
    """Turns chosen state changes into the occasional well-judged remark."""

    def __init__(
        self,
        hass: HomeAssistant,
        options: dict[str, Any],
        speech: SpeechRouter,
        memory: MemoryStore,
        watches: WatchManager,
    ) -> None:
        self.hass = hass
        self.options = options
        self.speech = speech
        self.memory = memory
        self.watches = watches
        self._pending: list[Change] = []
        self._last_seen: dict[str, datetime] = {}
        self._remarks: deque[datetime] = deque(maxlen=50)
        self.decisions: deque[dict[str, Any]] = deque(maxlen=20)
        self._flush_unsub = None
        self._unsub = None

    # ----- settings -------------------------------------------------------------
    @property
    def entities(self) -> set[str]:
        return set(self.options.get(CONF_NOTICE_ENTITIES) or [])

    @property
    def per_hour(self) -> int:
        return CHATTINESS_LIMITS.get(self.options.get(CONF_CHATTINESS, DEFAULT_CHATTINESS), 0)

    # ----- lifecycle ------------------------------------------------------------
    @callback
    def async_start(self) -> None:
        self._unsub = self.hass.bus.async_listen(EVENT_STATE_CHANGED, self._on_state_changed)

    @callback
    def async_stop(self) -> None:
        if self._unsub:
            self._unsub()
            self._unsub = None
        if self._flush_unsub:
            self._flush_unsub()
            self._flush_unsub = None

    # ----- collecting -----------------------------------------------------------
    @callback
    def _on_state_changed(self, event: Event[EventStateChangedData]) -> None:
        new = event.data["new_state"]
        if new is None or new.entity_id not in self.entities or self.per_hour <= 0:
            return
        if self.speech.mutes is not None and self.speech.mutes.entity_muted(new.entity_id):
            return
        old = event.data["old_state"]
        if old is None or old.state in _IGNORED or new.state in _IGNORED or old.state == new.state:
            return  # appearing at start-up, coming back from unavailable, or attribute-only
        now = dt_util.utcnow()
        numeric = _number(new.state) is not None
        last = self._last_seen.get(new.entity_id)
        if last and now - last < (NUMERIC_GAP if numeric else OTHER_GAP):
            return
        if numeric and old is not None and (a := _number(old.state)) is not None:
            b = _number(new.state) or 0.0
            if abs(b - a) < max(0.5, abs(a) * 0.05):
                return  # jitter
        self._last_seen[new.entity_id] = now
        area_id = area_of(self.hass, new.entity_id)
        area = ar.async_get(self.hass).async_get_area(area_id) if area_id else None
        self._pending.append(
            Change(
                entity_id=new.entity_id,
                name=new.name,
                area=area.name if area else None,
                old=old.state if old else None,
                new=new.state,
                unit=new.attributes.get("unit_of_measurement") or "",
                when=now,
            )
        )
        if self._flush_unsub is None:
            self._flush_unsub = async_call_later(self.hass, BATCH_SECONDS, self._flush)

    @callback
    def _flush(self, _now: datetime) -> None:
        self._flush_unsub = None
        changes, self._pending = self._pending, []
        if changes:
            self.hass.async_create_task(self.async_consider(changes))

    # ----- deciding -------------------------------------------------------------
    def _free_rules(self) -> str | None:
        """Reasons not to even ask the AI."""
        if self.speech.guest_mode:
            return "guest_mode"
        if self.speech.quiet_now():
            return "quiet_hours"
        if blocker := self.speech.recently_triggered():
            return f"hushed_by:{blocker}"
        hour_ago = dt_util.utcnow() - timedelta(hours=1)
        if sum(1 for t in self._remarks if t > hour_ago) >= self.per_hour:
            return "hourly_limit"
        return None

    async def async_consider(self, changes: list[Change]) -> dict[str, Any]:
        record = await self._async_decide(changes)
        self.hass.bus.async_fire(EVENT_NOTICED, record)
        return record

    async def _async_decide(self, changes: list[Change]) -> dict[str, Any]:
        record: dict[str, Any] = {
            "time": dt_util.now().isoformat(),
            "changes": [c.describe() for c in changes],
        }
        self.decisions.appendleft(record)
        if reason := self._free_rules():
            record["result"] = f"skipped: {reason}"
            return record
        if not self.watches.spend():
            record["result"] = "skipped: daily AI limit reached"
            return record
        try:
            decision = await self._async_ask_ai(changes)
        except HomeAssistantError as err:
            record["result"] = f"error: {err}"
            _LOGGER.warning("Sentient could not decide about %s: %s", record["changes"], err)
            return record
        speak = bool(decision.get("speak")) and bool((decision.get("message") or "").strip())
        record["result"] = "spoke" if speak else "stayed quiet"
        record["message"] = decision.get("message")
        if speak:
            self._remarks.append(dt_util.utcnow())
            message = decision["message"].strip()
            if decision.get("question"):
                result = await self.speech.async_ask(message, source="notice")
            else:
                result = await self.speech.async_say(message, "normal", None, source="notice")
            record["result"] = result.reason
        return record

    def _context(self) -> str:
        persona = self.options.get(CONF_PERSONA) or DEFAULT_PERSONA
        lines = [
            f"You are {persona}, the brain of a family home, deciding whether to speak up.",
            f"Local time: {dt_util.now():%A %d %B, %H:%M}.",
        ]
        if rules := (self.options.get(CONF_HOUSE_RULES) or "").strip():
            lines.append(f"House rules you must follow:\n{rules}")
        if self.speech.tracker is not None:
            rooms = self.speech.tracker.occupied_areas()
            names = [
                (a.name if (a := ar.async_get(self.hass).async_get_area(r)) else r) for r in rooms
            ]
            if names:
                lines.append(f"People seem to be in: {', '.join(names)}.")
        others = []
        for entity_id in sorted(self.entities)[:30]:
            if (st := self.hass.states.get(entity_id)) is not None:
                unit = st.attributes.get("unit_of_measurement") or ""
                others.append(f"- {st.name}: {st.state}{(' ' + unit) if unit else ''}")
        if others:
            lines.append("Current state of the things you watch:\n" + "\n".join(others))
        memories = self.memory.recent(15)
        if memories:
            lines.append("What you know about the household:\n" + "\n".join(f"- {m.text}" for m in memories))
        said = self.speech.recent_said(6)
        if said:
            lines.append("What you've said recently (don't repeat yourself):\n" + "\n".join(f"- {s}" for s in said))
        return "\n".join(lines)

    async def _async_ask_ai(self, changes: list[Change]) -> dict[str, Any]:
        instructions = (
            self._context()
            + "\n\nThis just happened:\n"
            + "\n".join(f"- {c.describe()}" for c in changes)
            + "\n\nDecide whether it's worth saying something out loud. Most changes are not: "
            "speak only when it is useful, timely or genuinely interesting to the people at home "
            "(something finished, something left open, a change that needs action, a helpful heads-up). "
            "If you speak, write one short, natural sentence in British English, no IDs or entity names "
            "in code form, no forms of address. Set question to true only if you're offering to do "
            "something and need a yes or no (e.g. 'It's got chilly, shall I put the heating on?')."
        )
        data: dict[str, Any] = {
            "task_name": "Sentient: worth saying?",
            "instructions": instructions,
            "structure": {
                "speak": {"description": "True only if worth saying out loud", "required": True,
                          "selector": {"boolean": {}}},
                "message": {"description": "One short spoken sentence, or empty", "required": False,
                            "selector": {"text": {}}},
                "question": {"description": "True if the message asks for a yes/no reply", "required": False,
                             "selector": {"boolean": {}}},
            },
        }
        if ai_entity := self.options.get(CONF_AI_TASK_ENTITY):
            data["entity_id"] = ai_entity
        response = await self.hass.services.async_call(
            "ai_task", "generate_data", data, blocking=True, return_response=True
        )
        result = (response or {}).get("data") or {}
        return result if isinstance(result, dict) else {}
