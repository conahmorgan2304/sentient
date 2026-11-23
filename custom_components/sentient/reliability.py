"""Reliability: where the voice went wrong, from what it actually did, so it can be measured and improved.

Every voice turn that uses Sentient is checked with plain rules (no AI): what was asked, which tools the voice
used, what they returned, and what it said back. A turn is noted when:

* a tool failed (wrong arguments, an error), and whether a retry put it right;
* a device didn't do what it was asked;
* it said something was done when no action in that turn succeeded;
* it offered something no device can do (feeding the rabbits, making a drink);
* it couldn't answer ("I don't know", "I couldn't find");
* it answered a question about what happened without looking anything up.

The Mind page shows the counts and the last few, and the voice's turns overall, so a change can be judged by
whether these go down. Only a short line of the question and answer is kept, for 14 days, and it can be cleared;
switching it off stops it and clears what was kept. The house testing itself overnight isn't counted.
"""

from __future__ import annotations

from datetime import timedelta
import re
from typing import Any

from homeassistant.core import HomeAssistant, callback
from homeassistant.helpers.storage import Store
from homeassistant.util import dt as dt_util

from .const import CONF_RELIABILITY_LOG, DOMAIN

KEEP_DAYS = 14
KEEP_MAX = 200
SAVE_DELAY = 30

KINDS = {
    "tool_failed": "A tool failed",
    "action_failed": "A device didn't do it",
    "claimed_done": "Said it was done, but nothing was done",
    "unverified": "Reported completion without a recognised success result",
    "offered_impossible": "Offered something no device can do",
    "unanswered": "Couldn't answer",
    "no_evidence": "Answered about the past without looking",
    "wrong_state": "Said something was on, off, open or closed when it wasn't",
}

# "I've turned off…", "Done.", "All set": a claim that something happened
DONE = re.compile(
    r"^(?:ok(?:ay)?[,. ]+)?(?:done|all set|sorted|all done)\b|\bi(?:'ve|’ve| have) (?:now )?(?:turned|switched|set|started|"
    r"stopped|opened|closed|locked|unlocked|added|put|sent|told|announced|paused|played|dimmed|brightened|activated|"
    r"deactivated|armed|disarmed|changed|moved|muted)\b|\b(?:is|are) now (?:on|off|open|closed|locked|unlocked|playing|set)\b",
    re.I)
# an offer to do something with hands: no device in the house can
OFFER = re.compile(
    r"\b(?:shall i|should i|do you want me to|would you like me to|want me to|i can|i could|i'll|i will|let me)\b[^.?!]{0,40}?"
    r"\b(?:feed|fetch|bring|carry|cook|make you|make a (?:cup|brew|drink|tea|coffee)|pour|tidy|clean up|water the|walk the|"
    r"let the \w+ (?:out|in)|top up|refill|go (?:and|out|outside|down|up)|pop (?:out|outside|down|up)|pick up|put (?:the )?\w+ away)\b",
    re.I)
UNANSWERED = re.compile(
    r"\bi (?:don't|do not|didn't|did not) (?:know|have (?:any )?(?:information|a record|records|access|details|data|way))\b|"
    r"\bi (?:couldn't|could not|can't|cannot|wasn't able to|was unable to|am unable to|'m unable to|’m unable to) "
    r"(?:find|see|tell|check|access|look|say|confirm|get)\b|\bno (?:record|records|information|data) (?:of|about|for)\b|"
    r"\bi'?m not (?:sure|able)\b", re.I)
QUESTION = re.compile(r"\?\s*$|^(?:has|have|had|did|was|were|when|who|what|which|how (?:long|many|often)|is there|are there)\b", re.I)
PAST = re.compile(r"\b(?:has|have|had|did|was|were|last|earlier|yesterday|today|tonight|this (?:morning|afternoon|evening)|"
                  r"recently|ago|been|happened|overnight|previously)\b", re.I)

# a question about the conversation or about the voice's own doing ("What did you say?", "Was you not watching the
# gate camera?"): answered from what was said, not from the house's records (22 Nov: logged as not looking)
ABOUT_ITSELF = re.compile(
    r"\b(?:what|did|do|were|was|have|had)\s+you\s+(?:not\s+|just\s+|actually\s+)?(?:say|said|mean|meant|ask|asked|"
    r"tell|told|understand|watching|listening|doing)\b|\bwhat (?:did|was it) i (?:just )?(?:say|said|ask|asked)\b", re.I)
# tool names that look the answer up in a record of the past
EVIDENCE_NAMES = ("history", "timeline", "what_happened", "camera_activity", "visitors", "incidents", "root_cause",
                  "conversations", "sentient_why", "logbook", "recorder", "security_event", "evidence", "HassGetHistory",
                  # 17 Nov: "What was the next birthday on my calendar?" read the calendar but was logged as
                  # answering without looking; "What did you say?" is answered from what was said
                  "calendar", "said", "spoken",
                  # 22 Nov: "What did the person look like at 6:14?" looked at a saved picture
                  "snapshot", "clip", "recap", "camera_events")


def needs_evidence(asked: str) -> bool:
    """A question about something that happened, which should be looked up rather than answered from memory."""
    asked = (asked or "").strip()
    return bool(asked and QUESTION.search(asked) and PAST.search(asked) and not ABOUT_ITSELF.search(asked))


def is_evidence(name: str) -> bool:
    return any(word.casefold() in (name or "").casefold() for word in EVIDENCE_NAMES)


# a tool result that says it hasn't happened yet
NOT_DONE = {"pending", "queued", "accepted", "unverified", "waiting", "scheduled_retry"}


def _short(text: str, limit: int) -> str:
    text = " ".join((text or "").split())
    return text if len(text) <= limit else text[: limit - 1].rstrip() + "…"


def _tool_outcome(content: dict[str, Any]) -> tuple[str, str | None, bool]:
    """(tool name, what went wrong or None, whether it was a device action that did something)."""
    name = str(content.get("tool_name") or "").split("__")[-1]
    result = content.get("tool_result")
    if isinstance(result, dict) and result.get("error"):
        return name, _short(str(result["error"]), 140), False
    data = result.get("data") if isinstance(result, dict) else None
    if isinstance(data, dict) and ("success" in data or "failed" in data):
        bad = [str(t.get("name")) for t in data.get("failed") or [] if isinstance(t, dict) and t.get("name")]
        # A nonempty success list represents a reported successful target, not
        # independent verification of the physical state.
        success = data.get("success")
        ok = isinstance(success, list) and bool(success)
        if bad:
            return name, "didn't work: " + ", ".join(bad[:4]), ok
        return name, None, ok
    if isinstance(result, dict) and result.get("success") is True:
        # Explicit success is evidence that the tool reports completion. It is
        # not independent verification that the physical device changed.
        return name, None, True
    if isinstance(result, dict) and result.get("success") is False:
        return name, _short(str(result.get("message") or result.get("reason") or "failed"), 140), False
    return name, None, False


def classify(asked: str, said: str, tools: list[dict[str, Any]], actions: frozenset[str] = frozenset()) -> list[dict[str, str]]:
    """The reliability problems in one turn, by plain rules. ``tools`` is each tool result as the chat log has it;
    ``actions`` are tool names that change something (a successful one counts as having done something)."""
    out: list[dict[str, str]] = []
    outcomes = [_tool_outcome(t) for t in tools]
    # Sentient's own action tools (``actions``) report a failure as an error, so one that comes back without an
    # error or a "not done yet" status did what it was asked. 16 Nov: 1.11.11 stopped counting them, and every
    # reminder, watch or announcement that worked was logged as a voice problem. Home Assistant's own intents still
    # need a named success target, and anything else needs an explicit success.
    def sentient_done(index: int, name: str, err: str | None) -> bool:
        if name not in actions or err:
            return False
        result = tools[index].get("tool_result")
        status = str(result.get("status") or "").casefold() if isinstance(result, dict) else ""
        return status not in NOT_DONE and not (isinstance(result, dict) and result.get("done") is False)

    did = any(changed or sentient_done(i, name, err) for i, (name, err, changed) in enumerate(outcomes))
    for i, (name, err, _changed) in enumerate(outcomes):
        if not err:
            continue
        if err.startswith("didn't work"):
            out.append({"kind": "action_failed", "detail": f"{name}: {err}"})
            continue
        retried = any(n == name and not e for n, e, _c in outcomes[i + 1:])
        out.append({"kind": "tool_failed", "detail": f"{name}: {err}" + (" (put right on a retry)" if retried else "")})
    said_clean = " ".join((said or "").split())
    if said_clean and not did and DONE.search(said_clean):
        # A Sentient action tool can have its own success contract (watch IDs,
        # reminder IDs, etc). Don't call such a response a failed action.
        # Keep it distinct from a turn in which no action tool ran at all.
        # an action ran without an error but isn't known to have finished ("pending"); if every action errored,
        # saying it was done is a plain false claim
        attempted_action = any(name in actions and not err for name, err, _changed in outcomes)
        out.append({"kind": "unverified" if attempted_action else "claimed_done",
                    "detail": "action outcome not verified" if attempted_action else "no action in this turn succeeded"})
    if said_clean and (m := OFFER.search(said_clean)):
        out.append({"kind": "offered_impossible", "detail": f"\"{_short(m.group(0), 80)}\""})
    if said_clean and UNANSWERED.search(said_clean):
        out.append({"kind": "unanswered", "detail": "used " + (", ".join(dict.fromkeys(n for n, _e, _c in outcomes)) or "no tools")})
    elif needs_evidence(asked):
        # A generic device command is not evidence about prior events. This
        # deliberately checks tool relevance, not just whether any tool ran.
        def relevant_evidence(index: int, name: str, error: str | None) -> bool:
            if error or not is_evidence(name):
                return False
            if name == "sentient_evidence":
                result = tools[index].get("tool_result") or {}
                if isinstance(result, dict):
                    payload = result.get("data") if isinstance(result.get("data"), dict) else result
                    sources = payload.get("sources") or {}
                    return isinstance(sources, dict) and any(
                        isinstance(value, dict) and value.get("available") is True
                        for value in sources.values())
                return False
            return True

        if not any(relevant_evidence(i, name, err)
                   for i, (name, err, _changed) in enumerate(outcomes)):
            out.append({"kind": "no_evidence", "detail": "no relevant historical tool was used"})
    return out


class Reliability:
    """The voice's mistakes and near-misses, turn by turn, kept briefly for the Mind page."""

    def __init__(self, hass: HomeAssistant, entry_id: str, options: dict[str, Any]) -> None:
        self.hass = hass
        self.options = options
        self._store: Store[dict[str, Any]] = Store(hass, 1, f"{DOMAIN}.{entry_id}.reliability")
        self.records: list[dict[str, Any]] = []  # newest first
        self.turns: list[str] = []  # when each checked turn happened, for a rate
        self._turn: dict[str, dict[str, Any]] = {}  # conversation id -> the turn being written
        self.actions: frozenset[str] = frozenset()

    @property
    def enabled(self) -> bool:
        return bool(self.options.get(CONF_RELIABILITY_LOG, True))

    async def async_start(self) -> None:
        data = await self._store.async_load() or {}
        if not self.enabled:
            if data:
                await self._store.async_remove()
            return
        self.records = data.get("records", [])
        self.turns = data.get("turns", [])
        self._trim()
        if self.rejudge():
            self._save()

    def rejudge(self) -> int:
        """Look again at kept "answered about the past without looking" notes with today's rules, so a checker fix
        counts on the Mind page straight away rather than after 7 days. Only that note is judged again, from the
        question and the tool names kept; any other problem in the turn stands. Returns how many were cleared."""
        cleared = 0
        for record in list(self.records):
            problems = record.get("problems") or []
            if not any(p.get("kind") == "no_evidence" for p in problems):
                continue
            if needs_evidence(record.get("asked", "")) and not any(
                    is_evidence(t) and t != "sentient_evidence" for t in record.get("tools") or []):
                continue
            record["problems"] = [p for p in problems if p.get("kind") != "no_evidence"]
            cleared += 1
            if not record["problems"]:
                self.records.remove(record)
        return cleared

    @callback
    def async_stop(self) -> None:
        if self.enabled:
            self._store.async_delay_save(lambda: {"records": self.records, "turns": self.turns}, 0)

    def update_options(self, options: dict[str, Any]) -> None:
        self.options = options
        if not self.enabled:
            self.clear()

    @callback
    def clear(self) -> None:
        self.records, self.turns, self._turn = [], [], {}
        self._store.async_delay_save(lambda: {"records": [], "turns": []}, 1)

    @callback
    def _save(self) -> None:
        self._store.async_delay_save(lambda: {"records": self.records, "turns": self.turns}, SAVE_DELAY)

    def _trim(self) -> None:
        cutoff = (dt_util.utcnow() - timedelta(days=KEEP_DAYS)).isoformat()
        self.records = [r for r in self.records if r["time"] >= cutoff][:KEEP_MAX]
        self.turns = [t for t in self.turns if t >= cutoff][-5000:]

    # ----- fed by the conversation recorder ------------------------------------------------
    @callback
    def asked(self, conversation_id: str, text: str) -> None:
        if self.enabled:
            self._turn[conversation_id] = {"asked": text, "tools": []}

    @callback
    def tool(self, conversation_id: str, content: dict[str, Any]) -> None:
        if self.enabled and (turn := self._turn.get(conversation_id)) is not None:
            turn["tools"].append(content)

    @callback
    def answered(self, conversation_id: str, text: str, room: str | None = None,
                 device_id: str | None = None) -> None:
        turn = self._turn.pop(conversation_id, None)
        if not self.enabled or turn is None:
            return
        now_dt = dt_util.utcnow()
        now = now_dt.isoformat()
        key = (" ".join(str(turn["asked"]).split()).casefold(), " ".join(str(text or "").split()).casefold())
        last = getattr(self, "_last", None)
        # Only collapse a likely relay: one record carries actual tool results,
        # the other carries none. Two complete requests are separate turns.
        relay_pair = last is not None and bool(turn["tools"]) != bool(last["tools"])
        same_room = last is not None and last.get("room") == room
        compatible_device = last is not None and (not device_id or not last.get("device_id")
                                                   or device_id == last["device_id"])
        if relay_pair and same_room and compatible_device and last["key"] == key and (now_dt - last["at"]).total_seconds() < 15:
            # the same question and answer again within seconds: one request seen twice — a voice add-on (voice
            # recognition) passes it to this agent and then relays the answer in its own conversation. Count it
            # once, keeping whichever copy shows the tools that were used.
            if turn["tools"] and not last["tools"]:
                if last["record"] is not None and last["record"] in self.records:
                    self.records.remove(last["record"])
                last["record"] = self._record(now, room, turn, text)
                last["tools"] = True
                self._trim()
                self._save()
            return
        self.turns.append(now)
        self._last = {"key": key, "at": now_dt, "tools": bool(turn["tools"]),
                      "record": self._record(now, room, turn, text), "room": room, "device_id": device_id}
        self._trim()
        self._save()

    def _record(self, now: str, room: str | None, turn: dict[str, Any], text: str) -> dict[str, Any] | None:
        found = classify(turn["asked"], text, turn["tools"], self.actions)
        if not found:
            return None
        record = {"time": now, "room": room, "asked": _short(turn["asked"], 120), "said": _short(text, 160),
                  "tools": list(dict.fromkeys(_tool_outcome(t)[0] for t in turn["tools"]))[:6], "problems": found}
        self.records.insert(0, record)
        return record

    def add_graded(self, *, time: str, room: str | None, asked: str, said: str, detail: str) -> bool:
        """A mistake found later (the nightly check of what the voice said). False if it was already recorded."""
        if not self.enabled:
            return False
        short = _short(said, 160)
        if any(r["time"] == time and r["said"] == short for r in self.records):
            return False
        self.records.insert(0, {"time": time, "room": room, "asked": _short(asked, 120), "said": short, "tools": [],
                                "problems": [{"kind": "wrong_state", "detail": detail}], "graded": True})
        self.records.sort(key=lambda r: r["time"], reverse=True)
        self._trim()
        self._save()
        return True

    @callback
    def forget(self, conversation_id: str) -> None:
        self._turn.pop(conversation_id, None)

    # ----- reading ---------------------------------------------------------------------------
    def summary(self, days: float = 7) -> dict[str, Any]:
        if not self.enabled:
            return {"enabled": False}
        cutoff = (dt_util.utcnow() - timedelta(days=days)).isoformat()
        recent = [r for r in self.records if r["time"] >= cutoff]
        turns = sum(1 for t in self.turns if t >= cutoff)
        counts = {k: 0 for k in KINDS}
        for r in recent:
            for p in r["problems"]:
                counts[p["kind"]] = counts.get(p["kind"], 0) + 1
        clean = turns - len(recent)
        return {
            "enabled": True, "days": days, "turns": turns, "clean_turns": max(clean, 0),
            "clean_rate": round(clean / turns, 2) if turns else None,
            "counts": [{"kind": k, "label": KINDS[k], "count": n} for k, n in counts.items() if n],
            "recent": [{**r, "problems": [{**p, "label": KINDS.get(p["kind"], p["kind"])} for p in r["problems"]]}
                       for r in recent[:10]],
        }
