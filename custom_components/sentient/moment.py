"""Awareness of the moment: a short "right now" picture for every voice turn. No AI, nothing stored.

The voice already gets the time, the weather, the room it's in, the drive and what changed lately. What it lacked
was the joined-up picture a person in the house has without thinking:

* **who's about** — each person's home/away state and since when, so "is anyone in?" or "when did I get back?"
  are answered from the record, not guessed;
* **where there's movement now** — the rooms with recent movement, busiest first (pets and empty rooms aside,
  movement is the best sign of where people are);
* **what just happened** — the house's own timeline for the last 45 minutes (a visitor, the drive, an alarm);
* **what's unusual today** — changes the house's expectations marked as surprising for that time of day, and the
  inner life's note when nobody's up when someone usually is.

Everything comes from what Sentient already keeps. In a private room and in guest mode the personal parts (people,
the timeline, the unusual) are left out; movement is about rooms, so it stays. Off: nothing is added.
"""

from __future__ import annotations

from datetime import timedelta
from typing import Any

from homeassistant.core import HomeAssistant
from homeassistant.helpers import area_registry as ar
from homeassistant.util import dt as dt_util

CONF_MOMENT = "moment_awareness"
JUST_NOW = timedelta(minutes=45)
# timeline kinds that are the house talking to itself, not something that happened
NOT_EVENTS = {"mind", "health", "learned", "loop", "settings", "ai", "selftest", "held", "digest", "said", "reminder", "briefing"}
MAX_EVENTS = 6
MAX_UNUSUAL = 4


def _when(when) -> str:
    """'17:42' today, 'yesterday 22:10', or 'Friday 09:05' — always local and to the minute."""
    local = dt_util.as_local(when)
    today = dt_util.now().date()
    if local.date() == today:
        return local.strftime("%H:%M")
    if local.date() == today - timedelta(days=1):
        return "yesterday " + local.strftime("%H:%M")
    return local.strftime("%A %H:%M")


def people(hass: HomeAssistant) -> list[str]:
    out = []
    for st in hass.states.async_all("person"):
        if st.state in ("unknown", "unavailable"):
            continue
        where = "home" if st.state == "home" else "out" if st.state == "not_home" else f"at {st.state}"
        out.append(f"{st.name} {where} since {_when(st.last_changed)}")
    return out


def movement(hass: HomeAssistant, rt: Any) -> list[str]:
    tracker = getattr(rt, "tracker", None)
    if tracker is None:
        return []
    areas = ar.async_get(hass)
    names = []
    for area_id in tracker.occupied_areas()[:4]:
        area = areas.async_get_area(area_id)
        names.append(area.name if area else area_id)
    return names


def just_happened(rt: Any) -> list[str]:
    timeline = getattr(rt, "timeline", None)
    if timeline is None:
        return []
    out = []
    for item in timeline.since(JUST_NOW.total_seconds() / 3600, limit=30):
        if item.get("kind") in NOT_EVENTS or not item.get("text"):
            continue
        when = dt_util.parse_datetime(item["time"])
        out.append(f"{_when(when) if when else ''} {item['text']}".strip())
        if len(out) >= MAX_EVENTS:
            break
    return out


def unusual_today(hass: HomeAssistant, rt: Any) -> list[str]:
    out = []
    brain = getattr(rt, "brain", None)
    expect = getattr(brain, "expect", None)
    if expect is not None and rt.options.get("expectations", True):
        for s in reversed(expect.score()["surprises"][-MAX_UNUSUAL:]):
            state = hass.states.get(s["entity"])
            name = s.get("name") or (state.name if state else s["entity"])
            out.append(f"{name} went {s['new']} at {_when(dt_util.utc_from_timestamp(s['ts']))}")
    inner = getattr(rt, "inner", None)
    if inner is not None:
        today = dt_util.now().date()
        for t in getattr(inner, "thoughts", []):
            if t.get("kind") == "day" and dt_util.as_local(dt_util.utc_from_timestamp(t["t"])).date() == today:
                out.append(t["text"])
    return out[:MAX_UNUSUAL + 1]


def lines(hass: HomeAssistant, rt: Any, options: dict[str, Any], *, private: bool, guest: bool) -> list[str]:
    """The 'right now' lines for the voice ([] when switched off or there's nothing to say)."""
    if not options.get(CONF_MOMENT):
        return []
    out: list[str] = []
    personal = not private and not guest
    if personal and (who := people(hass)):
        out.append("Who's about (from Home Assistant's people): " + "; ".join(who) + ".")
    if rooms := movement(hass, rt):
        out.append("Movement in the last few minutes, busiest first: " + ", ".join(rooms)
                   + ". Movement can be a pet; it doesn't say who.")
    if personal and (events := just_happened(rt)):
        out.append("Just happened (the house's own record, last 45 minutes): " + "; ".join(events) + ".")
    if personal and (odd := unusual_today(hass, rt)):
        out.append("Unusual for today (compared with what normally happens at these times): " + "; ".join(odd)
                   + ". Mention one only if it's relevant to what's asked.")
    if out:
        out.insert(0, "Right now — use this to answer naturally, without reading it out, and check with a tool "
                      "before relying on anything older than a few minutes:")
    return out
