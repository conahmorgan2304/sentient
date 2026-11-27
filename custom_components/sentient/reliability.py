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
    r"\b(?:shall i|should i|do you want me to|would you like me to|want me to|i can|i could|i['’]ll|i will|let me)"
    r"\s+(?:(?:also|just|please|quickly)\s+)*"
    r"(?:feed|fetch|bring|carry|cook|make you|make a (?:cup|brew|drink|tea|coffee)|pour|tidy|clean up|water the|walk the|"
    r"let the \w+ (?:out|in)|top up|refill|go (?:and|out|outside|down|up)|pop (?:out|outside|down|up)|pick up|put (?:the )?\w+ away)\b",
    re.I)
# Explicit offers to change the house, not informational offers such as "shall I check".
ACTIONABLE_OFFER = re.compile(
    r"\b(?:shall i|should i|would you like me to|do you want me to|want me to)\s+"
    r"(?:please\s+)?(?:turn|switch|set|start|stop|open|close|lock|unlock|arm|disarm|"
    r"activate|deactivate|run|schedule|cancel|delete|add|remove|send|play|pause|"
    r"make|brew|adjust|change|enable|disable|remind|monitor|watch|keep (?:an eye|watching)|let you know)\b", re.I)


# Narrower than DONE: a current-state answer ("the light is now on") is
# not itself proof the assistant claims to have changed the device.
EXPLICIT_COMPLETION = re.compile(
    r"^(?:ok(?:ay)?[,. ]+)?(?:done|all set|sorted|all done)(?:[,.!:;]|$)|"
    r"\bi (?:just |successfully )?(?:turned|switched|set|started|stopped|opened|closed|locked|unlocked)\b|"
    r"\b(?:has|have) just been (?:turned|switched|set|started|stopped|opened|closed|locked|unlocked)\b|"
    r"\byour (?:alarm|reminder|schedule|routine) (?:has been|is now) (?:set|scheduled|created|cancelled)\b|"
    r"\bi(?:'ve|’ve| have) (?:(?:now|just|successfully) )?(?:turned|switched|set|started|stopped|"
    r"opened|closed|locked|unlocked|armed|disarmed|changed|activated|deactivated)\b", re.I)


# Specific voice commitments require a matching kind of successful tool, even
# when a different tool completed in the same turn.
_ACTION_CLAIMS = (
    (re.compile(r"\b(?:i(?:'ve|’ve| have| am|'m|’m)|i (?:will|shall)|i['’]ll)\s+(?:now |already )?"
                r"(?:watch(?:ing)?|monitor(?:ing)?|keep(?:ing)? (?:an eye|watching)|let you know)\b"
                r"|\b(?:monitoring|watch) (?:is |has been )?(?:active|enabled|started)\b", re.I),
     {"sentient_watch"}),
    (re.compile(r"\b(?:i(?:'ve|’ve| have)|i (?:will|shall)|i['’]ll)\s+(?:now |always )?"
                r"(?:learn(?:ed|t)?|rememb(?:er|ered)|save(?:d)? (?:your|that|this) preference)\b", re.I),
     {"sentient_remember", "sentient_correction"}),
    (re.compile(r"\b(?:i (?:will|shall)|i['’]ll) (?:make sure|ensure|avoid|stop|be less|be more|change|adjust)\b"
                r"|\b(?:i(?:'ve|’ve| have)|i) (?:now |just )?(?:changed|updated|adjusted|fixed)\b[^.!?]*"
                r"\b(?:notif\w+|preferences?|settings?|reminders?)\b", re.I),
     {"sentient_shush", "sentient_quiet"}),
    (re.compile(r"\b(?:i(?:'ve|’ve| have)|i) (?:now |just )?(?:created|changed|updated|enabled|disabled|deleted)\b"
                r"[^.!?]*\b(?:automation|rule)\b", re.I),
     {"sentient_create_automation", "sentient_make_rule"}),
    (re.compile(r"\b(?:i(?:'ve|’ve| have)|i) (?:now |just )?(?:created|set|scheduled)\b[^.!?]*\breminder\b"
                r"|\byour reminder (?:has been|is now) (?:set|scheduled|created)\b", re.I),
     {"sentient_remind"}),
)


def claimed_action_tools(text: str) -> tuple[set[str], ...]:
    """Each matched claim needs a success from at least one of its supporting tools."""
    return tuple(tools for pattern, tools in _ACTION_CLAIMS if pattern.search(text))


def explicit_completion(text: str) -> bool:
    text = " ".join((text or "").split())
    return bool(EXPLICIT_COMPLETION.search(text) or claimed_action_tools(text))


_UNRECORDED_EXPLANATION = re.compile(
    r"\bi(?: am| was| have been|['’]m)\s*(?:still )?(?:learning|trying to learn)"
    r"\b[^.!?]*\b(?:notifications?|reminders?|preferences?)\b"
    r"|\bi (?:mentioned|said|notified|told)\b[^.!?]*\bbecause\b[^.!?]*"
    r"\b(?:i (?:assumed|guessed|inferred)|my (?:memory|internal process)|my training)\b",
    re.I,
)


def unsupported_self_explanation(text: str) -> bool:
    """Detect common invented internal explanations; recorded explanations use their own path."""
    return bool(_UNRECORDED_EXPLANATION.search(text))


def actionable_offer(text: str) -> bool:
    """Recognise explicit English action offers without treating questions as consent."""
    if re.match(r"^\s*(?:which|what|when|where|who|how)\b", text or "", re.I):
        return False  # requests for missing detail are not offers
    return bool(ACTIONABLE_OFFER.search(text or "") or (re.search(r"\b(?:shall i|should i|want me to|i can|i could)\b", text or "", re.I) and OFFER.search(text or "")))


UNANSWERED = re.compile(
    r"\bi (?:don't|do not|didn't|did not) (?:know|have (?:any )?(?:information|a record|records|access|details|data|way))\b|"
    r"\bi (?:couldn't|could not|can't|cannot|wasn't able to|was unable to|am unable to|'m unable to|’m unable to) "
    r"(?:find|see|tell|check|access|look|say|confirm|verify|get)\b|\bno (?:record|records|information|data) (?:of|about|for)\b|"
    r"\bi'?m not (?:sure|able)\b", re.I)
# A camera can report seeing nobody. An unavailable frame or an unsuccessful history
# search still counts as uncertainty, including when it asks what to check next.
_CAMERA_NEGATIVE = re.compile(
    r"\bi (?:can['’]t|cannot) see (?:anyone|anybody|any people|anything moving)"
    r"(?: (?:about|around|there|right now|at the moment))*\s*(?=[.!?]|$)", re.I,
)
_CURRENT_CAMERA_READS = {"ask_camera", "sentient_look", "frigate_query"}


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
                  "snapshot", "clip", "recap", "camera_events", "frigate_query")


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


def tool_payload(content: dict[str, Any]) -> dict[str, Any]:
    result = content.get("result")
    if isinstance(result, dict) and isinstance(result.get("data"), dict):
        return result["data"]
    result = content.get("tool_result")
    return result if isinstance(result, dict) else {}


CAMERA_ERROR_REASONS = frozenset({
    "camera_unresolved", "camera_access", "camera_not_configured", "evidence_disabled", "evidence_unavailable",
})
TOOL_ERROR_REASONS = CAMERA_ERROR_REASONS | {"invalid_arguments", "timeout", "read_only", "tool_failed"}


def tool_error_reason(content: dict[str, Any]) -> str:
    """Keep a useful category without persisting provider messages, tool arguments or entity names."""
    result = tool_payload(content)
    reason = result.get("reason")
    if isinstance(reason, str) and reason in CAMERA_ERROR_REASONS:
        return reason
    error = str(result.get("error") or "").casefold()
    if error.startswith("wrong arguments") or error in ("invalid", "multipleinvalid", "vol.invalid", "validationerror", "invalidtoolarguments"):
        return "invalid_arguments"
    if "timeout" in error or "timed out" in error:
        return "timeout"
    if "read-only" in error:
        return "read_only"
    return "tool_failed"


def _tool_outcome(content: dict[str, Any]) -> tuple[str, str | None, bool]:
    """(tool name, what went wrong or None, whether it was a device action that did something)."""
    name = str(content.get("tool_name") or "").split("__")[-1]
    result = content.get("result", content.get("tool_result"))
    payload = tool_payload(content)
    if payload.get("error") or (isinstance(result, dict) and result.get("error")):
        return name, _short(str(payload.get("error") or "tool reported an error"), 140), False
    if payload.get("response_type") == "error" or payload.get("status") in ("failed", "error", "timed_out"):
        return name, "tool reported an error", False
    data = payload.get("data")
    if isinstance(data, dict) and ("success" in data or "failed" in data):
        bad = [str(t.get("name")) for t in data.get("failed") or [] if isinstance(t, dict) and t.get("name")]
        # A nonempty success list represents a reported successful target, not
        # independent verification of the physical state.
        success = data.get("success")
        ok = isinstance(success, list) and bool(success)
        if bad:
            return name, "didn't work: " + ", ".join(bad[:4]), ok
        return name, None, ok
    if payload.get("success") is True:
        # Explicit success is evidence that the tool reports completion. It is
        # not independent verification that the physical device changed.
        return name, None, True
    if payload.get("success") is False:
        return name, _short(str(payload.get("message") or payload.get("reason") or "failed"), 140), False
    return name, None, False


def action_succeeded(content: dict[str, Any], *, acting: bool = False) -> bool:
    """Check a real tool result for completion, rather than preparation or a read-only success."""
    name, error, changed = _tool_outcome(content)
    result = content.get("tool_result")
    if not isinstance(result, dict) or not result or result.get("read_back") or result.get("error"):
        return False
    status = str(result.get("status") or "").casefold()
    if status in NOT_DONE | {"awaiting_confirmation", "failed", "skipped", "attempted", "error"}:
        return False
    if any(key in result and not result[key] for key in ("done", "spoken", "cancelled", "forgotten")):
        return False
    data = result.get("data")
    response_type = result.get("response_type")
    if response_type in ("error", "query_answer"):
        return False
    # HA intent targets provide explicit evidence, including partial success.
    if isinstance(data, dict) and ("success" in data or "failed" in data):
        return changed
    if result.get("actions") and isinstance(result["actions"], list):
        return any(isinstance(row, dict) and row.get("status") == "verified" for row in result["actions"])
    if name == "sentient_quiet":
        return "hushed_until" in result or result.get("guest_mode_changed") is True
    if name == "sentient_shush":
        return bool((isinstance(result.get("shushed"), str) and "until" in result) or result.get("unshushed"))
    if name in ("sentient_watch", "sentient_remind"):
        recorded = result.get("watching" if name == "sentient_watch" else "reminder")
        return bool(isinstance(recorded, dict) and recorded.get("id"))
    return (acting or response_type == "action_done") and not error and result.get("success") is not False


def _unanswered(asked: str, said: str, tools: list[dict[str, Any]],
                outcomes: list[tuple[str, str | None, bool]]) -> bool:
    if not UNANSWERED.search(said):
        return False
    current = not needs_evidence(asked) and re.search(
        r"\b(?:camera|see|look|visible)\b", asked, re.I,
    )
    if current and _CAMERA_NEGATIVE.search(said) and any(
        name in _CURRENT_CAMERA_READS and not error and tool_payload(tools[i])
        for i, (name, error, _) in enumerate(outcomes)
    ):
        return bool(UNANSWERED.search(_CAMERA_NEGATIVE.sub("", said)))
    return True


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
    if said_clean and _unanswered(asked, said_clean, tools, outcomes):
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
