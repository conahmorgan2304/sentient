"""Room trackers: which room someone's phone or tag is in, for saying things where they are.

A room tracker is any sensor whose state is the room something is in: a Bermuda BLE "Area" sensor, ESPresense,
room-assistant and the like. Its state is matched to a Home Assistant area by the sensor's own ``area_id``
attribute when it has one, otherwise by the area's name or one of its aliases.

Optional (off by default). When it's on and a tracker says someone is in a room that has a speaker, things meant for
"wherever people are" are said there. When no tracker knows (phone not seen, out, a room without a speaker, or the
setting is off), the motion-based guess is used exactly as before.

Used only for choosing where to speak. It isn't used for "is anyone up?": a phone left on a bedside table at 3am
would otherwise make the house think someone is about. Nothing is stored.
"""
from __future__ import annotations

import re
from typing import Any

from homeassistant.core import HomeAssistant
from homeassistant.helpers import area_registry as ar

CONF_ROOM_TRACKING = "room_tracking"
CONF_ROOM_TRACKERS = "room_trackers"

# not a room: not seen lately, away, or the integration has nothing to say
NOT_A_ROOM = {"", "unknown", "unavailable", "none", "not_home", "away", "home", "not_home_area"}


def _area_for(hass: HomeAssistant, state: Any) -> str | None:
    areas = ar.async_get(hass)
    area_id = state.attributes.get("area_id")
    if isinstance(area_id, str) and areas.async_get_area(area_id) is not None:
        return area_id
    value = str(state.state or "").strip()
    if value.casefold() in NOT_A_ROOM:
        return None
    if (area := areas.async_get_area_by_name(value)) is not None:
        return area.id
    wanted = value.casefold()
    for area in areas.async_list_areas():
        if area.id == value or area.name.casefold() == wanted or wanted in {a.casefold() for a in area.aliases or ()}:
            return area.id
    return None


def whose(name: str) -> str:
    """'Conah's phone Area' -> 'Conah's phone': Bermuda and similar add the word 'Area' to the device's name."""
    return re.sub(r"\s+(area|room)$", "", name.strip(), flags=re.IGNORECASE) or name


def prompt_line(hass: HomeAssistant, options: dict[str, Any]) -> str | None:
    """Where the room trackers put things now, for the voice ('Where's ...?'). None when off or nothing set."""
    known = describe(hass, options)
    if not known:
        return None
    where = "; ".join(f"{whose(t['tracker'])}: {t['room'] or 'not seen in any room just now'}" for t in known)
    # 24 Nov: "Which room is Conah's phone in?" got "it isn't showing up on the trackers" while this line said the
    # hallway: the tracker isn't in the exposed devices or the house context, and the voice went by those instead
    return ("Room trackers (Bluetooth) place these now — " + where + ". These are set up in Sentient, so they won't "
            "appear in the device list or the house context: answer where a phone is from this line. A phone is "
            "usually with its owner but not always: say where the phone is, not where the person definitely is. "
            "Not seen: say you can't tell.")


def tracked_areas(hass: HomeAssistant, options: dict[str, Any]) -> list[str]:
    """The areas the room trackers put people in now, in tracker order, without repeats ([] when off)."""
    if not options.get(CONF_ROOM_TRACKING):
        return []
    out: list[str] = []
    for entity_id in options.get(CONF_ROOM_TRACKERS) or []:
        state = hass.states.get(entity_id)
        if state is None or str(state.state).casefold() in NOT_A_ROOM:
            continue
        if (area := _area_for(hass, state)) and area not in out:
            out.append(area)
    return out


def describe(hass: HomeAssistant, options: dict[str, Any]) -> list[dict[str, Any]]:
    """Each tracker and the room it gives, for the Mind sensor ('room' None: not known now)."""
    if not options.get(CONF_ROOM_TRACKING):
        return []
    areas = ar.async_get(hass)
    out = []
    for entity_id in options.get(CONF_ROOM_TRACKERS) or []:
        state = hass.states.get(entity_id)
        area_id = _area_for(hass, state) if state is not None and str(state.state).casefold() not in NOT_A_ROOM else None
        area = areas.async_get_area(area_id) if area_id else None
        out.append({"tracker": state.name if state is not None else entity_id, "entity_id": entity_id,
                    "room": area.name if area else None})
    return out
