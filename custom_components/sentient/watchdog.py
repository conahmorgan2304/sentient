"""Watchdog: things wearing out, automations fighting, frozen cameras. Plain rules, no AI.

* **Wearing out** — learned slowly from the house's own records:
  - an appliance taking longer and longer to finish (recent runs a third longer than it used to);
  - a robot vacuum getting stuck, or finishing away from its dock, three times in a week;
  - a battery running down at least twice as fast as it used to (daily readings, a month to learn);
  - a device dropping offline four or more times in a week;
  - the heating warming up markedly slower than it did a month ago.
* **Automations fighting** — the same thing switched one way by one automation (or Sentient's auto-off)
  and back by another, within ten minutes, three times in a day. Both are named.
* **Frozen cameras** — a camera that says it's fine but whose picture hasn't changed at all for three
  hours (checked every half hour; only live cameras, not snapshot ones).

Each shows in house health — the overview, the briefing and voices — and on the timeline when it starts.
"""

from __future__ import annotations

from datetime import datetime, timedelta
import hashlib
import logging
from statistics import median
from typing import Any, Callable

from homeassistant.const import EVENT_STATE_CHANGED
from homeassistant.core import Event, HomeAssistant, callback
from homeassistant.helpers.event import async_track_time_change, async_track_time_interval
from homeassistant.helpers.storage import Store
from homeassistant.util import dt as dt_util

from .const import DOMAIN, EVENT_HEALTH_CHANGED

_LOGGER = logging.getLogger(__name__)

SLOWER_BY = 1.3
SLOWER_MIN = 10  # minutes
STUCK_TIMES = 3
DROPS = 4
DRAIN_FASTER = 2.0
DRAIN_MIN = 1.0  # % a day
HEATING_SLOWER = 0.7
FIGHT_WINDOW = timedelta(minutes=10)
FIGHTS = 3
FROZEN_FOR = timedelta(hours=3)
FIGHT_DOMAINS = {"light", "switch", "fan", "climate", "cover", "media_player", "input_boolean", "humidifier", "water_heater"}
WEEK = timedelta(days=7)


def _days_ago(iso_day: str, today: datetime) -> int:
    try:
        return (today.date() - datetime.fromisoformat(iso_day).date()).days
    except ValueError:
        return 9999


def drain_rate(samples: list[list], newest_days: int, oldest_days: int, today: datetime) -> float | None:
    """% per day, from the drops only (a rise is a charge or a new battery), over a window of days ago."""
    window = [(d, v) for d, v in samples if newest_days <= _days_ago(d, today) <= oldest_days]
    if len(window) < 5:
        return None
    window.sort()
    drops = sum(max(0.0, a - b) for (_, a), (_, b) in zip(window, window[1:]))
    span = _days_ago(window[0][0], today) - _days_ago(window[-1][0], today)
    return drops / span if span >= 4 else None


def _when(days: float) -> str:
    if days < 2:
        return "a day"
    if days < 13:
        return f"{round(days)} days"
    weeks = round(days / 7)
    return f"{weeks} week{'s' if weeks != 1 else ''}"


class Watchdog:
    def __init__(self, hass: HomeAssistant, entry_id: str, options: dict[str, Any], rt_getter: Callable[[], Any]) -> None:
        self.hass = hass
        self.options = options
        self._rt = rt_getter
        self._store: Store[dict[str, Any]] = Store(hass, 1, f"{DOMAIN}.{entry_id}.watchdog")
        self.batteries: dict[str, list[list]] = {}  # entity -> [[day, level]]
        self.heating: dict[str, list[list]] = {}  # entity -> [[day, rate]]
        self.drops: dict[str, list[str]] = {}  # device name -> [iso]
        self.fights: dict[str, list[str]] = {}  # "a|b|entity" -> [iso]
        self.cams: dict[str, dict[str, str]] = {}  # camera -> {hash, since}
        self._ctx: dict[str, tuple[str, datetime]] = {}  # context id -> (automation name, when)
        self._last: dict[str, tuple[str, str, datetime]] = {}  # entity -> (who, state, when)
        self._offline: set[str] | None = None
        self._unsubs: list = []

    async def async_start(self) -> None:
        data = await self._store.async_load() or {}
        for key in ("batteries", "heating", "drops", "fights", "cams"):
            setattr(self, key, dict(data.get(key) or {}))
        self._unsubs += [
            self.hass.bus.async_listen("automation_triggered", self._automation),
            self.hass.bus.async_listen(EVENT_STATE_CHANGED, self._changed),
            self.hass.bus.async_listen(EVENT_HEALTH_CHANGED, self._health),
            async_track_time_change(self.hass, self._daily, hour=10, minute=15, second=0),
            async_track_time_interval(self.hass, self._cameras, timedelta(minutes=30)),
        ]

    @callback
    def async_stop(self) -> None:
        while self._unsubs:
            self._unsubs.pop()()

    def _save(self) -> None:
        self._store.async_delay_save(lambda: {
            "batteries": self.batteries, "heating": self.heating, "drops": self.drops,
            "fights": self.fights, "cams": self.cams,
        }, 10)

    # ----- automations fighting ----------------------------------------------------------
    @callback
    def _automation(self, event: Event) -> None:
        now = dt_util.utcnow()
        self._ctx[event.context.id] = (str(event.data.get("name") or event.data.get("entity_id")), now)
        if len(self._ctx) > 200:
            self._ctx = {k: v for k, v in self._ctx.items() if now - v[1] < timedelta(minutes=5)}

    def _who(self, ctx) -> str | None:
        if ctx.id in self._ctx:
            return self._ctx[ctx.id][0]
        if ctx.parent_id and ctx.parent_id in self._ctx:
            return self._ctx[ctx.parent_id][0]
        rt = self._rt()
        parent = getattr(getattr(rt, "autooff", None), "_parent", None)
        if ctx.parent_id and parent is not None and ctx.parent_id == parent.id:
            return "Sentient auto-off"
        return None

    @callback
    def _changed(self, event: Event) -> None:
        new, old = event.data.get("new_state"), event.data.get("old_state")
        if new is None or old is None or new.domain not in FIGHT_DOMAINS or new.state == old.state:
            return
        if new.state in ("unavailable", "unknown") or old.state in ("unavailable", "unknown"):
            return
        who = self._who(new.context)
        now = dt_util.utcnow()
        before = self._last.get(new.entity_id)
        if who is None:
            self._last.pop(new.entity_id, None)  # a person or the device: not a fight
            return
        self._last[new.entity_id] = (who, new.state, now)
        if before is None or before[0] == who or before[1] == new.state or now - before[2] > FIGHT_WINDOW:
            return
        key = "|".join(sorted((before[0], who))) + "|" + new.entity_id
        times = [t for t in self.fights.get(key, []) if now - dt_util.parse_datetime(t) < timedelta(days=1)]
        self.fights[key] = times + [now.isoformat()]
        self._save()
        if len(self.fights[key]) == FIGHTS and (rt := self._rt()) is not None:
            rt.health.async_check()  # show it now, not at the next five-minute check

    # ----- devices dropping offline --------------------------------------------------------
    @callback
    def _health(self, event: Event) -> None:
        now_names = {k.split(":", 1)[1] for k in (event.data.get("problems") or {}) if k.startswith("offline:")}
        if self._offline is None:
            self._offline = now_names  # the first look after a start: nothing has "just" dropped
            return
        new = now_names - self._offline
        self._offline = now_names
        if not new:
            return
        now = dt_util.utcnow()
        for name in new:
            self.drops[name] = [t for t in self.drops.get(name, []) if now - dt_util.parse_datetime(t) < WEEK] + [now.isoformat()]
        self._save()

    # ----- daily readings ------------------------------------------------------------------
    @callback
    def _daily(self, _now: datetime) -> None:
        self.sample()

    def sample(self, day: str | None = None) -> None:
        day = day or dt_util.now().date().isoformat()
        for st in self.hass.states.async_all("sensor"):
            if st.attributes.get("device_class") != "battery":
                continue
            try:
                level = float(st.state)
            except ValueError:
                continue
            rows = [r for r in self.batteries.get(st.entity_id, []) if r[0] != day] + [[day, level]]
            self.batteries[st.entity_id] = rows[-60:]
        rt = self._rt()
        if rt is not None:
            for entity_id, zone in rt.heating.zones.items():
                if zone.get("rate"):
                    rows = [r for r in self.heating.get(entity_id, []) if r[0] != day] + [[day, zone["rate"]]]
                    self.heating[entity_id] = rows[-90:]
        self._save()

    # ----- frozen cameras --------------------------------------------------------------------
    @callback
    def _cameras(self, _now: datetime) -> None:
        self.hass.async_create_background_task(self.async_check_cameras(), "sentient_camera_watchdog")

    async def async_check_cameras(self) -> None:
        from homeassistant.components.camera import CameraEntityFeature, async_get_image

        now = dt_util.utcnow()
        for st in self.hass.states.async_all("camera"):
            if st.state in ("unavailable", "unknown") or not int(st.attributes.get("supported_features", 0)) & CameraEntityFeature.STREAM:
                continue  # snapshot cameras (one picture per detection) never "move"
            try:
                image = await async_get_image(self.hass, st.entity_id, timeout=10)
            except Exception:  # noqa: BLE001 - a camera that can't give a picture shows as offline elsewhere
                continue
            digest = hashlib.md5(image.content).hexdigest()  # noqa: S324 - not for security
            seen = self.cams.get(st.entity_id)
            if seen is None or seen.get("hash") != digest:
                self.cams[st.entity_id] = {"hash": digest, "since": now.isoformat()}
        self._save()

    # ----- batteries: when they'll run out ------------------------------------------------------
    def battery_forecasts(self, within_days: int = 60) -> list[dict[str, Any]]:
        """Batteries that will run out within about two months at their recent rate, soonest first."""
        today = dt_util.now()
        out = []
        for entity_id, rows in self.batteries.items():
            rate = drain_rate(rows, 0, 30, today)
            st = self.hass.states.get(entity_id)
            if not rate or rate <= 0 or st is None:
                continue
            try:
                level = float(st.state)
            except ValueError:
                continue
            days = level / rate
            if days <= within_days:
                out.append({"entity_id": entity_id, "name": st.name, "level": round(level), "days": round(days),
                            "text": f"{st.name}: about {_when(days)} left ({round(level)}%)"})
        return sorted(out, key=lambda b: b["days"])

    # ----- what to report -------------------------------------------------------------------
    def issues(self) -> list[dict[str, Any]]:
        rt = self._rt()
        now = dt_util.utcnow()
        today = dt_util.now()
        out: list[dict[str, Any]] = []

        def name(entity_id: str) -> str:
            st = self.hass.states.get(entity_id)
            return st.name if st else entity_id

        if rt is not None:
            for entity_id, runs in rt.appliances.runs.items():
                if len(runs) >= 8:
                    recent, before = median(runs[-3:]), median(runs[:-3])
                    if recent >= before * SLOWER_BY and recent - before >= SLOWER_MIN:
                        out.append({"kind": "wear", "entity_id": entity_id, "name": f"{name(entity_id)} slower",
                                    "text": f"{name(entity_id)} is taking longer to finish than it used to "
                                            f"(about {round(recent)} min, was {round(before)})"})
            for entity_id, times in rt.appliances.errors.items():
                week = [t for t in times if now - dt_util.parse_datetime(t) < WEEK]
                if len(week) >= STUCK_TIMES:
                    out.append({"kind": "wear", "entity_id": entity_id, "name": f"{name(entity_id)} stuck",
                                "text": f"{name(entity_id)} has got stuck or not made it back {len(week)} fois cette semaine"})
        for entity_id, rows in self.batteries.items():
            recent, before = drain_rate(rows, 0, 14, today), drain_rate(rows, 15, 45, today)
            if recent and before and recent >= DRAIN_MIN and recent >= before * DRAIN_FASTER:
                out.append({"kind": "wear", "entity_id": entity_id, "name": f"{name(entity_id)} draining",
                            "text": f"{name(entity_id)} is running down about {recent / before:.0f}× faster than it used to"})
        for dev, times in self.drops.items():
            week = [t for t in times if now - dt_util.parse_datetime(t) < WEEK]
            if len(week) >= DROPS:
                out.append({"kind": "wear", "name": f"{dev} dropping",
                            "text": f"{dev} est tombé hors ligne {len(week)} fois cette semaine"})
        for entity_id, rows in self.heating.items():
            old = [r[1] for r in rows if 28 <= _days_ago(r[0], today) <= 60]
            if len(old) >= 3 and rows and rows[-1][1] <= median(old) * HEATING_SLOWER:
                out.append({"kind": "wear", "entity_id": entity_id, "name": f"{name(entity_id)} warming",
                            "text": f"{name(entity_id)} is warming up slower than a month ago "
                                    f"({rows[-1][1]:.1f}° an hour, was {median(old):.1f}°)"})
        for key, times in self.fights.items():
            day = [t for t in times if now - dt_util.parse_datetime(t) < timedelta(days=1)]
            if len(day) >= FIGHTS:
                a, b, entity_id = key.split("|", 2)
                out.append({"kind": "fight", "entity_id": entity_id, "name": f"{name(entity_id)} fight",
                            "text": f"{a} and {b} keep switching {name(entity_id)} back and forth ({len(day)} times today)"})
        for entity_id, seen in self.cams.items():
            since = dt_util.parse_datetime(seen.get("since") or "")
            st = self.hass.states.get(entity_id)
            if since and st is not None and st.state not in ("unavailable", "unknown") and now - since >= FROZEN_FOR:
                hours = int((now - since).total_seconds() // 3600)
                out.append({"kind": "frozen", "entity_id": entity_id, "name": f"{name(entity_id)} frozen",
                            "text": f"{name(entity_id)}'s picture hasn't changed for {hours} hours — it may be frozen",
                            "since": since.isoformat()})
        return out
