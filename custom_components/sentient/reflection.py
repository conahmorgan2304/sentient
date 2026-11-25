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
import re
import json
from copy import deepcopy
from typing import Any, Callable

from homeassistant.core import HomeAssistant, callback
from homeassistant.helpers.event import async_track_time_change
from homeassistant.helpers.storage import Store
from homeassistant.util import dt as dt_util

from .language import write_in
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
MAX_FIXES = 3
CONF_MEMORY_UPKEEP = "memory_upkeep"
CONF_MEMORY_TIME = "memory_time"
KEEP_DAYS = 60  # day stories kept
STALE_DAYS = 90  # a fact nobody has confirmed or used in this long is checked once
_FIX = re.compile(r"^\[?([0-9a-f]{8})\]?\s*[:\-–—]\s*(.+)$")
PRIVATE = ("password", "pin ", "card", "account number", "bank", "salary", "diagnos", "medication", "illness")


class Reflection:
    def __init__(self, hass: HomeAssistant, entry_id: str, options: dict[str, Any], rt_getter: Callable[[], Any]) -> None:
        self.hass = hass
        self.options = options
        self._rt = rt_getter
        self._store: Store[dict[str, Any]] = Store(hass, 1, f"{DOMAIN}.{entry_id}.reflection")
        self.last: dict[str, Any] | None = None
        self.days: list[dict[str, str]] = []  # {"date": "2026-11-22", "story": "..."}, oldest first
        self.asked_old: list[str] = []  # memory ids already checked with someone
        self._unsub = None
        self._busy = False
        self._stopped = False

    async def async_start(self) -> None:
        self._stopped = False
        data = await self._store.async_load() or {}
        self.last = data.get("last")
        if self.options.get(CONF_MEMORY_TIME):
            self.days = [d for d in data.get("days") or [] if isinstance(d, dict) and d.get("date") and d.get("story")][-KEEP_DAYS:]
            self.asked_old = list(data.get("asked_old") or [])[-500:]
        elif data.get("days"):
            self._keep(self.last or {})  # switched off: day stories already kept are deleted
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
        upkeep = bool(rt.options.get(CONF_MEMORY_UPKEEP))
        time_aware = bool(rt.options.get(CONF_MEMORY_TIME))
        recent_memories = rt.memory.recent(60)
        # with upkeep on, each memory carries its id, so one that today's conversations show is out of date can be
        # put right; ones a person confirmed (pinned or edited) are never offered for that
        known = [f"[{m.id}] {m.text}" if upkeep and m.basis != "confirmed" else m.text for m in recent_memories]
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
            + ("5. outdated: known memories (the ones with an [id]) that something a person said in today's "
               "conversations shows is now wrong or out of date — each as \"[id]: the corrected sentence\". Only "
               "when a person clearly said so, never from the log alone or a guess. Usually none.\n" if upkeep else "")
            + ("6. story: two to four plain sentences telling the story of the day the log covers, for answering "
               "'what did we do last Saturday?' or 'when did the boiler last play up?' weeks later — what people "
               "did, plans made, anything that went wrong or was fixed, visitors as 'someone came to the door' "
               "(never who), with times only where they matter. Nothing private (health, money, passwords). Never "
               "say the house was empty. Write it in the past tense.\n" if time_aware else "")
            + "Never say or imply the house is or was empty. "
            + f"Write the thoughts and the question in {write_in(self.hass, rt.options, spoken=True)}; write the "
            + f"memories, lessons, corrections and story in {write_in(self.hass, rt.options, spoken=False)}. "
            + personality_line(rt.options)
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
        if upkeep:
            data["structure"]["outdated"] = {"description": "[id]: corrected sentence, for memories shown out of date "
                                             "today (often none)", "required": False,
                                             "selector": {"text": {"multiple": True}}}
        if time_aware:
            data["structure"]["story"] = {"description": "The day's story in two to four sentences", "required": False,
                                          "selector": {"text": {}}}
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
        from .memory import one_off_event

        for text in _list(out.get("memories"))[:MAX_MEMORIES]:
            if any(p in text.lower() for p in PRIVATE):
                continue
            if one_off_event(text):
                continue  # 19 Nov: "Kayleigh's car left the drive at nine thirty-four this morning" — the timeline's
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
        if upkeep:
            result["fixed"] = self._fix_outdated(rt, _list(out.get("outdated")))
            if question := self._confirm_a_guess(rt):
                result["checking"] = question
        if time_aware:
            story = out.get("story")
            if isinstance(story, str) and story.strip() and len(story) <= 2000 and not any(p in story.lower() for p in PRIVATE):
                result["story"] = self.add_day(" ".join(story.split()))
            if question := self._check_an_old_fact(rt):
                result["checking_old"] = question
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
                f"put right {len(result['fixed'])} out-of-date memor{'y' if len(result['fixed']) == 1 else 'ies'}" if result.get("fixed") else "",
            ) if p
        )
        rt.timeline.add("mind", summary or "Thought over the day — nothing to add.", "overnight reflection")
        self._keep(result)
        return result

    @staticmethod
    def _fix_outdated(rt: Any, lines: list[str]) -> list[str]:
        """Put right memories today's conversations showed are out of date. A fix is a guess until confirmed."""
        fixed = []
        for line in lines[:MAX_FIXES]:
            m = _FIX.match(line.strip())
            if not m or any(p in line.lower() for p in PRIVATE):
                continue
            memory = rt.memory.get(m.group(1))
            text = m.group(2).strip()
            if memory is None or memory.basis == "confirmed" or not text or text == memory.text:
                continue
            old = memory.text
            if rt.memory.update(memory.id, text) is None:
                continue
            memory.origin, memory.source = None, "reflection"  # worked out, not told: a guess until confirmed
            rt.memory._note("replaced", old, memory.text)
            rt.memory._save()
            fixed.append(f"{old} → {memory.text}")
        return fixed

    @staticmethod
    def _confirm_a_guess(rt: Any) -> str | None:
        """One guess a day goes to curiosity to be asked about, so guesses become things the house knows."""
        day_old = (dt_util.now() - timedelta(days=1)).isoformat()
        for memory in sorted(rt.memory.items, key=lambda m: m.created):
            if memory.basis != "guess" or memory.created > day_old or memory.category == "correction":
                continue
            question = f"I worked out that {memory.text[0].lower()}{memory.text[1:].rstrip('.')} — is that right?"
            if rt.curiosity.wonder(question, why=f"checking a guess ({memory.id})", source="memory"):
                return question
        return None

    def _check_an_old_fact(self, rt: Any) -> str | None:
        """One fact a day that nobody has confirmed or used in three months is checked with someone, once."""
        cutoff = (dt_util.now() - timedelta(days=STALE_DAYS)).isoformat()
        for memory in sorted(rt.memory.items, key=lambda m: m.created):
            if (memory.pinned or memory.basis in ("confirmed", "told") or memory.category in ("correction", "note")
                    or memory.id in self.asked_old or memory.created > cutoff or (memory.used or "") > cutoff
                    or not rt.memory.live(memory)):
                continue
            question = f"A while ago I was told that {memory.text[0].lower()}{memory.text[1:].rstrip('.')} — is that still right?"
            if rt.curiosity.wonder(question, why=f"checking an old memory ({memory.id})", source="memory"):
                self.asked_old = (self.asked_old + [memory.id])[-500:]
                return question
            return None  # curiosity has enough to ask today
        return None

    def add_day(self, story: str, day: str | None = None) -> dict[str, str]:
        """Keep the story of a day (the day the overnight log was mostly about)."""
        when = day or (dt_util.now() - timedelta(hours=8)).date().isoformat()
        self.days = [d for d in self.days if d["date"] != when] + [{"date": when, "story": story}]
        self.days = sorted(self.days, key=lambda d: d["date"])[-KEEP_DAYS:]
        self._keep(self.last or {})
        return {"date": when, "story": story}

    def find_days(self, day: str | None = None, about: str | None = None, limit: int = 5) -> list[dict[str, str]]:
        """Day stories for a day ('yesterday', 'last Saturday', '2026-11-21', '3 days ago') and/or about something,
        newest first."""
        found = list(reversed(self.days))
        if day:
            wanted = parse_day(day)
            if wanted is None:
                return []
            found = [d for d in found if d["date"] == wanted.isoformat()]
        if about:
            words = {w for w in re.findall(r"[a-z0-9]+", about.lower()) if len(w) > 2}
            from .memory import _expand

            words = _expand(words)
            found = [d for d in found if words & set(re.findall(r"[a-z0-9]+", d["story"].lower()))]
        out = []
        for d in found[:limit]:
            when = datetime.fromisoformat(d["date"]).date() if len(d["date"]) == 10 else None
            out.append({"day": f"{when:%A} {when.day} {when:%B}" if when else d["date"], "story": d["story"]})
        return out

    def _keep(self, result: dict[str, Any]) -> None:
        self.last = result or None
        if not self.options.get(CONF_MEMORY_TIME):
            self.days, self.asked_old = [], []
        self._store.async_delay_save(lambda: {"last": self.last, "days": self.days, "asked_old": self.asked_old}, 2)


_WEEKDAYS = ["monday", "tuesday", "wednesday", "thursday", "friday", "saturday", "sunday"]


def parse_day(text: str, today: Any = None) -> Any:
    """'yesterday', 'last saturday', 'saturday', '3 days ago', '2026-11-21', '21 november' -> a date (or None)."""
    from datetime import date

    t = " ".join(text.lower().split())
    today = today or dt_util.now().date()
    if re.fullmatch(r"\d{4}-\d{2}-\d{2}", t):
        try:
            return date.fromisoformat(t)
        except ValueError:
            return None
    if t in ("today", "this morning", "earlier today"):
        return today
    if t in ("yesterday", "last night"):
        return today - timedelta(days=1)
    if m := re.fullmatch(r"(\d{1,2}|a|one|two|three|four|five|six|seven) days? ago", t):
        n = {"a": 1, "one": 1, "two": 2, "three": 3, "four": 4, "five": 5, "six": 6, "seven": 7}.get(m.group(1))
        return today - timedelta(days=n or int(m.group(1)))
    if m := re.fullmatch(r"(?:last |on )?(monday|tuesday|wednesday|thursday|friday|saturday|sunday)", t):
        back = (today.weekday() - _WEEKDAYS.index(m.group(1))) % 7 or 7
        return today - timedelta(days=back)
    if m := re.fullmatch(r"(?:the )?(\d{1,2})(?:st|nd|rd|th)? (?:of )?(january|february|march|april|may|june|july|"
                         r"august|september|october|november|december)", t):
        months = ["january", "february", "march", "april", "may", "june", "july", "august", "september", "october",
                  "november", "december"]
        try:
            when = date(today.year, months.index(m.group(2)) + 1, int(m.group(1)))
        except ValueError:
            return None
        return when if when <= today else date(today.year - 1, when.month, when.day)
    return None
