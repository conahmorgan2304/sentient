"""What each Settings-page list looks like: its fields (Home Assistant form selectors), labels,
defaults and a one-line summary. The panel draws the editor from this, so adding a field
here is all it takes."""

from __future__ import annotations

from datetime import datetime
import random
import re
from typing import Any

DAYS = ["mon", "tue", "wed", "thu", "fri", "sat", "sun"]
DAY_NAMES = {"mon": "Mon", "tue": "Tue", "wed": "Wed", "thu": "Thu", "fri": "Fri", "sat": "Sat", "sun": "Sun"}


def parse_windows(text: str | None) -> list[tuple[int, int]]:
    """'08:00-10:00, 14:30-16:30' -> [(480, 600), (870, 990)]. Empty means any time."""
    out = []
    for a, b in re.findall(r"(\d{1,2}[:.]\d{2})\s*[-–to]+\s*(\d{1,2}[:.]\d{2})", text or ""):
        def m(t: str) -> int:
            h, mm = re.split(r"[:.]", t)
            return int(h) * 60 + int(mm)

        out.append((m(a), m(b)))
    return out


def in_windows(text: str | None, when: datetime) -> bool:
    windows = parse_windows(text)
    if not windows:
        return True
    minute = when.hour * 60 + when.minute
    return any((a <= minute < b) if a <= b else (minute >= a or minute < b) for a, b in windows)


def variant(text: str | None, **values: Any) -> str:
    """One of the lines, at random, with {value}-style blanks filled in."""
    lines = [ln.strip() for ln in (text or "").splitlines() if ln.strip()]
    if not lines:
        return ""
    line = random.choice(lines)
    for key, val in values.items():
        line = line.replace("{" + key + "}", str(val))
    return line


def _sel(name: str, selector: dict, required: bool = False) -> dict[str, Any]:
    return {"name": name, "required": required, "selector": selector}


KINDS: dict[str, dict[str, Any]] = {
    "offers": {
        "title": "Offers",
        "icon": "mdi:hand-heart-outline",
        "blurb": "Questions the house asks when someone walks in — 'fancy a brew?', 'shall I put the heating on?'. "
                 "Saying yes does the actions; saying no a few times makes it ask less.",
        "add": "+ NEW OFFER",
        "defaults": {
            "enabled": True, "areas": [], "times": "", "questions": "", "value_entity": None,
            "idle_entity": None, "idle_minutes": 45, "below_entity": None, "below": None,
            "actions": [], "cooldown_hours": 2, "last": None,
        },
        "schema": [
            _sel("name", {"text": {}}, True),
            _sel("enabled", {"boolean": {}}),
            _sel("areas", {"area": {"multiple": True}}),
            _sel("times", {"text": {}}),
            _sel("questions", {"text": {"multiline": True}}),
            _sel("actions", {"action": {}}),
            _sel("idle_entity", {"entity": {}}),
            _sel("idle_minutes", {"number": {"min": 0, "max": 600, "unit_of_measurement": "min", "mode": "box"}}),
            _sel("below_entity", {"entity": {}}),
            _sel("below", {"number": {"min": -50, "max": 100, "step": 0.5, "mode": "box"}}),
            _sel("value_entity", {"entity": {}}),
            _sel("cooldown_hours", {"number": {"min": 0, "max": 48, "step": 0.5, "unit_of_measurement": "h", "mode": "box"}}),
        ],
        "labels": {
            "name": "Name", "enabled": "On", "areas": "Ask when someone walks into",
            "times": "Only between (e.g. 08:00-10:00, 14:30-16:30; empty = any time)",
            "questions": "What to ask — one per line, picked at random ({value} = the reading below)",
            "actions": "If they say yes", "idle_entity": "Only if this hasn't been on for a while (e.g. the kettle)",
            "idle_minutes": "…for at least", "below_entity": "Only if this reads below (e.g. the thermostat)",
            "below": "…this value", "value_entity": "Reading for {value} (optional; uses the one above if empty)",
            "cooldown_hours": "Then don't ask again for",
        },
    },
    "announce": {
        "title": "Announcements",
        "icon": "mdi:bullhorn-outline",
        "blurb": "Said straight away when something happens — the doorbell, the gate opening. Can get through quiet "
                 "hours, and can be skipped when a door was opened just before (someone going out).",
        "add": "+ NEW ANNOUNCEMENT",
        "defaults": {
            "enabled": True, "entity": None, "event_type": "", "to_state": "on", "messages": "", "speakers": [],
            "through_quiet": True, "skip_entity": None, "skip_seconds": 30, "actions": [],
            "cooldown_seconds": 15, "last": None,
        },
        "schema": [
            _sel("name", {"text": {}}, True),
            _sel("enabled", {"boolean": {}}),
            _sel("entity", {"entity": {}}),
            _sel("event_type", {"text": {}}),
            _sel("to_state", {"text": {}}),
            _sel("messages", {"text": {"multiline": True}}),
            _sel("speakers", {"entity": {"domain": "assist_satellite", "multiple": True}}),
            _sel("through_quiet", {"boolean": {}}),
            _sel("skip_entity", {"entity": {}}),
            _sel("skip_seconds", {"number": {"min": 0, "max": 600, "unit_of_measurement": "s", "mode": "box"}}),
            _sel("actions", {"action": {}}),
            _sel("cooldown_seconds", {"number": {"min": 0, "max": 3600, "unit_of_measurement": "s", "mode": "box"}}),
        ],
        "labels": {
            "name": "Name", "enabled": "On", "entity": "When this happens (doorbell, gate sensor…)",
            "event_type": "…or when this event fires (e.g. doorbell_pressed), instead of the above",
            "to_state": "…and changes to (usually 'on'; ignored for buttons and doorbell events)",
            "messages": "What to say — one per line, picked at random",
            "speakers": "Say it on (empty = where people are)", "through_quiet": "Say it even in quiet hours",
            "skip_entity": "Don't say it if this opened just before (e.g. the back door: someone going out)",
            "skip_seconds": "…within", "actions": "Also do (e.g. show the camera on the tablets)",
            "cooldown_seconds": "Ignore repeats for",
        },
    },
    "alarms": {
        "title": "Alarms",
        "icon": "mdi:alarm",
        "blurb": "Alarms and appointments: a time, which days, what to say, and a routine to run — lights on, kettle "
                 "on. Optionally more actions the first time someone walks into a room afterwards.",
        "add": "+ NEW ALARM",
        "defaults": {
            "enabled": True, "time": "07:00:00", "days": [], "date": None, "description": "", "message": "",
            "speakers": [], "actions": [], "room_area": None, "room_actions": [], "last_rang": None,
        },
        "schema": [
            _sel("name", {"text": {}}, True),
            _sel("enabled", {"boolean": {}}),
            _sel("time", {"time": {}}, True),
            _sel("days", {"select": {"multiple": True, "mode": "list", "options": [{"value": d, "label": DAY_NAMES[d]} for d in DAYS]}}),
            _sel("date", {"date": {}}),
            _sel("description", {"text": {}}),
            _sel("message", {"text": {"multiline": True}}),
            _sel("speakers", {"entity": {"domain": "assist_satellite", "multiple": True}}),
            _sel("actions", {"action": {}}),
            _sel("room_area", {"area": {}}),
            _sel("room_actions", {"action": {}}),
        ],
        "labels": {
            "name": "Name", "enabled": "On", "time": "Time",
            "days": "Repeat on (none = just once)", "date": "Date (for a one-off on another day)",
            "description": "Description (e.g. doctor's at the surgery)",
            "message": "What to say (empty = the name and description). {tomorrow:calendar.bins} says what's on that "
                       "calendar tomorrow — and the alarm stays quiet if nothing is", "speakers": "Say it on (empty = where people are)",
            "actions": "When it goes off, do", "room_area": "Then, the first time someone walks into",
            "room_actions": "…do",
        },
    },
    "checks": {
        "title": "Camera checks",
        "icon": "mdi:cctv",
        "blurb": "Standing questions for a camera, asked when its sensor sees something — 'is there a delivery van?'. "
                 "A yes goes on the timeline and to phones, and can be said out loud.",
        "add": "+ NEW CAMERA CHECK",
        "defaults": {
            "enabled": True, "camera": None, "triggers": [], "question": "", "spot": "", "times": "",
            "say": False, "speakers": [], "cooldown_minutes": 30, "last": None, "last_answer": None,
        },
        "schema": [
            _sel("name", {"text": {}}, True),
            _sel("enabled", {"boolean": {}}),
            _sel("camera", {"entity": {"domain": "camera"}}, True),
            _sel("triggers", {"entity": {"domain": ["binary_sensor", "event", "sensor"], "multiple": True}}),
            _sel("question", {"text": {}}, True),
            _sel("spot", {"text": {}}),
            _sel("times", {"text": {}}),
            _sel("say", {"boolean": {}}),
            _sel("speakers", {"entity": {"domain": "assist_satellite", "multiple": True}}),
            _sel("cooldown_minutes", {"number": {"min": 1, "max": 1440, "unit_of_measurement": "min", "mode": "box"}}),
        ],
        "labels": {
            "name": "Name", "enabled": "On", "camera": "Camera",
            "triggers": "Check when these see something (e.g. the camera's car or person sensor)",
            "question": "Yes-or-no question (e.g. 'Is there a delivery van in the picture?')",
            "spot": "Only this spot of the picture (optional)", "times": "Only between (empty = any time)",
            "say": "Say it out loud too", "speakers": "Say it on (empty = where people are)",
            "cooldown_minutes": "Then don't check again for",
        },
    },
}


def summary(kind: str, item: dict[str, Any], hass) -> str:
    """One short line for the list on the page."""
    def name_of(entity_id: str | None) -> str:
        st = hass.states.get(entity_id) if entity_id else None
        return st.name if st else (entity_id or "")

    if kind == "offers":
        from homeassistant.helpers import area_registry as ar

        areas = [a.name if (a := ar.async_get(hass).async_get_area(x)) else x for x in item.get("areas") or []]
        bits = [("in " + ", ".join(areas)) if areas else "anywhere", item.get("times") or "any time",
                f"every {item.get('cooldown_hours') or 0:g} h"]
        return " · ".join(bits)
    if kind == "announce":
        bits = [f"on event {item['event_type']}" if item.get("event_type") else f"when {name_of(item.get('entity'))} {item.get('to_state') or 'on'}"]
        if item.get("through_quiet"):
            bits.append("through quiet hours")
        if item.get("skip_entity"):
            bits.append(f"not after {name_of(item['skip_entity'])}")
        return " · ".join(bits)
    if kind == "alarms":
        days = item.get("days") or []
        when = ("every day" if len(days) == 7 else ", ".join(DAY_NAMES[d] for d in DAYS if d in days)) if days else (item.get("date") or "once")
        return f"{str(item.get('time') or '')[:5]} · {when}" + (f" · {item['description']}" if item.get("description") else "")
    if kind == "checks":
        return f"{name_of(item.get('camera'))} · {item.get('question') or ''}" + (" · says it" if item.get("say") else "")
    return ""
