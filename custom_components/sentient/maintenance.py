"""Maintenance from use: filters, descaling and services due by how much something has actually run.

Each item on the Maintenance page names a job ("Air purifier filter"), the device whose use counts, and when
it's due — after so many hours of use, so many days, or whichever comes first. Sentient adds up the time the
device is on or running. When a job is due it's said once in the daytime when someone's about (and again
every few days until it's done), it's in the briefing, and "I've changed the purifier filter" by voice — or
sentient.maintenance_done — starts the count again. "How do I…?" is answered from the manuals, when those
are on. Plain rules, no AI.
"""

from __future__ import annotations

from datetime import datetime, timedelta
from typing import Any, Callable

from homeassistant.const import EVENT_STATE_CHANGED
from homeassistant.core import Event, HomeAssistant, callback
from homeassistant.helpers.event import async_track_time_interval
from homeassistant.util import dt as dt_util

from .reasons import because

RUNNING = {"on", "running", "heating", "cleaning", "brewing", "playing", "washing", "drying", "cooking", "cool",
           "heat", "heat_cool", "dry", "fan_only", "auto"}
SAY_AGAIN = timedelta(days=3)
DAYTIME = (9, 20)


class Maintenance:
    def __init__(self, hass: HomeAssistant, items: Any, rt_getter: Callable[[], Any]) -> None:
        self.hass = hass
        self.items = items
        self._rt = rt_getter
        self._running: dict[str, tuple[str, datetime]] = {}  # job ID -> (device, uncounted start)
        self._unsubs: list = []

    async def async_start(self) -> None:
        self._items_changed()
        self.items.listeners.append(self._items_changed)
        self._unsubs = [
            self.hass.bus.async_listen(EVENT_STATE_CHANGED, self._changed),
            async_track_time_interval(self.hass, self._tick, timedelta(minutes=15)),
        ]

    @callback
    def async_stop(self) -> None:
        self.items.listeners.remove(self._items_changed)
        self._count(dt_util.utcnow())
        self._running.clear()
        while self._unsubs:
            self._unsubs.pop()()

    # ----- counting use ---------------------------------------------------------------------
    @callback
    def _items_changed(self) -> None:
        """Start new jobs immediately and drop meters for removed or reconfigured jobs."""
        now = dt_util.utcnow()
        watched = {item["id"]: item.get("entity") for item in self.items.enabled()}
        for job_id, (entity_id, _) in list(self._running.items()):
            if watched.get(job_id) != entity_id:
                self._running.pop(job_id)
        for job_id, entity_id in watched.items():
            if job_id not in self._running and entity_id:
                state = self.hass.states.get(entity_id)
                if state is not None and state.state in RUNNING:
                    self._running[job_id] = (entity_id, now)

    @callback
    def _changed(self, event: Event) -> None:
        entity_id = event.data.get("entity_id")
        new = event.data.get("new_state")
        now = dt_util.utcnow()
        for item in self.items.enabled():
            if item.get("entity") != entity_id:
                continue
            job_id = item["id"]
            if new is not None and new.state in RUNNING:
                self._running.setdefault(job_id, (entity_id, now))
            elif pending := self._running.pop(job_id, None):
                self._bank(item, now - pending[1])

    def _bank(self, item: dict[str, Any], ran: timedelta) -> None:
        hours = ran.total_seconds() / 3600
        self.items.update(item["id"], used_hours=float(item.get("used_hours") or 0) + hours)

    def _count(self, now: datetime) -> None:
        """Bank the uncounted time for each job."""
        for job_id, (entity_id, start) in list(self._running.items()):
            self._running[job_id] = (entity_id, now)
            self._bank(self.items.items[job_id], now - start)

    # ----- due --------------------------------------------------------------------------------
    @staticmethod
    def _when(value: Any) -> datetime | None:
        """A stored time, always timezone-aware. A date typed on the Maintenance page ("2026-11-12", or a time with
        no zone) is read as local time; 12 Nov such a date crashed the check every minute and stopped all reminders."""
        text = str(value or "").strip()
        if not text:
            return None
        when = dt_util.parse_datetime(text)
        if when is None and (day := dt_util.parse_date(text)) is not None:
            when = datetime.combine(day, datetime.min.time())
        if when is None:
            return None
        return dt_util.as_utc(when.replace(tzinfo=dt_util.get_default_time_zone()) if when.tzinfo is None else when)

    def status(self, item: dict[str, Any]) -> dict[str, Any]:
        used = float(item.get("used_hours") or 0)
        if pending := self._running.get(item["id"]):
            used += (dt_util.utcnow() - pending[1]).total_seconds() / 3600
        done = self._when(item.get("last_done"))
        days = (dt_util.utcnow() - done).days if done else None
        every_h, every_d = float(item.get("every_hours") or 0), float(item.get("every_days") or 0)
        due = bool((every_h and used >= every_h) or (every_d and days is not None and days >= every_d))
        why = []
        if every_h:
            why.append(f"about {round(used)} hours of use (due every {every_h:g})")
        if every_d and days is not None:
            why.append(f"{days} days since it was last done (due every {every_d:g})")
        return {"name": item["name"], "due": due, "hours_used": round(used), "days_since": days,
                "why": " and ".join(why), "how": item.get("how") or None}

    def due(self) -> list[dict[str, Any]]:
        return [s for i in self.items.enabled() if (s := self.status(i))["due"]]

    @callback
    def _tick(self, now: datetime) -> None:
        self._count(now)
        local = dt_util.as_local(now)
        if not DAYTIME[0] <= local.hour < DAYTIME[1]:
            return
        rt = self._rt()
        if rt is None or (rt.tracker is not None and rt.tracker.best_area() is None):
            return
        for item in self.items.enabled():
            said = self._when(item.get("said"))
            if said and now - said < SAY_AGAIN:
                continue
            s = self.status(item)
            if not s["due"]:
                continue
            self.items.update(item["id"], said=now.isoformat())
            rt.timeline.add("health", f"Due: {item['name']}", s["why"])
            self.hass.async_create_task(self._async_say(rt, item["name"], s["why"]))
            break  # one at a time

    async def _async_say(self, rt: Any, name: str, why: str) -> None:
        with because(why):
            await rt.speech.async_say(f"The {name[0].lower()}{name[1:]} is due — {why.split(' (')[0]}.", "low", None,
                                      source=f"maintenance:{name}")

    def done(self, what: str) -> dict[str, Any] | None:
        item = self.items.find(what)
        if item is None:
            return None
        now = dt_util.utcnow()
        if pending := self._running.get(item["id"]):
            self._running[item["id"]] = (pending[0], now)
        self.items.update(item["id"], used_hours=0, last_done=now.isoformat(), said=None)
        rt = self._rt()
        if rt is not None:
            rt.timeline.add("mode", f"Done: {item['name']}", "the count starts again")
        return {"done": item["name"]}
