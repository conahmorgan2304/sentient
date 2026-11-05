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
        self._since: dict[str, datetime] = {}  # entity -> when it started running (not yet counted)
        self._unsubs: list = []

    async def async_start(self) -> None:
        now = dt_util.utcnow()
        for item in self.items.enabled():
            if (st := self.hass.states.get(item.get("entity") or "")) is not None and st.state in RUNNING:
                self._since[st.entity_id] = now
        self._unsubs = [
            self.hass.bus.async_listen(EVENT_STATE_CHANGED, self._changed),
            async_track_time_interval(self.hass, self._tick, timedelta(minutes=15)),
        ]

    @callback
    def async_stop(self) -> None:
        self._count(dt_util.utcnow())
        while self._unsubs:
            self._unsubs.pop()()

    # ----- counting use ---------------------------------------------------------------------
    def _watched(self) -> set[str]:
        return {i["entity"] for i in self.items.enabled() if i.get("entity")}

    @callback
    def _changed(self, event: Event) -> None:
        entity_id = event.data.get("entity_id")
        if entity_id not in self._watched():
            return
        new, now = event.data.get("new_state"), dt_util.utcnow()
        running = new is not None and new.state in RUNNING
        if running and entity_id not in self._since:
            self._since[entity_id] = now
        elif not running and entity_id in self._since:
            self._add(entity_id, now - self._since.pop(entity_id))

    def _add(self, entity_id: str, ran: timedelta) -> None:
        hours = ran.total_seconds() / 3600
        for item in self.items.enabled():
            if item.get("entity") == entity_id:
                self.items.update(item["id"], used_hours=round(float(item.get("used_hours") or 0) + hours, 2))

    def _count(self, now: datetime) -> None:
        """Bank the time things have been running so far (so a restart loses nothing)."""
        for entity_id, start in list(self._since.items()):
            self._add(entity_id, now - start)
            self._since[entity_id] = now

    # ----- due --------------------------------------------------------------------------------
    def status(self, item: dict[str, Any]) -> dict[str, Any]:
        used = float(item.get("used_hours") or 0) + (
            (dt_util.utcnow() - self._since[item["entity"]]).total_seconds() / 3600 if item.get("entity") in self._since else 0)
        done = dt_util.parse_datetime(str(item.get("last_done") or ""))
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
            said = dt_util.parse_datetime(str(item.get("said") or ""))
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
        self.items.update(item["id"], used_hours=0, last_done=dt_util.utcnow().isoformat(), said=None)
        if item.get("entity") in self._since:
            self._since[item["entity"]] = dt_util.utcnow()
        rt = self._rt()
        if rt is not None:
            rt.timeline.add("mode", f"Done: {item['name']}", "the count starts again")
        return {"done": item["name"]}
