"""Instant answers: everyday questions answered straight from the house, before any AI. No AI, nothing stored.

A cheap model still takes two or three seconds a turn. Plenty of what's asked needs no thinking at all:

* "What time is it?" — the clock;
* "What's the temperature outside?" / "How cold is it outside?" — the weather the house already uses;
* "Is the kitchen light on?" / "Is the back door open?" — one device, named exactly, that voice may use;
* "How warm is it in the bedroom?" — the room's own temperature sensor (or its thermostat's reading);
* "When's my next alarm?", "Did anyone come round?", "Is the house locked up?" — Sentient's own records;
* "What's on my calendar this week?" / "Have I got anything on tomorrow?" — the calendars shared with the voice.

Only questions, never anything that could be a command; only a single device that clearly matches, never a guess
between several; only entities exposed to voice assistants. Anything else, or any doubt, goes to the AI as before.
The question and answer are added to the conversation, so a follow-up ("turn it off") still knows what "it" is.
Guests get the clock, the weather and device states, not the household's alarms, calendar or visitors.
Off: every question goes to the AI.
"""

from __future__ import annotations

import re
from typing import Any

from homeassistant.core import Context, HomeAssistant
from homeassistant.helpers import area_registry as ar
from homeassistant.util import dt as dt_util

CONF_INSTANT = "instant_answers"

TIME = re.compile(r"^\s*(?:what(?:'s| is) the time|what time is it)(?: (?:now|please|right now))?\s*\??\s*$", re.I)
OUTSIDE = re.compile(r"^\s*(?:what(?:'s| is) the (?:temperature|temp)|how (?:cold|warm|hot) is it)(?: like)? outside"
                     r"(?: (?:now|today|right now))?\s*\??\s*$", re.I)
STATE = re.compile(r"^\s*(?:is|are) (?:the )?(?P<name>[\w' -]{2,40}?) (?:still |currently )?(?P<want>on|off|open|closed|shut|"
                   r"locked|unlocked)(?: (?:now|right now|at the moment))?\s*\??\s*$", re.I)
ROOM_TEMP = re.compile(r"^\s*(?:what(?:'s| is) the (?:temperature|temp) in (?:the )?(?P<a>[\w' -]{2,30}?)|how (?:warm|cold|hot) "
                       r"is (?:it in )?(?:the )?(?P<b>[\w' -]{2,30}?))(?: (?:now|right now))?\s*\??\s*$", re.I)
CALENDAR = re.compile(r"^\s*(?:what(?:'s| is) on|(?:have|has) (?:i|we) got anything(?: on)?|do (?:i|we) have anything(?: on)?|"
                      r"is there anything on)(?: (?:my|the|our) (?:calendar|diary))?(?: (?P<when>today|tomorrow|this week))?"
                      r"(?: on (?:my|the|our) (?:calendar|diary))?\s*\??\s*$", re.I)
ON = {"on", "open", "unlocked", "playing", "home", "heat", "cool", "heat_cool", "auto"}
OPENISH = {"open": "open", "closed": "closed", "shut": "closed", "locked": "locked", "unlocked": "unlocked"}


def _exposed(hass: HomeAssistant, entity_id: str) -> bool:
    from .llm_api import _exposed as exposed

    return exposed(hass, entity_id)


def _say_state(st, want: str) -> str | None:
    domain = st.domain
    state = str(st.state)
    if state in ("unavailable", "unknown"):
        return f"I can't tell: the {st.name} isn't reporting right now."
    if domain == "lock":
        now = "locked" if state == "locked" else "unlocked" if state == "unlocked" else None
    elif domain in ("binary_sensor", "cover") and want in OPENISH:
        if domain == "binary_sensor" and st.attributes.get("device_class") not in (
                "door", "window", "garage_door", "opening", "lock"):
            return None
        is_open = state in ("on", "open", "opening")
        if st.attributes.get("device_class") == "lock" and domain == "binary_sensor":
            now = "unlocked" if is_open else "locked"
        else:
            now = "open" if is_open else "closed"
    elif domain in ("light", "switch", "fan", "input_boolean", "media_player", "climate", "vacuum"):
        if want not in ("on", "off"):
            return None
        now = "on" if state in ON or (domain == "vacuum" and state == "cleaning") else "off"
    else:
        return None
    if now is None:
        return None
    wanted = OPENISH.get(want, want)
    yes = now == wanted
    return f"{'Yes' if yes else 'No'}, the {st.name} is {now}."


def _one_entity(hass: HomeAssistant, name: str) -> Any | None:
    """The single exposed entity whose name is exactly this (or 'the <area> <name>'); None if none or several."""
    wanted = " ".join(name.casefold().removeprefix("the ").split())
    found = []
    for st in hass.states.async_all():
        if st.domain not in ("light", "switch", "fan", "lock", "cover", "binary_sensor", "media_player", "climate",
                             "vacuum", "input_boolean"):
            continue
        names = {" ".join((st.name or "").casefold().split()),
                 st.entity_id.split(".", 1)[1].replace("_", " ")}
        if wanted in names and _exposed(hass, st.entity_id):
            found.append(st)
    return found[0] if len(found) == 1 else None


def _not_the_room(st: Any) -> bool:
    from .glance import _chip

    return _chip(st)


def _room_temperature(hass: HomeAssistant, room: str) -> str | None:
    from .presence import area_of

    area = ar.async_get(hass).async_get_area_by_name(room.strip())
    if area is None:
        return None
    chosen = getattr(area, "temperature_entity_id", None)
    readings = []
    for st in hass.states.async_all("sensor"):
        if st.attributes.get("device_class") != "temperature" or not _exposed(hass, st.entity_id):
            continue
        if (chosen and st.entity_id == chosen) or (not chosen and area_of(hass, st.entity_id) == area.id
                                                   and not _not_the_room(st)):
            try:
                readings.append((st, float(st.state)))
            except (TypeError, ValueError):
                continue
    if len(readings) != 1:
        return None  # none, or several that might disagree: the AI can weigh them up
    st, value = readings[0]
    unit = st.attributes.get("unit_of_measurement") or "°C"
    return f"It's {value:g}{unit} in the {area.name}."


def _outside(hass: HomeAssistant, options: dict[str, Any]) -> str | None:
    weather_id = options.get("briefing_weather")
    weather = hass.states.get(weather_id) if weather_id else next(
        (w for w in hass.states.async_all("weather") if w.attributes.get("temperature") is not None), None)
    if weather is None or weather.attributes.get("temperature") is None:
        return None
    unit = weather.attributes.get("temperature_unit") or "°C"
    sky = str(weather.state).replace("partlycloudy", "partly cloudy").replace("clear-night", "clear").replace("-", " ")
    return f"It's {round(float(weather.attributes['temperature']))}{unit} outside and {sky}."


async def _calendar(hass: HomeAssistant, when: str) -> str | None:
    """What's on today, tomorrow or this week, from the calendars shared with the voice."""
    from .llm_api import _voice_calendars
    from .skills import async_calendar_events

    calendars = _voice_calendars(hass)
    if not calendars:
        return None
    found = await async_calendar_events(hass, calendars, {"today": 1, "tomorrow": 2}.get(when, 7))
    if "error" in found:
        return None
    events = found.get("events") or []
    if when == "tomorrow":
        events = [e for e in events if str(e.get("when", "")).startswith("tomorrow")]
    label = {"today": "today", "tomorrow": "tomorrow"}.get(when, "this week")
    if not events:
        return f"There's nothing on your calendar {label}."
    said = [f"{e.get('what') or 'something'}, {e.get('when')}" for e in events[:4]]
    more = f", and {len(events) - 4} more" if len(events) > 4 else ""
    return f"On your calendar {label}: " + "; ".join(said) + more + "."


LOCK = re.compile(r"^(?:(?:can|could|would|will) you |please |hey \w+,? )*(?P<verb>lock|unlock)(?: up)? (?P<what>(?:the |my |our )?[\w' -]{2,40}?)(?: for me| please| now)?[?.!]*$",
                  re.I)


def _no_lock_reply(hass: HomeAssistant, what: str, guest: bool) -> str | None:
    """'Lock the front door' in a house with no smart lock the voice may use: the plain truth, every time.

    23 Nov: the voice kept saying "this house doesn't have locks on the doors", which isn't true — they're ordinary
    locks, just not connected. A fixed reply can't get that wrong."""
    if any(_exposed(hass, st.entity_id) for st in hass.states.async_all("lock")):
        return None  # there is a smart lock: the AI and its tools handle it
    reply = "There's no smart lock I can use, so that one needs doing by hand."
    if guest:
        return reply
    wanted = " ".join(what.casefold().removeprefix("the ").removeprefix("my ").removeprefix("our ").split())
    doors = [st for st in hass.states.async_all("binary_sensor")
             if st.attributes.get("device_class") in ("door", "garage_door", "opening") and _exposed(hass, st.entity_id)]
    match = [st for st in doors if wanted and wanted in " ".join((st.name or "").casefold().split())]
    if len(match) != 1 or match[0].state not in ("on", "off"):
        return reply
    door = match[0]
    state = "shut" if door.state == "off" else "open"
    return f"There's no smart lock I can use, so that one needs doing by hand — the {door.name} is {state}."


async def async_answer(hass: HomeAssistant, rt: Any, text: str, context: Context | None,
                       device_id: str | None = None) -> str | None:
    """An instant answer, or None for the AI to answer as usual."""
    options = getattr(rt, "options", {}) or {}
    if not options.get(CONF_INSTANT) or not text:
        return None
    from .local_answers import COMING, SECURITY, VISITORS, _coming_up, _visitors, is_question

    text = " ".join(text.split())
    lock = LOCK.match(text)
    if not lock and not is_question(text):
        return None
    guest = bool(getattr(getattr(rt, "speech", None), "guest_mode", False))
    if not guest and device_id:
        from .voice_id import private_history

        try:
            guest = bool(private_history(rt, device_id))  # a private room: household records stay out, as for guests
        except Exception:  # noqa: BLE001 - can't tell: be careful
            guest = True
    if lock:
        try:
            return _no_lock_reply(hass, lock.group("what"), guest)
        except Exception:  # noqa: BLE001
            return None
    try:
        if TIME.match(text):
            from .llm_api import clock_words

            return f"It's {clock_words(dt_util.now())}."
        if OUTSIDE.match(text):
            return _outside(hass, options)
        if m := STATE.match(text):
            st = _one_entity(hass, m.group("name"))
            return _say_state(st, m.group("want").casefold()) if st is not None else None
        if m := ROOM_TEMP.match(text):
            room = m.group("a") or m.group("b") or ""
            return None if room.casefold() in ("outside", "it outside") else _room_temperature(hass, room)
        if guest:
            return None
        if SECURITY.search(text) and re.search(r"\b(locked up|all locked|secure|anything (left )?open)\b", text, re.I):
            from .security import check_for_voice

            report = await check_for_voice(hass, context)
            return report.get("summary")
        if (m := CALENDAR.match(text)) and (m.group("when") or re.search(r"calendar|diary", text, re.I)):
            return await _calendar(hass, (m.group("when") or "this week").casefold())
        if COMING.search(text):
            return _coming_up(rt)
        if VISITORS.search(text):
            return _visitors(rt)
    except Exception:  # noqa: BLE001 - an instant answer is a bonus; the AI is always there behind it
        return None
    return None
