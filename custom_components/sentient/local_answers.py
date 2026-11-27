"""Answers to a few everyday questions without any AI, for when the AI voice agent has failed.

Home Assistant's own backup agent already handles lights, heating, timers and the time, but it can't answer
"is the house locked up?", "when's my next alarm?" or "did anyone come round?". Those come from Sentient's own
records, so they can be answered here, in plain words, before the request is handed to the backup agent.

Only questions are answered (never anything that could be a command), only from data the person may see (the
security check is filtered by their Home Assistant permissions), and in guest mode only the security question.
Switched off with "Answer common questions without the AI" (Settings → Voice backup).
"""
from __future__ import annotations

import re
from typing import Any

from homeassistant.core import Context, HomeAssistant
from homeassistant.util import dt as dt_util

CONF_OFFLINE_ANSWERS = "offline_answers"

_QUESTION = re.compile(r"^\s*(is|are|was|were|did|do|does|have|has|any|anything|anyone|anybody|what|what's|whats|when|"
                       r"when's|who|which|how|can you tell)\b|\?\s*$", re.I)
SECURITY = re.compile(r"\b(locked up|lock(ed)? up|all locked|(door|doors|window|windows|gate|garage)\b.*\b(open|locked|shut|closed)|"
                      r"(left|leave) (a|any|the)? ?(door|window|gate)s?\b.*open|anything (left )?open|house (secure|safe)|"
                      r"(everything|all|we) (is |are )?(secure|safe))\b", re.I)
_SCHEDULE_ITEMS = r"(?:(?:personal )?reminders?(?: and (?:scheduled )?alarms?)?|(?:scheduled )?alarms?(?: and (?:personal )?reminders?)?)"
COMING = re.compile(
    rf"^(?:can you tell me |tell me )?(?:"
    rf"(?:what|which) (?:{_SCHEDULE_ITEMS})(?: (?:have i (?:got|set)|do i have|are (?:set|coming up)))?"
    rf"|(?:what|which|when)(?: is| are|'s) (?:my |the |our )?(?:next |scheduled |personal )?{_SCHEDULE_ITEMS}"
    rf"|(?:do i have|have i got|are there|any)(?: any)? {_SCHEDULE_ITEMS}"
    r"|what(?:'s| is) coming up|what do i have coming up)[?.!]*$", re.I)
VISITORS = re.compile(r"\b(anyone|anybody|someone)\b.*\b(come round|came round|been round|call(ed)? round|at the door|"
                      r"to the door|knock(ed)?|rang|ring the (bell|doorbell))\b|\bvisitors?\b|\bwho (came|called|rang)\b|"
                      r"\b(parcels?|delivery|deliveries)\b.*\b(come|came|left|arrived|been)\b", re.I)
LEAD = "My AI isn't answering right now, so this is straight from the house: "


def is_question(text: str) -> bool:
    from .sessions import COMMAND

    text = (text or "").strip()
    return bool(_QUESTION.search(text) or COMING.fullmatch(text)) and not COMMAND.search(text)


def _coming_up(rt: Any, question: str = "") -> str | None:
    from .watches import local_when

    wants_reminders = bool(re.search(r"\breminders?\b", question, re.I))
    wants_alarms = bool(re.search(r"\balarms?\b", question, re.I))
    parts = []
    if wants_alarms or not wants_reminders:
        alarms = getattr(rt, "alarms", None)
        upcoming = [a for a in (alarms.listing() if alarms else ()) if a["on"] and a["next"]]
        if upcoming:
            plural = bool(re.search(r"\balarms\b", question, re.I))
            listed = []
            for alarm in upcoming if plural else upcoming[:1]:
                when = dt_util.parse_datetime(alarm["next"])
                listed.append(alarm["name"] + (f" at {local_when(when)}" if when else ""))
            parts.append(("your alarms are " if len(listed) > 1 else "your next alarm is ") + "; ".join(listed))
        elif wants_alarms:
            parts.append("you have no alarms coming up")
    if wants_reminders or not wants_alarms:
        watches = getattr(getattr(rt, "watches", None), "watches", ())
        watches = watches() if callable(watches) else watches
        reminders = sorted((w for w in watches if w.kind == "reminder"), key=lambda w: w.until_dt)
        if reminders:
            listed = [f"“{w.what}” at {local_when(w.until_dt)}" for w in reminders]
            parts.append("your " + ("reminder is " if len(listed) == 1 else "reminders are ") + "; ".join(listed))
        elif wants_reminders:
            parts.append("you have no reminders set")
    text = " and ".join(parts) if parts else "there are no alarms or reminders coming up"
    return text[0].upper() + text[1:] + "."


def _visitors(rt: Any) -> str | None:
    visitors = getattr(rt, "visitors", None)
    if visitors is None:
        return None
    today = visitors.today()
    if not today:
        return "nobody has been logged at the door today."
    lines = visitors.summary(limit=min(3, len(today)))
    more = f" ({len(today)} visits today in all)" if len(today) > 3 else ""
    return "; ".join(lines) + more + "."


async def async_answer(hass: HomeAssistant, rt: Any, text: str, context: Context | None) -> str | None:
    """A plain answer, or None to let the backup agent try."""
    if rt is None or not rt.options.get(CONF_OFFLINE_ANSWERS, True) or not is_question(text):
        return None
    guest = bool(getattr(getattr(rt, "speech", None), "guest_mode", False))
    try:
        if SECURITY.search(text):
            from .security import check_for_voice

            report = await check_for_voice(hass, context)
            verdict = (report.get("assessment") or {}).get("verdict")
            body = report["summary"] if verdict == "problem" else report.get("offline_explanation") or report["summary"]
            if (brain := getattr(rt, "brain", None)) is not None:
                brain.count("local")
            return LEAD + body
        if guest:
            return None  # alarms, reminders and visitors are household details
        if COMING.search(text):
            body = _coming_up(rt, text)
        elif VISITORS.search(text):
            body = _visitors(rt)
        else:
            return None
    except Exception:  # noqa: BLE001 - a local answer is a bonus; never stop the backup agent from trying
        return None
    if not body:
        return None
    if (brain := getattr(rt, "brain", None)) is not None:
        brain.count("local")  # answered from the house, no AI
    return LEAD + body
