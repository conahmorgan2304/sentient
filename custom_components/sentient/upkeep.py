"""Briefings and memory upkeep.

* A **briefing** gathers what matters right now — reminders and watches due,
  routines coming up, open windows and doors among the things Sentient
  notices, the weather and what was said recently — without any AI. A voice
  agent turns it into words itself; the `sentient.briefing` service can also
  have the AI task write a few sentences and speak them where people are.
* Once a week the AI task **tidies memory**: merges duplicates, drops nonsense
  and things that are plainly out of date. It may change at most a third of
  the memories in one go.
"""

from __future__ import annotations

from datetime import datetime, timedelta
import logging
import re
from typing import TYPE_CHECKING, Any

from homeassistant.core import HomeAssistant, callback
from homeassistant.exceptions import HomeAssistantError
from homeassistant.helpers.event import async_track_time_change
from homeassistant.util import dt as dt_util

from .const import (
    CONF_AI_TASK_ENTITY,
    CONF_BRIEFING_CALENDARS,
    CONF_BRIEFING_SECTIONS,
    CONF_BRIEFING_SYSTEM,
    CONF_BRIEFING_TIME,
    CONF_BRIEFING_TODO,
    CONF_BRIEFING_WEATHER,
    CONF_BRIEFINGS,
    CONF_HOUSE_RULES,
    CONF_NOTICE_ENTITIES,
    CONF_PERSONA,
    DEFAULT_BRIEFING_SECTIONS,
    DEFAULT_PERSONA,
)
from .watches import local_when

if TYPE_CHECKING:
    from . import SentientRuntime

_LOGGER = logging.getLogger(__name__)

TIDY_MIN_MEMORIES = 8
TIDY_MAX_SHARE = 1 / 3
_OPEN_STATES = {"on", "open", "opening", "unlocked"}
_OPENABLE_CLASSES = {"window", "door", "garage_door", "opening", "lock"}


async def gather_briefing(hass: HomeAssistant, rt: SentientRuntime) -> dict[str, Any]:
    """Everything worth mentioning right now, as plain data — only the chosen sections."""
    opts = rt.options
    if not opts.get(CONF_BRIEFINGS, True):
        return {"briefings": "turned off in Sentient's settings"}
    want = set(opts.get(CONF_BRIEFING_SECTIONS) or DEFAULT_BRIEFING_SECTIONS)
    now = dt_util.now()
    end_of_day = now.replace(hour=23, minute=59, second=59)
    out: dict[str, Any] = {}
    if "time" in want:
        out["local_time"] = now.strftime("%A %d %B, %-I:%M%p").replace("AM", "am").replace("PM", "pm")

    if "weather" in want:
        weather_id = opts.get(CONF_BRIEFING_WEATHER)
        weather = hass.states.get(weather_id) if weather_id else next(iter(hass.states.async_all("weather")), None)
        if weather is not None:
            unit = weather.attributes.get("temperature_unit") or ""
            out["weather"] = (
                f"{weather.state.replace('-', ' ').replace('partlycloudy', 'partly cloudy')}, "
                f"{weather.attributes.get('temperature')}{unit}"
            )

    reminders, watches = [], []
    for w in rt.watches.watches:
        if w.kind == "reminder":
            if w.until_dt <= dt_util.as_utc(end_of_day):
                reminders.append(f"{w.message or w.what} at {local_when(w.until_dt)}")
        else:
            watches.append(f"{w.what}, until {local_when(w.until_dt)}")
    if reminders and "reminders" in want:
        out["reminders_today"] = reminders
    if watches and "watches" in want:
        out["keeping_an_eye_on"] = watches

    if "routines" in want:
        from .learning import _DAYTYPES  # local import to avoid a cycle

        minute_now = now.hour * 60 + now.minute
        coming = [
            r.describe() + (" (the house does this)" if r.adopted else "")
            for r in rt.learner.routines.values()
            if _DAYTYPES[r.days](now.date()) and 0 <= r.minute - minute_now <= 180
        ]
        if coming:
            out["routines_coming_up"] = coming

    if "open" in want:
        open_things = []
        for entity_id in opts.get(CONF_NOTICE_ENTITIES) or []:
            st = hass.states.get(entity_id)
            if st is not None and st.attributes.get("device_class") in _OPENABLE_CLASSES and st.state in _OPEN_STATES:
                open_things.append(st.name)
        if open_things:
            out["open_now"] = open_things

    if "visitors" in want and (visits := rt.visitors.today()):
        out["visitors_today"] = [
            f"{dt_util.as_local(dt_util.parse_datetime(v['time'])).strftime('%-I:%M%p').lower()} "
            + (f"{v['name']} — " if v.get("name") else "")
            + v["description"]
            + (" (left a parcel)" if v.get("parcel_left") else "")
            for v in visits[:5]
        ]

    if "said" in want and (said := rt.speech.recent_said(3)):
        out["said_recently"] = said

    if "system" in want:
        system: dict[str, Any] = {}
        updates = [s.name for s in hass.states.async_all("update") if s.state == "on"]
        if updates:
            system["updates_available"] = updates[:8]
        for entity_id in opts.get(CONF_BRIEFING_SYSTEM) or []:
            if (st := hass.states.get(entity_id)) is not None:
                unit = st.attributes.get("unit_of_measurement") or ""
                system[st.name] = f"{st.state}{(' ' + unit) if unit else ''}"
        if system:
            out["system"] = system

    if "todo" in want and (lists := opts.get(CONF_BRIEFING_TODO)):
        todo: dict[str, list[str]] = {}
        try:
            resp = await hass.services.async_call(
                "todo", "get_items", {"status": "needs_action"}, target={"entity_id": lists},
                blocking=True, return_response=True,
            )
            for entity_id, data in (resp or {}).items():
                items = [i.get("summary") for i in (data or {}).get("items", []) if i.get("summary")]
                if items:
                    name = (st.name if (st := hass.states.get(entity_id)) else entity_id)
                    todo[name] = items[:8]
        except Exception as err:  # noqa: BLE001 - a missing list shouldn't spoil the briefing
            _LOGGER.debug("Briefing could not read to-do lists: %s", err)
        if todo:
            out["to_do"] = todo

    if "calendar" in want and (calendars := opts.get(CONF_BRIEFING_CALENDARS)):
        events: list[str] = []
        try:
            resp = await hass.services.async_call(
                "calendar", "get_events",
                {"start_date_time": now.isoformat(), "end_date_time": (now + timedelta(hours=24)).isoformat()},
                target={"entity_id": calendars}, blocking=True, return_response=True,
            )
            for data in (resp or {}).values():
                for ev in (data or {}).get("events", []):
                    start = dt_util.parse_datetime(str(ev.get("start", "")))
                    when = local_when(start) if start else "all day"
                    events.append(f"{ev.get('summary', 'Event')} ({when})")
        except Exception as err:  # noqa: BLE001
            _LOGGER.debug("Briefing could not read calendars: %s", err)
        if events:
            out["calendar_next_24h"] = events[:8]
    return out


class Upkeep:
    """Briefings on request and the weekly memory tidy."""

    def __init__(self, hass: HomeAssistant, rt_getter) -> None:
        self.hass = hass
        self._rt = rt_getter
        self._unsub = None
        self.last_tidy: dict[str, Any] | None = None

    @callback
    def async_start(self) -> None:
        # Sundays at 03:45
        self._unsubs = [
            async_track_time_change(self.hass, self._weekly, hour=3, minute=45, second=0),
            async_track_time_change(self.hass, self._every_minute, second=0),
        ]

    @callback
    def async_stop(self) -> None:
        for unsub in getattr(self, "_unsubs", []):
            unsub()
        self._unsubs = []

    @callback
    def _every_minute(self, now: datetime) -> None:
        """The optional daily briefing: only if someone's about and it isn't quiet hours."""
        rt = self._rt()
        at = rt.options.get(CONF_BRIEFING_TIME)
        if not at or not rt.options.get(CONF_BRIEFINGS, True):
            return
        local = dt_util.as_local(now)
        if local.strftime("%H:%M") != str(at)[:5]:
            return
        if rt.speech.quiet_now() or (rt.tracker is not None and rt.tracker.best_area() is None):
            return
        self.hass.async_create_background_task(self._async_scheduled(), "sentient_briefing")

    async def _async_scheduled(self) -> None:
        try:
            await self.async_speak_briefing()
        except HomeAssistantError as err:
            _LOGGER.debug("Scheduled briefing skipped: %s", err)

    @callback
    def _weekly(self, now: datetime) -> None:
        if dt_util.as_local(now).weekday() == 6:
            self.hass.async_create_background_task(self.async_tidy(), "sentient_tidy")

    async def _ai(self, task: str, instructions: str, structure: dict | None) -> Any:
        rt = self._rt()
        data: dict[str, Any] = {"task_name": task, "instructions": instructions}
        if structure:
            data["structure"] = structure
        if ai_entity := rt.options.get(CONF_AI_TASK_ENTITY):
            data["entity_id"] = ai_entity
        rt.watches.spend()
        response = await self.hass.services.async_call(
            "ai_task", "generate_data", data, blocking=True, return_response=True
        )
        return (response or {}).get("data")

    # ----- briefing -------------------------------------------------------------
    async def async_speak_briefing(self, priority: str = "normal") -> dict[str, Any]:
        rt = self._rt()
        if not rt.options.get(CONF_BRIEFINGS, True):
            raise HomeAssistantError("Briefings are turned off in Sentient's settings")
        facts = await gather_briefing(self.hass, rt)
        persona = rt.options.get(CONF_PERSONA) or DEFAULT_PERSONA
        rules = (rt.options.get(CONF_HOUSE_RULES) or "").strip()
        text = await self._ai(
            "Sentient: briefing",
            f"You are {persona}, the brain of a family home. Write a spoken briefing of two to four short, "
            "natural sentences in British English from these facts. Lead with what needs doing; skip "
            "anything empty; no lists, no IDs, no forms of address."
            + (f"\nHouse rules:\n{rules}" if rules else "")
            + f"\nFacts: {facts}",
            None,
        )
        message = str(text or "").strip()
        if not message:
            raise HomeAssistantError("The AI didn't write a briefing")
        result = await rt.speech.async_say(message, priority, None, source="briefing")
        return {**result.as_dict(), "facts": facts}

    # ----- memory tidy -------------------------------------------------------------
    async def async_tidy(self) -> dict[str, Any]:
        rt = self._rt()
        items = rt.memory.items
        if len(items) < TIDY_MIN_MEMORIES:
            self.last_tidy = {"time": dt_util.now().isoformat(), "result": "too few memories to tidy"}
            return self.last_tidy
        listing = "\n".join(f"{m.id}: {m.text} ({m.category})" for m in items)
        reply = await self._ai(
            "Sentient: tidy memory",
            "These are a household's long-term memories (id: text (category)). Tidy them carefully:\n"
            "- remove: ids that are exact or near duplicates of another, nonsense, or clearly out of date;\n"
            "- merge: groups that say the same thing in parts, written as 'id1,id2 => one combined sentence', "
            "one group per line.\n"
            "Keep everything else. When unsure, keep it. Never invent facts.\n\n" + listing,
            {
                "remove": {"description": "Comma-separated ids to remove, or empty", "required": False,
                           "selector": {"text": {}}},
                "merge": {"description": "Lines of 'id1,id2 => combined sentence', or empty", "required": False,
                          "selector": {"text": {"multiline": True}}},
            },
        )
        reply = reply if isinstance(reply, dict) else {}
        valid = {m.id for m in items}
        budget = max(1, int(len(items) * TIDY_MAX_SHARE))
        removed: list[str] = []
        merged: list[str] = []
        for line in str(reply.get("merge") or "").splitlines():
            if "=>" not in line:
                continue
            ids_part, text = line.split("=>", 1)
            ids = [i for i in re.findall(r"[0-9a-f]{6,}", ids_part) if i in valid]
            text = text.strip().strip('"')
            if len(ids) < 2 or not text or len(removed) + len(ids) - 1 > budget:
                continue
            keep, drop = ids[0], ids[1:]
            rt.memory.update(keep, text)
            for i in drop:
                rt.memory.forget(i)
                valid.discard(i)
            removed.extend(drop)
            merged.append(text)
        for i in re.findall(r"[0-9a-f]{6,}", str(reply.get("remove") or "")):
            if i in valid and len(removed) < budget:
                gone = rt.memory.forget(i)
                if gone is not None:
                    removed.append(i)
                    valid.discard(i)
        self.last_tidy = {
            "time": dt_util.now().isoformat(),
            "result": f"merged {len(merged)}, removed {len(removed)}",
            "merged": merged,
        }
        _LOGGER.info("Sentient memory tidy: %s", self.last_tidy["result"])
        return self.last_tidy
