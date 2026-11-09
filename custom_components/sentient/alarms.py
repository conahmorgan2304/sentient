"""Alarms: a time, which days (or once), what to say, and actions — "5:45, weekdays: downstairs lights
on and the kettle on". Optionally more actions the first time someone walks into a room afterwards
("when someone gets to the kitchen, the radio on"). Alarms get through quiet hours: someone set them.
"""

from __future__ import annotations

from datetime import datetime, timedelta
import logging
import re
from typing import Any, Callable

from homeassistant.const import EVENT_STATE_CHANGED
from homeassistant.core import Event, HomeAssistant, callback
from homeassistant.helpers.event import async_track_time_change
from homeassistant.util import dt as dt_util

from .items import Items
from .kinds import DAYS
from .presence import ActivityTracker, area_of
from .shortcuts import async_run_actions

_LOGGER = logging.getLogger(__name__)
ROOM_WINDOW = timedelta(minutes=90)


def due(alarm: dict[str, Any], local: datetime) -> bool:
    if not alarm.get("enabled", True):
        return False
    t = str(alarm.get("time") or "")[:5]
    if t != local.strftime("%H:%M"):
        return False
    rang = dt_util.parse_datetime(alarm.get("last_rang") or "")
    if rang and dt_util.as_local(rang).strftime("%Y-%m-%d %H:%M") == local.strftime("%Y-%m-%d %H:%M"):
        return False
    days = alarm.get("days") or []
    if days:
        return DAYS[local.weekday()] in days
    if alarm.get("date"):
        return str(alarm["date"])[:10] == local.date().isoformat()
    return True  # once, at the next time it comes round


def next_ring(alarm: dict[str, Any], now: datetime) -> datetime | None:
    """When it next goes off (local), for the page and for 'when's my alarm?'."""
    if not alarm.get("enabled", True):
        return None
    try:
        h, m = (int(x) for x in str(alarm.get("time") or "")[:5].split(":"))
    except ValueError:
        return None
    local = dt_util.as_local(now)
    for add in range(0, 370):
        day = (local + timedelta(days=add)).date()
        at = local.replace(year=day.year, month=day.month, day=day.day, hour=h, minute=m, second=0, microsecond=0)
        if at <= local:
            continue
        days = alarm.get("days") or []
        if days and DAYS[day.weekday()] not in days:
            continue
        if not days and alarm.get("date") and str(alarm["date"])[:10] != day.isoformat():
            continue
        return at
    return None



def exposed_to_voice(hass: HomeAssistant, entity_id: str) -> bool:
    """Exposed to Assist (Settings → Voice assistants → Expose). Unknown counts as no."""
    try:
        from homeassistant.components.homeassistant.exposed_entities import async_should_expose

        return bool(async_should_expose(hass, "conversation", entity_id))
    except (KeyError, ImportError):
        return False


def voice_actions_problem(hass: HomeAssistant, actions: list[dict[str, Any]]) -> str | None:
    """Why an alarm made by voice can't do these, or None. The same limits as rules (rules.safe_actions), the
    never-touch list, and only entities exposed to voice assistants."""
    from .never import blocked
    from .rules import safe_actions

    if why := safe_actions(actions):
        return why.replace("rules", "alarms made by voice", 1)
    for action in actions:
        target = action.get("target") or {}
        ids = target.get("entity_id") if isinstance(target, dict) else None
        for eid in [ids] if isinstance(ids, str) else list(ids or []):
            st = hass.states.get(eid)
            name = st.name if st else eid
            if blocked(hass, eid):
                return f"{name} is on the never-touch list"
            if not exposed_to_voice(hass, eid):
                return f"{name} isn't exposed to voice assistants (Settings → Voice assistants → Expose)"
    return None


def describe_schedule(hass: HomeAssistant, name: str, at: str, days: list[str], on: str | None,
                      actions: list[dict[str, Any]], routine: str | None, silent: bool) -> str:
    """What's read back before an alarm made by voice is saved."""
    names = {"mon": "Monday", "tue": "Tuesday", "wed": "Wednesday", "thu": "Thursday", "fri": "Friday",
             "sat": "Saturday", "sun": "Sunday"}
    if days and len(days) == 7:
        when = "every day"
    elif days == DAYS[:5]:
        when = "on weekdays"
    elif days == DAYS[5:]:
        when = "at weekends"
    elif days:
        when = "every " + ", ".join(names[d] for d in days if d in names)
    else:
        when = f"on {on}" if on else "once, the next time it comes round"
    doing = []
    for action in actions:
        eid = (action.get("target") or {}).get("entity_id")
        st = hass.states.get(eid) if isinstance(eid, str) else None
        verb = "switch on" if str(action.get("action", "")).endswith("turn_on") else "switch off"
        doing.append(f"{verb} the {st.name if st else eid}")
    if routine:
        doing.append(f"run the routine '{routine}'")
    what = " and ".join(doing) or ("say it out loud" if not silent else "nothing")
    return f"'{name}': at {str(at)[:5]} {when}, {what}" + ("" if silent else ", and say it out loud") + "."


class Alarms:
    def __init__(self, hass: HomeAssistant, items: Items, runtime: Callable[[], Any]) -> None:
        self.hass = hass
        self.items = items
        self._rt = runtime
        self._room: dict[str, tuple[str, datetime, list]] = {}  # alarm id -> (area, until, actions)
        self._unsubs: list = []

    @callback
    def async_start(self) -> None:
        self._unsubs.append(async_track_time_change(self.hass, self._tick, second=0))
        self._unsubs.append(self.hass.bus.async_listen(EVENT_STATE_CHANGED, self._moved))

    @callback
    def async_stop(self) -> None:
        while self._unsubs:
            self._unsubs.pop()()

    @callback
    def _tick(self, now: datetime) -> None:
        local = dt_util.as_local(now)
        for alarm in list(self.items.items.values()):
            if due(alarm, local):
                self.hass.async_create_task(self.async_ring(alarm))

    def _checked(self, alarm: dict[str, Any]) -> list[dict[str, Any]]:
        """The actions to run. Only alarms saved on the Alarms page by an admin run exactly as set; everything else —
        made by voice, or saved by a version before 0.88 that didn't record who made it — is checked again when it
        goes off (allowed actions, the never-touch list, exposed to voice assistants)."""
        actions = alarm.get("actions") or []
        if alarm.get("made_by") == "page":
            return actions
        device = [x for x in actions if x.get("action") != "sentient.run_routine"]
        if why := voice_actions_problem(self.hass, device):
            _LOGGER.warning("Alarm %s: not run — %s", alarm["name"], why)
            if (rt := self._rt()) is not None:
                if not alarm.get("made_by"):  # saved before 0.88: it may have been made on the page
                    why += " — if you made it on the Alarms page, open it there and save it once to keep it as it is"
                rt.timeline.add("health", f"Didn't run: {alarm['name']}", why)
            return [x for x in actions if x.get("action") == "sentient.run_routine"]
        return actions

    async def _ring_heating(self, alarm: dict[str, Any]) -> dict[str, Any]:
        """A heating schedule: its actions are rebuilt from the spec and only run while the feature is on."""
        from .heating_schedule import describe, enabled, heating_actions

        rt = self._rt()
        if not enabled(rt.options):
            return {"skipped": "heating schedules are switched off in Settings"}
        now = dt_util.utcnow()
        changes: dict[str, Any] = {"last_rang": now.isoformat()}
        if not alarm.get("days"):
            changes["enabled"] = False
        self.items.update(alarm["id"], **changes)
        actions, why = heating_actions(self.hass, rt.options, alarm.get("heating"))
        if why:
            _LOGGER.warning("Heating schedule %s: not run — %s", alarm["name"], why)
            rt.timeline.add("health", f"Didn't run: {alarm['name']}", why)
            return {"skipped": why}
        try:
            await async_run_actions(self.hass, actions, f"heating: {alarm['name']}")
        except Exception as err:  # noqa: BLE001
            _LOGGER.warning("Heating schedule %s failed: %s", alarm["name"], err)
            rt.timeline.add("health", f"Heating schedule didn't run: {alarm['name']}", str(err)[:200])
            return {"skipped": "failed"}
        rt.timeline.add("heating", f"Schedule: {describe(self.hass, alarm['heating'])}", alarm["name"])
        return {"ran": alarm["name"]}

    async def async_ring(self, alarm: dict[str, Any]) -> dict[str, Any]:
        if alarm.get("heating"):
            return await self._ring_heating(alarm)
        rt = self._rt()
        now = dt_util.utcnow()
        changes: dict[str, Any] = {"last_rang": now.isoformat()}
        if not alarm.get("days"):
            changes["enabled"] = False  # a one-off: done
        self.items.update(alarm["id"], **changes)
        local = dt_util.as_local(now)
        if alarm.get("silent"):
            rt.timeline.add("mode", alarm["name"], "schedule")
            try:
                await async_run_actions(self.hass, self._checked(alarm), f"alarm: {alarm['name']}")
            except Exception as err:  # noqa: BLE001
                _LOGGER.warning("Alarm %s: actions failed: %s", alarm["name"], err)
            return {"spoken": False, "reason": "silent"}
        message = (alarm.get("message") or "").strip()
        if "{tomorrow:" in message or "{today:" in message:
            message = await self._fill_calendar(message, local)
            if not message:
                rt.timeline.add("noticed", f"Alarm skipped: {alarm['name']}", "nothing on the calendar")
                return {"skipped": "nothing on the calendar"}
        if not message:
            message = f"It's {local:%-I:%M}. {alarm['name']}." + (f" {alarm['description']}." if alarm.get("description") else "")
        speakers = [s for s in alarm.get("speakers") or [] if self.hass.states.get(s)] or None
        rt.timeline.add("watch", f"Alarm: {alarm['name']}", alarm.get("description") or None)
        if alarm.get("room_area") and alarm.get("room_actions"):
            self._room[alarm["id"]] = (alarm["room_area"], now + ROOM_WINDOW, alarm["room_actions"])
        result = await rt.speech.async_say(message, "urgent", speakers, source=f"alarm:{alarm['name']}")
        try:
            await async_run_actions(self.hass, self._checked(alarm), f"alarm: {alarm['name']}")
        except Exception as err:  # noqa: BLE001
            _LOGGER.warning("Alarm %s: actions failed: %s", alarm["name"], err)
        return result.as_dict()

    async def _fill_calendar(self, message: str, local: datetime) -> str:
        """{tomorrow:calendar.x} / {today:calendar.x} -> what's on it ("Recycling"); '' if nothing."""
        out = message
        for when, cal in re.findall(r"\{(today|tomorrow):([a-z0-9_.]+)\}", message):
            day = local.date() + timedelta(days=1 if when == "tomorrow" else 0)
            start = local.replace(year=day.year, month=day.month, day=day.day, hour=0, minute=0, second=0, microsecond=0)
            try:
                resp = await self.hass.services.async_call(
                    "calendar", "get_events",
                    {"entity_id": cal, "start_date_time": start.isoformat(), "end_date_time": (start + timedelta(days=1)).isoformat()},
                    blocking=True, return_response=True,
                )
                events = [e.get("summary", "") for e in (resp or {}).get(cal, {}).get("events", [])]
            except Exception as err:  # noqa: BLE001
                _LOGGER.warning("Alarm couldn't read %s: %s", cal, err)
                events = []
            if not events:
                return ""
            out = out.replace("{" + f"{when}:{cal}" + "}", " and ".join(e for e in events if e))
        return out

    @callback
    def _moved(self, event: Event) -> None:
        if not self._room:
            return
        new, old = event.data.get("new_state"), event.data.get("old_state")
        if new is None or new.state != "on" or (old is not None and old.state == "on") or not ActivityTracker.is_presence(new):
            return
        area = area_of(self.hass, new.entity_id)
        now = dt_util.utcnow()
        for alarm_id, (want, until, actions) in list(self._room.items()):
            if now > until:
                self._room.pop(alarm_id, None)
            elif area == want:
                self._room.pop(alarm_id, None)
                name = (self.items.items.get(alarm_id) or {}).get("name", "alarm")
                self.hass.async_create_task(async_run_actions(self.hass, actions, f"{name} (room)"))

    # ----- by voice ------------------------------------------------------------------------
    def set(self, name: str, at: str, days: list[str] | None = None, on: str | None = None,
            description: str = "", routine: str | None = None, silent: bool = False,
            actions: list[dict[str, Any]] | None = None, made_by: str = "") -> dict[str, Any]:
        """An alarm (said at a time) — or, silent, an automated routine (just done at a time)."""
        actions = list(actions or []) + ([{"action": "sentient.run_routine", "data": {"name": routine}}] if routine else [])
        item = {"name": name, "time": at if len(at) > 5 else f"{at}:00", "days": days or [], "date": on,
                "description": description, "actions": actions, "enabled": True, "silent": silent, "made_by": made_by}
        existing = self.items.find(name)
        if existing:
            item["id"] = existing["id"]
        return self.items.upsert(item)

    def listing(self) -> list[dict[str, Any]]:
        now = dt_util.utcnow()
        out = []
        for a in self.items.items.values():
            nxt = next_ring(a, now)
            out.append({"id": a["id"], "name": a["name"], "time": str(a.get("time"))[:5], "days": a.get("days") or [],
                        "description": a.get("description") or "", "on": a.get("enabled", True), "silent": bool(a.get("silent")),
                        "next": nxt.isoformat() if nxt else None})
        return sorted(out, key=lambda x: x["next"] or "9")
