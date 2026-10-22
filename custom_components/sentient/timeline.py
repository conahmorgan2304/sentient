"""Timeline: one running log of what the house did and noticed.

Fed from Sentient's own events — what was said or held back and why, remarks it
decided against, doorbell visits, watches and reminders going off, motion lights,
and house health changes. Kept across restarts (the last few hundred entries) for
the sidebar page and for "what happened while I was out?".
"""

from __future__ import annotations

from collections import deque
from datetime import timedelta
from typing import Any

from homeassistant.core import Event, HomeAssistant, callback
from homeassistant.helpers.storage import Store
from homeassistant.util import dt as dt_util

from .const import (
    DOMAIN,
    EVENT_HEALTH_CHANGED,
    EVENT_HELD,
    EVENT_MODE_CHANGED,
    EVENT_NOTICED,
    EVENT_SPOKE,
    EVENT_VISITOR,
    EVENT_WATCH_FIRED,
)
from .motion_lights import EVENT_MOTION_LIGHT

STORAGE_VERSION = 1
KEEP = 400
EVENT_TIMELINE = f"{DOMAIN}_timeline"
KINDS = ["said", "held", "noticed", "visitor", "watch", "light", "health", "mode"]


class Timeline:
    """Newest-first list of {time, kind, text, detail}."""

    def __init__(self, hass: HomeAssistant, entry_id: str) -> None:
        self.hass = hass
        self._store: Store[dict[str, Any]] = Store(hass, STORAGE_VERSION, f"{DOMAIN}.{entry_id}.timeline")
        self.items: deque[dict[str, Any]] = deque(maxlen=KEEP)
        self._visitors_log = None
        self._seen_visit: str | None = None
        self._problems: dict[str, str] | None = None
        self._unsubs: list = []

    async def async_start(self) -> None:
        data = await self._store.async_load() or {}
        self.items.extend(data.get("items", [])[:KEEP])
        listen = self.hass.bus.async_listen
        self._unsubs += [
            listen(EVENT_SPOKE, self._spoke),
            listen(EVENT_HELD, self._held),
            listen(EVENT_NOTICED, self._noticed),
            listen(EVENT_VISITOR, self._visitor),
            listen(EVENT_WATCH_FIRED, self._watch),
            listen(EVENT_MOTION_LIGHT, self._light),
            listen(EVENT_HEALTH_CHANGED, self._health),
            listen(EVENT_MODE_CHANGED, self._mode),
        ]

    @callback
    def attach_visitors(self, log) -> None:
        self._visitors_log = log
        if log:
            self._seen_visit = log[0].get("time")

    @callback
    def async_stop(self) -> None:
        while self._unsubs:
            self._unsubs.pop()()

    # ----- recording -----------------------------------------------------------------
    @callback
    def add(self, kind: str, text: str, detail: str | None = None, **extra: Any) -> None:
        if not text:
            return
        item = {"time": dt_util.utcnow().isoformat(), "kind": kind, "text": text}
        if detail:
            item["detail"] = detail
        item.update({k: v for k, v in extra.items() if v is not None})
        self.items.appendleft(item)
        self._store.async_delay_save(lambda: {"items": list(self.items)}, 10)
        self.hass.bus.async_fire(EVENT_TIMELINE, item)

    @callback
    def _spoke(self, event: Event) -> None:
        d = event.data
        rooms = []
        for sp in d.get("speakers") or []:
            st = self.hass.states.get(sp)
            rooms.append(st.name if st else sp)
        self.add("said", d.get("message", ""), ", ".join(rooms) or None, priority=d.get("priority"), source=d.get("source"))

    @callback
    def _held(self, event: Event) -> None:
        d = event.data
        reason = str(d.get("reason") or "").split(":")[0].replace("_", " ")
        self.add("held", d.get("message", ""), f"held back: {reason}" if reason else None, reason=d.get("reason"), source=d.get("source"))

    @callback
    def _noticed(self, event: Event) -> None:
        d = event.data
        if d.get("result") != "stayed quiet":
            return  # remarks it made show up as "said"; skips are routine
        self.add("noticed", "; ".join(d.get("changes") or []), "noticed, decided it wasn't worth saying")

    @callback
    def _visitor(self, _event: Event) -> None:
        log = self._visitors_log
        if not log or log[0].get("time") == self._seen_visit:
            return
        visit = log[0]
        self._seen_visit = visit.get("time")
        who = f"{visit['name']} — " if visit.get("name") else ""
        self.add(
            "visitor", f"{who}{visit.get('description') or 'Someone at the door'}", visit.get("kind"),
            parcel=bool(visit.get("parcel_left")) or None,
        )

    @callback
    def _watch(self, event: Event) -> None:
        d = event.data
        self.add("watch", d.get("message") or d.get("what", ""), d.get("what"))

    @callback
    def _light(self, event: Event) -> None:
        d = event.data
        self.add("light", f"{d.get('name')}: {d.get('what')}")

    @callback
    def _mode(self, event: Event) -> None:
        d = event.data
        if "chime" in d:
            self.add("mode", "Chime on" if d["chime"] else "Chime off")
        elif "guest_mode" in d:
            self.add("mode", "Guest mode on" if d["guest_mode"] else "Guest mode off")
        elif d.get("hush") and d.get("until"):
            until = dt_util.as_local(dt_util.parse_datetime(d["until"]))
            self.add("mode", f"Hushed until {until:%H:%M}")
        elif "hush" in d:
            self.add("mode", "Talking again")

    @callback
    def _health(self, event: Event) -> None:
        now: dict[str, str] = event.data.get("problems") or {}
        before, self._problems = self._problems, dict(now)
        if before is None:
            return  # first check after start: nothing to compare with
        for key, line in now.items():
            if key not in before:
                self.add("health", line)
        for key, line in before.items():
            if key not in now:
                name = key.split(":", 1)[1]
                self.add("health", f"{name} is back to normal" if key.startswith("offline") else f"{name}: battery fine again")

    # ----- reading -----------------------------------------------------------------------
    def since(self, hours: float = 24, kinds: list[str] | None = None, limit: int = 100) -> list[dict[str, Any]]:
        cutoff = (dt_util.utcnow() - timedelta(hours=hours)).isoformat()
        out = []
        for item in self.items:
            if item["time"] < cutoff:
                break
            if kinds and item["kind"] not in kinds:
                continue
            out.append(item)
            if len(out) >= limit:
                break
        return out
