"""Know who's where: one best guess per person at home, from everything that knows a bit.

Three sources, strongest first:
* **heard** — voice recognition named them in the last few minutes, in the room the speaker is in;
* **phone** — a room tracker (Bermuda and the like) puts their phone in a room;
* **movement** — they're the only person home and there's been movement in one room lately.

Each person home gets a room (or "not sure"), how sure ("sure", "probably", "a guess") and why. People who aren't
home aren't mentioned at all — the voice never says who's out or that the house is empty. Nothing is stored:
it's worked out when the voice asks. Setting: "Know who's where" (off by default).
"""
from __future__ import annotations

from datetime import datetime, timedelta
from typing import Any

from homeassistant.core import HomeAssistant
from homeassistant.helpers import area_registry as ar
from homeassistant.util import dt as dt_util

CONF_WHO_WHERE = "who_where"
HEARD_FRESH = timedelta(minutes=5)
MATCH_TURN = timedelta(seconds=90)  # a recognised voice belongs to the conversation within this


def _people_home(hass: HomeAssistant) -> list[Any]:
    return [st for st in hass.states.async_all("person") if st.state == "home"]


def _heard(hass: HomeAssistant, rt: Any, now: datetime) -> dict[str, tuple[str, datetime]]:
    """name (casefolded) -> (room, when) for voices recognised in the last few minutes."""
    from .voice_id import RECENT_KEY

    out: dict[str, tuple[str, datetime]] = {}
    recent = list(hass.data.get(RECENT_KEY) or [])
    episodes = rt.conversations.recent(hours=1, limit=20) if getattr(rt, "conversations", None) else []
    for heard in recent:
        name, at = heard.get("name"), dt_util.parse_datetime(str(heard.get("at") or ""))
        if not name or at is None or now - at > HEARD_FRESH or name.casefold() in out:
            continue
        for ep in episodes:
            last = dt_util.parse_datetime(str(ep.get("last") or ep.get("start") or ""))
            start = dt_util.parse_datetime(str(ep.get("start") or ""))
            if ep.get("room") and last and start and start - MATCH_TURN <= at <= last + MATCH_TURN:
                out[name.casefold()] = (str(ep["room"]), at)
                break
    return out


def _phones(hass: HomeAssistant, options: dict[str, Any]) -> dict[str, tuple[str, datetime]]:
    """name (casefolded) -> (room, since) from room trackers whose owner is a person."""
    from .people import _owner_of_tracker
    from .room_trackers import CONF_ROOM_TRACKERS, CONF_ROOM_TRACKING, NOT_A_ROOM, _area_for

    if not options.get(CONF_ROOM_TRACKING):
        return {}
    areas = ar.async_get(hass)
    out: dict[str, tuple[str, datetime]] = {}
    for tracker in options.get(CONF_ROOM_TRACKERS) or []:
        state = hass.states.get(tracker)
        if state is None or str(state.state).casefold() in NOT_A_ROOM:
            continue
        owner = _owner_of_tracker(hass, tracker)
        area_id = _area_for(hass, state)
        area = areas.async_get_area(area_id) if area_id else None
        if owner and area and owner.casefold() not in out:
            out[owner.casefold()] = (area.name, state.last_changed)
    return out


def _movement(hass: HomeAssistant, rt: Any) -> str | None:
    tracker = getattr(rt, "tracker", None)
    if tracker is None:
        return None
    area_id = tracker.best_area(candidates=None)
    area = ar.async_get(hass).async_get_area(area_id) if area_id else None
    return area.name if area else None


def _ago(now: datetime, when: datetime) -> str:
    minutes = int((now - when).total_seconds() // 60)
    if minutes < 1:
        return "just now"
    if minutes < 60:
        return f"{minutes} minute{'s' if minutes != 1 else ''} ago"
    hours = minutes // 60
    return f"{hours} hour{'s' if hours != 1 else ''} ago"


def who_where(hass: HomeAssistant, rt: Any) -> list[dict[str, Any]]:
    """[{name, room, sure, why}] for each person at home ([] when the setting is off)."""
    options = rt.options
    if not options.get(CONF_WHO_WHERE):
        return []
    now = dt_util.utcnow()
    home = _people_home(hass)
    heard, phones = _heard(hass, rt, now), _phones(hass, options)
    # movement says who only when everyone else is known to be out: with one person set up in Home Assistant,
    # the rest of the household could be the ones moving
    everyone = hass.states.async_all("person")
    moving = _movement(hass, rt) if len(home) == 1 and len(everyone) >= 2 else None
    out = []
    for person in home:
        key = (person.name or "").casefold()
        if key in heard:
            room, at = heard[key]
            out.append({"name": person.name, "room": room, "sure": "sure", "why": f"heard talking there {_ago(now, at)}"})
        elif key in phones:
            room, since = phones[key]
            out.append({"name": person.name, "room": room, "sure": "probably",
                        "why": f"their phone has been there since {_ago(now, since)}"})
        elif moving:
            out.append({"name": person.name, "room": moving, "sure": "a guess",
                        "why": "the only one home, and there's been movement there"})
        else:
            out.append({"name": person.name, "room": None, "sure": "not sure", "why": "nothing places them"})
    return out


def prompt_line(hass: HomeAssistant, rt: Any) -> str | None:
    people = who_where(hass, rt)
    if not people:
        return None
    parts = [f"{p['name']}: {p['room']} ({p['sure']} — {p['why']})" if p["room"] else f"{p['name']}: can't tell where"
             for p in people]
    return ("Who's where, for those at home (best guess; say how sure you are, and if you can't tell, say so): "
            + "; ".join(parts) + ". People not listed: say nothing about where they are — never say anyone is out "
            "or that the house is empty.")
