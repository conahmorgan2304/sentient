"""Foresight: one look at tomorrow each evening, and a heads-up only when it's worth it.

At a set time (19:30 by default), Sentient gathers tomorrow's calendar events, tomorrow's weather,
alarms and reminders due tomorrow, and asks the AI one thing: is there anything the household should
know or do tonight? ("Frost forecast — the car will need defrosting before the 8:40 appointment.")
Usually there's nothing, and nothing is said. Said where people are; if nobody's about, it's passed
on when someone turns up. Never with guests in.
"""

from __future__ import annotations

from datetime import datetime, timedelta
import logging
from typing import Any, Callable

from homeassistant.core import HomeAssistant, callback
from homeassistant.helpers.event import async_track_time_change
from homeassistant.util import dt as dt_util

from .ai import AIUnavailable, async_generate
from .const import (
    CONF_AI_TASK_ENTITY,
    CONF_BRIEFING_CALENDARS,
    CONF_BRIEFING_WEATHER,
    CONF_FORESIGHT,
    CONF_FORESIGHT_TIME,
    CONF_HOUSE_RULES,
    DEFAULT_FORESIGHT_TIME,
    personality_line,
)

_LOGGER = logging.getLogger(__name__)



def plain_heads_up(facts: dict[str, Any]) -> str:
    """Tomorrow without the AI: only frost and an early start are worth a word."""
    out: list[str] = []
    weather = facts.get("weather") or {}
    try:
        low = float(weather.get("templow"))
    except (TypeError, ValueError):
        low = None
    if low is not None and low <= 1:
        out.append("Frost's forecast for tomorrow morning, so the car may need defrosting"
                   + (f" and the water in {facts['animals_living_outside_now'][0].rsplit(' ', 1)[0]} may freeze." if facts.get("animals_living_outside_now") else "."))
    early = [e for e in facts.get("calendar") or [] if e[:2].isdigit() and e[:5] < "09:30"]
    if early:
        when, _, what = early[0].partition(" ")
        out.append(f"Early start tomorrow: {what or 'something'} at {when}.")
    return " ".join(out)

class Foresight:
    def __init__(self, hass: HomeAssistant, options: dict[str, Any], rt_getter: Callable[[], Any]) -> None:
        self.hass = hass
        self.options = options
        self._rt = rt_getter
        self._unsub = None
        self.last: dict[str, Any] | None = None

    async def async_start(self) -> None:
        self._unsub = async_track_time_change(self.hass, self._tick, second=10)

    @callback
    def async_stop(self) -> None:
        if self._unsub:
            self._unsub()
            self._unsub = None

    @callback
    def _tick(self, now: datetime) -> None:
        if not self.options.get(CONF_FORESIGHT, True):
            return
        at = str(self.options.get(CONF_FORESIGHT_TIME) or DEFAULT_FORESIGHT_TIME)[:5]
        if dt_util.as_local(now).strftime("%H:%M") == at:
            self.hass.async_create_background_task(self._async_quietly(), "sentient_foresight")

    async def _async_quietly(self) -> None:
        try:
            await self.async_look_ahead()
        except Exception as err:  # noqa: BLE001 - a missed evening is no loss
            _LOGGER.debug("Foresight skipped: %s", err)

    async def gather(self) -> dict[str, Any]:
        """Tomorrow, as plain facts."""
        rt = self._rt()
        now = dt_util.now()
        start = (now + timedelta(days=1)).replace(hour=0, minute=0, second=0, microsecond=0)
        end = start + timedelta(days=1)
        out: dict[str, Any] = {"tomorrow": start.strftime("%A %d %B")}
        events = []
        for cal in self.options.get(CONF_BRIEFING_CALENDARS) or []:
            try:
                resp = await self.hass.services.async_call(
                    "calendar", "get_events",
                    {"entity_id": cal, "start_date_time": start.isoformat(), "end_date_time": end.isoformat()},
                    blocking=True, return_response=True,
                )
            except Exception as err:  # noqa: BLE001
                _LOGGER.debug("Foresight couldn't read %s: %s", cal, err)
                continue
            for e in (resp or {}).get(cal, {}).get("events", []):
                when = str(e.get("start") or "")
                events.append(f"{when[11:16] or 'all day'} {e.get('summary', '')}".strip())
        if events:
            out["calendar"] = events
        weather_id = self.options.get(CONF_BRIEFING_WEATHER) or next(
            (s.entity_id for s in self.hass.states.async_all("weather")), None
        )
        if weather_id:
            try:
                resp = await self.hass.services.async_call(
                    "weather", "get_forecasts", {"entity_id": weather_id, "type": "daily"},
                    blocking=True, return_response=True,
                )
                for f in (resp or {}).get(weather_id, {}).get("forecast", []):
                    when = dt_util.parse_datetime(str(f.get("datetime") or ""))
                    if when and dt_util.as_local(when).date() == start.date():
                        out["weather"] = {
                            k: f.get(k) for k in ("condition", "temperature", "templow", "precipitation_probability",
                                                  "precipitation", "wind_speed") if f.get(k) is not None
                        }
                        break
            except Exception as err:  # noqa: BLE001
                _LOGGER.debug("Foresight couldn't read the forecast: %s", err)
        if rt is not None:
            alarms = [a for a in rt.alarms.listing() if a.get("next") and a["on"] and not a.get("silent")
                      and start <= dt_util.as_local(dt_util.parse_datetime(a["next"])) < end + timedelta(hours=-4)]
            if alarms:
                out["alarms_tomorrow"] = [f"{a['time']} {a['name']}" for a in alarms]
            tonight = [a for a in rt.alarms.listing() if a.get("next") and a["on"] and not a.get("silent")
                       and now <= dt_util.as_local(dt_util.parse_datetime(a["next"])) < start]
            if tonight:
                out["already_said_tonight_by_alarms"] = [f"{a['time']} {a['name']}" for a in tonight]
            if animals := rt.animals.readings():
                out["animals_living_outside_now"] = animals
            reminders = [w.what for w in rt.watches.watches if w.kind == "reminder" and start <= dt_util.as_local(w.until_dt) < end]
            if reminders:
                out["reminders_tomorrow"] = reminders
        return out

    async def async_look_ahead(self, speak: bool = True) -> dict[str, Any]:
        rt = self._rt()
        facts = await self.gather()
        result: dict[str, Any] = {"time": dt_util.utcnow().isoformat(), "facts": facts, "said": None}
        if len(facts) <= 1:
            self.last = result
            return result
        rules = (self.options.get(CONF_HOUSE_RULES) or "").strip()
        data: dict[str, Any] = {
            "task_name": "Sentient: tomorrow",
            "instructions": (
                "It's the evening. From these facts about tomorrow, decide whether the household needs a heads-up "
                "tonight — something to do or prepare (an early appointment, frost so the car will need "
                "defrosting, heavy rain so bring the washing in, a strong wind for the bins, very hot or cold; if "
                "animals live outside and it'll freeze or be very hot, their water and bedding or shade). "
                "Ordinary weather and ordinary days need nothing: then speak is false. Don't repeat anything an "
                "alarm will already say tonight. If speaking, at most two short, natural sentences, British English, "
                "no greetings, no names. " + personality_line(self.options)
                + (f"\nHouse rules:\n{rules}" if rules else "") + f"\nFacts: {facts}"
            ),
            "structure": {
                "speak": {"description": "True only if there's something worth saying", "required": True,
                          "selector": {"boolean": {}}},
                "message": {"description": "What to say, or empty", "required": False, "selector": {"text": {}}},
            },
        }
        if ai := self.options.get(CONF_AI_TASK_ENTITY):
            data["entity_id"] = ai
        try:
            if not rt.watches.spend("tomorrow"):
                raise AIUnavailable("today's AI limit is used up")
            response = await async_generate(self.hass, data)
            out = (response or {}).get("data") or {}
        except AIUnavailable as err:  # plain rules instead: frost, and an early start
            _LOGGER.debug("Look at tomorrow in plain rules: %s", err)
            plain = plain_heads_up(facts)
            out = {"speak": bool(plain), "message": plain}
        message = " ".join(str(out.get("message") or "").split()) if isinstance(out, dict) else ""
        if isinstance(out, dict) and out.get("speak") in (True, "true", "yes") and message:
            result["said"] = message
            rt.timeline.add("mind", message, "a look at tomorrow")
            if speak and not rt.speech.guest_mode:
                said = await rt.speech.async_say(message, "normal", None, source="thought:tomorrow")
                if not said.spoken and said.reason not in ("duplicate",):
                    rt.followyou.keep(message, source="thought", hours=3)
        self.last = result
        return result
