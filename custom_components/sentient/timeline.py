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
from homeassistant.helpers import area_registry as ar
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
from .presence import area_of

STORAGE_VERSION = 1
KEEP = 400
EVENT_TIMELINE = f"{DOMAIN}_timeline"
KINDS = ["said", "held", "noticed", "visitor", "watch", "light", "health", "mode", "digest", "critical", "energy", "security", "mind", "loop", "drive", "appliance", "animal", "voice", "clip"]


HELD_REASONS = {
    "quiet_hours": "quiet hours", "hushed": "the house was hushed", "guest_mode": "guests were in",
    "said_recently": "said the same not long ago", "duplicate": "just said that", "thumbs_down": "a thumbs-down on these",
    "no_speaker": "no speaker to say it on", "busy": "already saying something", "hourly_limit": "enough said this hour",
    "empty": "nothing to say", "holiday": "holiday mode (sent to phones instead)",
    "room_quiet": "that room's own quiet time",
}


def held_reason(hass: HomeAssistant, reason: str) -> str:
    """Why something wasn't said, in words ("right after another announcement", "quiet hours")."""
    kind, _, what = reason.partition(":")
    if kind == "hushed_by":
        if what == "announcement":
            return "right after another announcement"
        st = hass.states.get(what) if what else None
        return f"right after {st.name if st else what or 'something else'}"
    if kind == "muted":
        return f"muted ({what})" if what else "muted"
    return HELD_REASONS.get(kind, kind.replace("_", " "))


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
        self._started = dt_util.utcnow()
        data = await self._store.async_load() or {}
        self.items.extend(data.get("items", [])[:KEEP])
        self._problems = data.get("problems")
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
    def add(self, kind: str, text: str, detail: str | None = None, **extra: Any) -> dict | None:
        if not text:
            return
        item = {"time": dt_util.utcnow().isoformat(), "kind": kind, "text": text}
        if detail:
            item["detail"] = detail
        item.update({k: v for k, v in extra.items() if v is not None})
        self.items.appendleft(item)
        self._save()
        self.hass.bus.async_fire(EVENT_TIMELINE, dict(item))
        return item

    @callback
    def mark(self, time: str | dict | None, **fields: Any) -> None:
        """Add details to an item already on the timeline (e.g. that it went to the phones)."""
        for item in self.items:
            if item is time or item.get("time") == time:
                item.update(fields)
                self._save()
                return

    @callback
    def _save(self) -> None:
        self._store.async_delay_save(lambda: {"items": list(self.items), "problems": self._problems}, 10)

    @callback
    def _spoke(self, event: Event) -> None:
        d = event.data
        rooms = []
        for sp in d.get("speakers") or []:
            area = area_of(self.hass, sp)
            area_entry = ar.async_get(self.hass).async_get_area(area) if area else None
            st = self.hass.states.get(sp)
            rooms.append(area_entry.name if area_entry else (st.name if st else sp))
        self.add("said", d.get("message", ""), ", ".join(rooms) or ("asked on the phones" if d.get("phone") else None), priority=d.get("priority"), source=d.get("source"),
                 deliveries=d.get("deliveries"), why=d.get("why"), question=d.get("question"))

    @callback
    def _held(self, event: Event) -> None:
        d = event.data
        why = held_reason(self.hass, str(d.get("reason") or ""))
        self.add("held", d.get("message", ""), f"held back: {why}" if why else None, reason=d.get("reason"), source=d.get("source"),
                 why=d.get("why"))

    @callback
    def _noticed(self, event: Event) -> None:
        d = event.data
        if d.get("result") != "stayed quiet":
            return  # remarks it made show up as "said"; skips are routine
        self.add("noticed", "; ".join(d.get("changes") or []), "noticed, decided it wasn't worth saying", entities=d.get("entities"))

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
        self.add("watch", d.get("message") or d.get("what", ""), d.get("what"), reminder=True if d.get("kind") == "reminder" else None)

    @callback
    def _light(self, event: Event) -> None:
        d = event.data
        self.add("light", f"{d.get('name')}: {d.get('what')}")

    @callback
    def _mode(self, event: Event) -> None:
        d = event.data
        if "media_speakers" in d:
            self.add("mode", "Echo speakers on" if d["media_speakers"] else "Echo speakers off")
        elif "chime" in d:
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
        if self._starting(event):
            # just after a restart sensors are still coming back; don't log anything as recovered
            now = {**(self._problems or {}), **now}
        before, self._problems = self._problems, dict(now)
        self._save()
        if before is None:
            return  # very first check: nothing to compare with
        for key, line in now.items():
            if key not in before:
                self.add("health", line)
        for key, line in before.items():
            if key not in now:
                kind, name = key.split(":", 1)
                back = {
                    "offline": f"{name} is back to normal", "battery": f"{name}: battery fine again",
                    "ai": "The AI is answering again", "speaker": f"Speaking on {name} again",
                    "stuck": f"{name} is working again", "limit": None, "wear": None, "fight": None,
                    "frozen": f"{name.removesuffix(' frozen')}'s picture is moving again",
                }.get(kind, f"{name} is fine again")
                if back:
                    self.add("health", back, recovered=True)  # on the timeline; good news doesn't buzz a phone

    def _starting(self, _event: Event) -> bool:
        return dt_util.utcnow() - self._started < timedelta(minutes=12)

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
