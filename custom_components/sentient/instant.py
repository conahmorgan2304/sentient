"""Instant answers: everyday questions answered straight from the house, before any AI. No AI, nothing stored.

A cheap model still takes two or three seconds a turn. Plenty of what's asked needs no thinking at all:

* "What time is it?" — the clock;
* "What's the temperature outside?" / "How cold is it outside?" — the weather the house already uses;
* "Is the kitchen light on?" / "Is the back door open?" — one device, named exactly, that voice may use;
* "How warm is it in the bedroom?" — the room's own temperature sensor (or its thermostat's reading);
* "When's my next alarm?", "Did anyone come round?", "Is the house locked up?" — Sentient's own records.

Only questions, never anything that could be a command; only a single device that clearly matches, never a guess
between several; only entities exposed to voice assistants. Anything else, or any doubt, goes to the AI as before.
The question and answer are added to the conversation, so a follow-up ("turn it off") still knows what "it" is.
Guests get the clock, the weather and device states, not the household's alarms or visitors.
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
        if (chosen and st.entity_id == chosen) or (not chosen and area_of(hass, st.entity_id) == area.id):
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


async def async_answer(hass: HomeAssistant, rt: Any, text: str, context: Context | None) -> str | None:
    """An instant answer, or None for the AI to answer as usual."""
    options = getattr(rt, "options", {}) or {}
    if not options.get(CONF_INSTANT) or not text:
        return None
    from .local_answers import COMING, SECURITY, VISITORS, _coming_up, _visitors, is_question

    text = " ".join(text.split())
    if not is_question(text):
        return None
    guest = bool(getattr(getattr(rt, "speech", None), "guest_mode", False))
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
        if COMING.search(text):
            return _coming_up(rt)
        if VISITORS.search(text):
            return _visitors(rt)
    except Exception:  # noqa: BLE001 - an instant answer is a bonus; the AI is always there behind it
        return None
    return None
