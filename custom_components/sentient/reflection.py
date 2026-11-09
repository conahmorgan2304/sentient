"""Overnight reflection: once a night, Sentient thinks over the day.

One AI call in the small hours, with the day's timeline and conversations and what it already
knows. Out of it come:
* a few **memories** — durable things people said in passing that are worth keeping
  ("the boiler engineer's coming on Thursday"), skipping anything already known or private;
* at most two **morning thoughts** — short, genuinely useful observations, passed on the first
  time someone's about after quiet hours (and in the morning briefing);
* at most one **question** it's curious about, for curiosity to ask in the daytime.
Nothing comes out of a quiet day, and that's fine.
"""

from __future__ import annotations

from datetime import datetime, timedelta
import logging
import hashlib
import json
from copy import deepcopy
from typing import Any, Callable

from homeassistant.core import HomeAssistant, callback
from homeassistant.helpers.event import async_track_time_change
from homeassistant.helpers.storage import Store
from homeassistant.util import dt as dt_util

from .ai import async_generate
from .const import (
    CONF_AI_TASK_ENTITY,
    CONF_SMART_AI,
    CONF_HOUSE_RULES,
    CONF_PERSONA,
    CONF_QUIET_END,
    CONF_REFLECTION,
    CONF_REFLECTION_TIME,
    DEFAULT_PERSONA,
    DEFAULT_QUIET_END,
    DEFAULT_REFLECTION_TIME,
    DOMAIN,
    personality_line,
)
from .followyou import next_quiet_end

_LOGGER = logging.getLogger(__name__)

KINDS = ["said", "visitor", "watch", "health", "security", "critical", "energy", "noticed", "loop", "drive", "mode", "light"]
MAX_MEMORIES = 3
MAX_THOUGHTS = 2
PRIVATE = ("password", "pin ", "card", "account number", "bank", "salary", "diagnos", "medication", "illness")


class Reflection:
    def __init__(self, hass: HomeAssistant, entry_id: str, options: dict[str, Any], rt_getter: Callable[[], Any]) -> None:
        self.hass = hass
        self.options = options
        self._rt = rt_getter
        self._store: Store[dict[str, Any]] = Store(hass, 1, f"{DOMAIN}.{entry_id}.reflection")
        self.last: dict[str, Any] | None = None
        self._unsub = None
        self._busy = False
        self._stopped = False

    async def async_start(self) -> None:
        self._stopped = False
        self.last = (await self._store.async_load() or {}).get("last")
        self._unsub = async_track_time_change(self.hass, self._tick, second=5)

    @callback
    def async_stop(self) -> None:
        self._stopped = True
        if self._unsub:
            self._unsub()
            self._unsub = None

    @callback
    def _tick(self, now: datetime) -> None:
        if self._stopped or not self.options.get(CONF_REFLECTION, True):
            return
        if self.last and (last := dt_util.parse_datetime(self.last.get("time") or "")) and dt_util.as_local(last).date() == dt_util.as_local(now).date():
            return
        at = str(self.options.get(CONF_REFLECTION_TIME) or DEFAULT_REFLECTION_TIME)[:5]
        if dt_util.as_local(now).strftime("%H:%M") == at:
            self.hass.async_create_background_task(self._async_quietly(), "sentient_reflection")

    async def _async_quietly(self) -> None:
        try:
            await self.async_reflect()
        except Exception as err:  # noqa: BLE001 - a missed night is no loss
            _LOGGER.debug("Reflection skipped: %s", err)

    def thoughts_for_briefing(self) -> list[str]:
        """This morning's thoughts, if they're fresh."""
        if not self.last or not self.last.get("thoughts"):
            return []
        when = dt_util.parse_datetime(self.last.get("time") or "")
        if when is None or dt_util.utcnow() - when > timedelta(hours=16):
            return []
        return list(self.last["thoughts"])

    def _material(self, rt) -> tuple[list[str], list[str]]:
        """The day's log with the noise folded up (a motion light's fifty on/offs are one line), plus what it learned."""
        log: list[str] = []
        repeats: dict[str, list] = {}
        order: list[str] = []
        for i in reversed(rt.timeline.since(24, KINDS, 400)):
            if i["kind"] == "noticed" and "wasn't worth saying" in str(i.get("detail") or ""):
                continue
            when = dt_util.as_local(dt_util.parse_datetime(i["time"])).strftime("%a %H:%M")
            text = f"[{i['kind']}] {i['text']}"
            if i["kind"] in ("light", "noticed", "security"):
                if text not in repeats:
                    repeats[text] = [when, when, 0]
                    order.append(text)
                repeats[text][1] = when
                repeats[text][2] += 1
                continue
            log.append(f"{when} {text}" + (f" ({i['detail']})" if i.get("detail") else ""))
        for text in order:
            first, last, n = repeats[text]
            log.append(f"{first} {text}" + (f" — {n} times, last {last}" if n > 1 else ""))
        log = sorted(log)[-160:]
        # what it learned or changed its mind about today
        learned: list[str] = []
        inner = getattr(rt, "inner", None)
        since = dt_util.utcnow().timestamp() - 86400
        for t in (getattr(inner, "thoughts", None) or []):
            if t.get("t", 0) >= since:
                learned.append(f"[learning] {t['text']}")
        for c in (getattr(getattr(rt, "memory", None), "changed", None) or [])[-10:]:
            when = dt_util.parse_datetime(c.get("time") or "")
            if when and when.timestamp() >= since:
                learned.append(f"[memory {c['kind']}] {c['old']}" + (f" → {c['new']}" if c.get("new") else ""))
        if inner is not None and getattr(inner, "thoughts_on", False):
            p = inner.prediction()
            if p.get("tried"):
                learned.append(f"[learning] next-room guesses right {round((p['rate'] or 0) * 100)}% of {p['tried']}")
        log += learned
        talk = []
        for ep in reversed(rt.conversations.recent(hours=24, limit=60)):
            when = dt_util.as_local(dt_util.parse_datetime(ep["start"])).strftime("%a %H:%M")
            room = f" in the {ep['room']}" if ep.get("room") else ""
            turns = " / ".join(f"{'Them' if t['who'] == 'them' else 'You'}: {t['text'][:200]}" for t in ep["turns"][-8:])
            talk.append(f"{when}{room}: {turns}")
        return log, talk

    async def async_reflect(self) -> dict[str, Any]:
        rt = self._rt()
        if self._busy:
            return {"skipped": "reflection already running"}
        if self._stopped or not rt.options.get(CONF_REFLECTION, True) or rt.speech.guest_mode:
            return {"skipped": "reflection is off or unavailable in guest mode"}
        self._busy = True
        options = rt.options
        stamp = (getattr(rt.speech, '_privacy_generation', 0), getattr(rt.speech, '_guest_generation', 0))
        def current():
            return (not self._stopped and rt.options is options and options.get(CONF_REFLECTION, True)
                    and not rt.speech.guest_mode
                    and stamp == (getattr(rt.speech, '_privacy_generation', 0), getattr(rt.speech, '_guest_generation', 0)))
        try:
            return await self._async_reflect(rt, current)
        finally:
            self._busy = False

    async def _async_reflect(self, rt, current) -> dict[str, Any]:
        log, talk = self._material(rt)
        context = {key: rt.options.get(key) for key in (CONF_PERSONA, CONF_HOUSE_RULES, CONF_SMART_AI,
                   CONF_AI_TASK_ENTITY, 'voice_language', 'speak_house_language', 'automatic_language')}
        fingerprint = hashlib.sha256(json.dumps([log, talk, context], ensure_ascii=False, sort_keys=True).encode()).hexdigest()
        if self.last and self.last.get("material") == fingerprint:
            when = dt_util.parse_datetime(self.last.get("time") or "")
            if when and dt_util.as_local(when).date() == dt_util.now().date():
                return {**deepcopy(self.last), "note": "already reflected on this material today"}
        result: dict[str, Any] = {"time": dt_util.utcnow().isoformat(), "memories": [], "thoughts": [], "question": None, "lessons": [], "material": fingerprint}
        if not log and not talk:
            result["note"] = "a quiet day: nothing to think over"
            self._keep(result)
            return result
        persona = rt.options.get(CONF_PERSONA) or DEFAULT_PERSONA
        known = [m.text for m in rt.memory.recent(60)]
        rules = (rt.options.get(CONF_HOUSE_RULES) or "").strip()
        instructions = (
            f"You are {persona}, the mind of a family home, thinking over the last 24 hours. "
            "From the log and the conversations below, decide:\n"
            f"1. memories: up to {MAX_MEMORIES} durable facts people actually said that are worth knowing later "
            "(plans, people, preferences, things about the house). One short sentence each. Skip anything already "
            "known, anything that will be stale within a day, guesses, and anything private (health, money, "
            "passwords, account or card numbers). Usually there are none.\n"
            f"2. thoughts: up to {MAX_THOUGHTS} short, natural sentences worth saying to the family this morning — "
            "something useful they'd want to know, a pattern worth pointing out, or something you learned or changed "
            "your mind about (the [learning] and [memory] lines) — say 'yesterday' or 'last night' for things before "
            "this morning. Most days there is one worth saying; leave it empty only when nothing is worth it. "
            "Never repeat routine noise (a light going on and off with movement). No greetings, no names, no forms of address.\n"
            "3. question: at most one specific thing you're genuinely curious about that a person in the house could "
            "answer in a few words and that would help you look after the house — about one pattern, time or "
            "device you saw (e.g. 'what's the garage light for at 7pm?', 'is the fan meant to run all night?'). Never a general or security worry, never about who people are, who visited or where people went "
            "(that's private, and the cameras are for safety), never something already known. Usually empty.\n"
            "4. lessons: corrections people made to you in the conversations ('no, I meant the lamp', 'not that "
            "room') worth remembering so it never happens again — one short sentence each, saying what was meant "
            "(e.g. 'In the living room, \"the light\" means the lamp.'). Skip any already known. Usually none.\n"
            "Never say or imply the house is or was empty. British English. " + personality_line(rt.options)
            + (f"\nHouse rules:\n{rules}" if rules else "")
            + "\n\nAlready known:\n" + "\n".join(f"- {k}" for k in known[:60])
            + "\n\nLog:\n" + ("\n".join(log) or "(nothing)")
            + "\n\nConversations:\n" + ("\n".join(talk) or "(none)")
        )
        data: dict[str, Any] = {
            "task_name": "Sentient: overnight reflection",
            "instructions": instructions[:24000],
            "structure": {
                "memories": {"description": "Durable facts worth keeping (often none)", "required": False,
                             "selector": {"text": {"multiple": True}}},
                "thoughts": {"description": "Morning thoughts (usually one or two)", "required": False,
                             "selector": {"text": {"multiple": True}}},
                "question": {"description": "One curious question, or empty", "required": False,
                             "selector": {"text": {}}},
                "lessons": {"description": "Corrections worth remembering (often none)", "required": False,
                            "selector": {"text": {"multiple": True}}},
            },
        }
        if ai_entity := rt.options.get(CONF_SMART_AI) or rt.options.get(CONF_AI_TASK_ENTITY):
            data["entity_id"] = ai_entity
        if not rt.watches.spend("overnight"):
            return {"skipped": "today's AI limit was used up: no reflection tonight"}
        response = await async_generate(self.hass, data)
        if not current():
            return {"skipped": "reflection changed while waiting; nothing was kept"}
        out = response.get("data") if isinstance(response, dict) else None
        if not isinstance(out, dict):
            return {"error": "The reflection returned an invalid result; nothing was kept"}

        def _list(value: Any) -> list[str]:
            if isinstance(value, str):
                value = [value]
            if not isinstance(value, list):
                return []
            return [" ".join(v.split()) for v in value if isinstance(v, str) and v.strip() and len(v) <= 1000]

        known_ids = {m.id for m in rt.memory.items}
        for text in _list(out.get("memories"))[:MAX_MEMORIES]:
            if any(p in text.lower() for p in PRIVATE):
                continue
            try:
                memory = rt.memory.add(text, "fact", source="reflection")
                if memory.id not in known_ids:
                    result["memories"].append(memory.text)
                    known_ids.add(memory.id)
            except ValueError:
                pass
        result["lessons"] = []
        for text in _list(out.get("lessons"))[:3]:
            if any(p in text.lower() for p in PRIVATE):
                continue
            try:
                memory = rt.memory.add(text, "correction", source="reflection")
                if memory.id not in known_ids:
                    result["lessons"].append(memory.text)
                    known_ids.add(memory.id)
            except ValueError:
                pass
        result["thoughts"] = _list(out.get("thoughts"))[:MAX_THOUGHTS]
        raw_question = out.get("question")
        question = " ".join(raw_question.split()) if isinstance(raw_question, str) and len(raw_question) <= 1000 else ""
        if question and rt.curiosity.wonder(question, why="overnight reflection", source="reflection"):
            result["question"] = question
        if result["thoughts"]:
            now = dt_util.now()
            morning = next_quiet_end(now, rt.options.get(CONF_QUIET_END) or DEFAULT_QUIET_END)
            for thought in result["thoughts"]:
                rt.followyou.keep(thought, source="thought", not_before=morning, hours=4)
        summary = "; ".join(
            p for p in (
                f"{len(result['memories'])} new memor{'y' if len(result['memories']) == 1 else 'ies'}" if result["memories"] else "",
                " ".join(result["thoughts"]),
                f"wondering: {result['question']}" if result["question"] else "",
                f"learned from {len(result['lessons'])} correction{'s' if len(result['lessons']) > 1 else ''}" if result["lessons"] else "",
            ) if p
        )
        rt.timeline.add("mind", summary or "Thought over the day — nothing to add.", "overnight reflection")
        self._keep(result)
        return result

    def _keep(self, result: dict[str, Any]) -> None:
        self.last = result
        self._store.async_delay_save(lambda: {"last": self.last}, 2)
