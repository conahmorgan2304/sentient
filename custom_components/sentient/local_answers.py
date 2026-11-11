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
COMING = re.compile(r"\b(next alarm|alarms?\b.*\b(set|tomorrow|morning)|reminders?|remind me (about|of) anything|"
                    r"what'?s coming up)\b", re.I)
VISITORS = re.compile(r"\b(anyone|anybody|someone)\b.*\b(come round|came round|been round|call(ed)? round|at the door|"
                      r"to the door|knock(ed)?|rang|ring the (bell|doorbell))\b|\bvisitors?\b|\bwho (came|called|rang)\b|"
                      r"\b(parcels?|delivery|deliveries)\b.*\b(come|came|left|arrived|been)\b", re.I)
LEAD = "My AI isn't answering right now, so this is straight from the house: "


def is_question(text: str) -> bool:
    from .sessions import COMMAND

    return bool(_QUESTION.search(text or "")) and not COMMAND.search(text or "")


def _coming_up(rt: Any) -> str | None:
    from .watches import local_when

    alarms = [a for a in (rt.alarms.listing() if getattr(rt, "alarms", None) else []) if a["on"] and a["next"]]
    reminders = []
    watches = getattr(rt, "watches", None)
    for w in (watches.watches() if watches is not None else []):
        if getattr(w, "kind", "") == "reminder":
            reminders.append(w)
    parts = []
    if alarms:
        first = alarms[0]
        when = dt_util.parse_datetime(first["next"])
        parts.append(f"your next alarm is {first['name']} at {local_when(when)}" if when else f"your next alarm is {first['name']}")
    if reminders:
        reminders.sort(key=lambda w: w.until_dt)
        nxt = reminders[0]
        more = f", and {len(reminders) - 1} more after that" if len(reminders) > 1 else ""
        parts.append(f"the next reminder is “{nxt.what}” at {local_when(nxt.until_dt)}{more}")
    if not parts:
        return "there are no alarms or reminders coming up."
    text = " and ".join(parts) + "."
    return text[0].upper() + text[1:]


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
            return LEAD + body
        if guest:
            return None  # alarms, reminders and visitors are household details
        if COMING.search(text):
            body = _coming_up(rt)
        elif VISITORS.search(text):
            body = _visitors(rt)
        else:
            return None
    except Exception:  # noqa: BLE001 - a local answer is a bonus; never stop the backup agent from trying
        return None
    return LEAD + body if body else None
