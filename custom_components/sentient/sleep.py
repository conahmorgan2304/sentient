"""Asleep or awake: the house knows when everyone's gone to bed and when someone's up.

* **Asleep** — a bedtime routine ran ("goodnight"), or it's quiet hours and nothing inside has moved
  for a while (20 minutes by default).
* **Awake** — in the morning (from 05:00), movement (or a light switched on by hand) in two different
  rooms within ten minutes — one trip to the bathroom doesn't count.

A sensor shows it, it goes on the timeline, voices know it ("the house is asleep — keep it down"),
and you can choose actions for each (screens off and the Echo speakers off at bedtime, the kettle
on when someone's up). Plain rules, no AI.
"""

from __future__ import annotations

from collections import deque
from datetime import datetime, time, timedelta
import logging
from typing import Any, Callable

from homeassistant.const import EVENT_STATE_CHANGED
from homeassistant.core import Event, HomeAssistant, callback
from homeassistant.helpers.event import async_track_time_interval
from homeassistant.helpers.storage import Store
from homeassistant.util import dt as dt_util

from .const import (
    CONF_BEDTIME_ROUTINES,
    CONF_SLEEP,
    CONF_SLEEP_ACTIONS,
    CONF_SLEEP_IGNORE,
    CONF_SLEEP_MINUTES,
    CONF_WAKE_ACTIONS,
    DEFAULT_SLEEP_MINUTES,
    DOMAIN,
)
from .presence import area_of, inside_sensors

_LOGGER = logging.getLogger(__name__)

EVENT_SLEEP = f"{DOMAIN}_sleep"
EARLIEST_UP = time(5, 0)
UP_WINDOW = timedelta(minutes=10)
# a sensor that is on for most of the day (a radar that sees a curtain, a PIR pointed at a heater)
# says nothing about whether anyone is up: judged on a day of once-a-minute samples
NEVER_RESTS = 0.8
SAMPLES = 1440
MIN_SAMPLES = 360
# back in bed within this long of getting up is the same night (up for the loo, a cat setting off the hall at 5am):
# one bedtime and one getting-up time, not a 5am "bedtime" (live 14 Nov: usual bedtime learned as 06:15)
BACK_TO_BED = timedelta(hours=3)


def one_per_night(history: list[dict[str, str]]) -> list[dict[str, str]]:
    """Join a night's going back to bed onto the night it belongs to: first bedtime, last getting up."""
    out: list[dict[str, str]] = []
    for row in history:
        slept = dt_util.parse_datetime(row.get("slept") or "")
        woke = dt_util.parse_datetime(out[-1].get("woke") or "") if out else None
        if out and slept is not None and woke is not None and timedelta(0) <= slept - woke < BACK_TO_BED:
            merged = {"slept": out[-1]["slept"]}
            if row.get("woke"):
                merged["woke"] = row["woke"]
            out[-1] = merged
        else:
            out.append(dict(row))
    return out


class Sleep:
    def __init__(self, hass: HomeAssistant, entry_id: str, options: dict[str, Any], rt_getter: Callable[[], Any]) -> None:
        self.hass = hass
        self.options = options
        self._rt = rt_getter
        self._store: Store[dict[str, Any]] = Store(hass, 1, f"{DOMAIN}.{entry_id}.sleep")
        self.asleep = False
        self.since: str | None = None
        self._moves: list[tuple[datetime, str]] = []
        self._samples: dict[str, deque[bool]] = {}
        self.history: list[dict[str, str]] = []
        self._restless_seen: frozenset[str] = frozenset()
        self.listeners: list[Callable[[], None]] = []  # told when the never-resting list changes
        self._unsubs: list = []

    async def async_start(self) -> None:
        data = await self._store.async_load() or {}
        self.asleep, self.since = bool(data.get("asleep")), data.get("since")
        self.history: list[dict[str, str]] = one_per_night(data.get("history", []))  # [{"slept": iso, "woke": iso}]
        self._unsubs.append(self.hass.bus.async_listen(EVENT_STATE_CHANGED, self._changed))
        self._unsubs.append(async_track_time_interval(self.hass, self._tick, timedelta(minutes=1)))

    @callback
    def async_stop(self) -> None:
        while self._unsubs:
            self._unsubs.pop()()

    @property
    def enabled(self) -> bool:
        return bool(self.options.get(CONF_SLEEP, True))

    def woke_on(self, day) -> int | None:
        """Minutes after midnight the house got up on that date (local), or None."""
        for h in reversed(self.history):
            if (t := dt_util.parse_datetime(h.get("woke") or "")) is not None:
                local = dt_util.as_local(t)
                if local.date() == day:
                    return local.hour * 60 + local.minute
        return None

    def usual(self) -> dict[str, str | None]:
        """Usual bedtime and getting-up time over the last three weeks (median, local)."""
        from statistics import median

        def mins(key: str, shift: int) -> list[float]:
            out = []
            for h in self.history:
                if (t := dt_util.parse_datetime(h.get(key) or "")) is not None:
                    local = dt_util.as_local(t)
                    m = local.hour * 60 + local.minute
                    out.append(m + (1440 if shift and m < 12 * 60 else 0))  # after midnight counts as late, not early
            return out

        def fmt(values: list[float]) -> str | None:
            if len(values) < 3:
                return None
            m = int(median(values)) % 1440
            return f"{m // 60:02d}:{m % 60:02d}"

        return {"bedtime": fmt(mins("slept", 1)), "up": fmt(mins("woke", 0))}

    def describe(self) -> dict[str, Any]:
        since = dt_util.parse_datetime(self.since or "")
        return {"asleep": self.asleep, "since": dt_util.as_local(since).strftime("%H:%M") if since else None,
                "since_at": self.since, **{f"usual_{k}": v for k, v in self.usual().items()},
                "ignoring": self.never_rests()}

    # ----- sensors that never rest ---------------------------------------------------------
    def _sample(self, sensors: list[Any]) -> None:
        for s in sensors:
            self._samples.setdefault(s.entity_id, deque(maxlen=SAMPLES)).append(s.state == "on")

    def never_rests(self) -> list[dict[str, Any]]:
        """Sensors on for most of the last day: they can't say whether the house is asleep or up."""
        out = []
        for eid, d in self._samples.items():
            if len(d) >= MIN_SAMPLES and (share := sum(d) / len(d)) >= NEVER_RESTS:
                st = self.hass.states.get(eid)
                out.append({"entity_id": eid, "name": st.name if st else eid, "on_share": round(share, 2)})
        return out

    def _restless(self) -> set[str]:
        return {r["entity_id"] for r in self.never_rests()}

    # ----- going to sleep ----------------------------------------------------------------
    def is_bedtime_routine(self, name: str) -> bool:
        chosen = [n.strip().lower() for n in (self.options.get(CONF_BEDTIME_ROUTINES) or "").split(",") if n.strip()]
        name = (name or "").lower()
        if chosen:
            return any(c in name for c in chosen)
        return "night" in name or "bed" in name

    @callback
    def routine_ran(self, name: str) -> None:
        if self.enabled and not self.asleep and self.is_bedtime_routine(name):
            self.hass.async_create_task(self.async_set(True, f"the {name} routine"), eager_start=False)

    @callback
    def _tick(self, now: datetime) -> None:
        rt = self._rt()
        if not self.enabled or rt is None:
            return
        # a bedroom PIR that fires as people turn over says nothing about bedtime, and a sensor that is
        # offline says nothing at all
        ignore = set(self.options.get(CONF_SLEEP_IGNORE) or [])
        sensors = [s for e in inside_sensors(self.hass, self.options) if e not in ignore
                   and (s := self.hass.states.get(e)) and s.state not in ("unavailable", "unknown")]
        self._sample(sensors)
        if (now_restless := frozenset(self._restless())) != self._restless_seen:
            self._restless_seen = now_restless
            for cb in list(self.listeners):
                cb()
        if self.asleep or not rt.speech.quiet_hours_now():
            return
        restless = self._restless()
        sensors = [s for s in sensors if s.entity_id not in restless]
        if not sensors or any(s.state == "on" for s in sensors):
            return
        quiet_for = now - max(s.last_changed for s in sensors)
        if quiet_for >= timedelta(minutes=float(self.options.get(CONF_SLEEP_MINUTES, DEFAULT_SLEEP_MINUTES))):
            self.hass.async_create_task(self.async_set(True, "nothing moving downstairs or up"), eager_start=False)

    # ----- waking up -------------------------------------------------------------------
    @callback
    def _changed(self, event: Event) -> None:
        if not self.enabled or not self.asleep:
            return
        new, old = event.data.get("new_state"), event.data.get("old_state")
        if new is None or old is None or new.state == old.state:
            return
        now = dt_util.now()
        if now.time() < EARLIEST_UP or now.time() > time(12, 0):
            return
        by_hand = new.domain == "light" and new.state == "on" and not new.context.parent_id
        if not by_hand and (new.state != "on" or new.entity_id not in inside_sensors(self.hass, self.options)
                            or new.entity_id in set(self.options.get(CONF_SLEEP_IGNORE) or [])
                            or new.entity_id in self._restless()):
            return  # a bedroom sensor stirring isn't anyone up: it takes movement in two other rooms (or a light)
        area = area_of(self.hass, new.entity_id) or new.entity_id
        cutoff = now - UP_WINDOW
        self._moves = [(t, a) for t, a in self._moves if t > cutoff] + [(now, area)]
        if len({a for _, a in self._moves}) >= 2:
            self.hass.async_create_task(self.async_set(False, "moving about in two rooms"), eager_start=False)

    # ----- changing --------------------------------------------------------------------
    async def async_set(self, asleep: bool, why: str = "") -> dict[str, Any]:
        if asleep == self.asleep:
            return self.describe()
        self.asleep = asleep
        self.since = dt_util.utcnow().isoformat()
        self._moves = []
        if asleep:
            last = self.history[-1] if self.history else {}
            woke = dt_util.parse_datetime(last.get("woke") or "")
            if woke is not None and dt_util.utcnow() - woke < BACK_TO_BED:
                last.pop("woke")  # back to bed: the same night carries on
            else:
                self.history.append({"slept": self.since})
        elif self.history and "woke" not in self.history[-1]:
            self.history[-1]["woke"] = self.since
        del self.history[:-21]
        self._store.async_delay_save(lambda: {"asleep": self.asleep, "since": self.since, "history": self.history}, 1)
        rt = self._rt()
        if rt is not None:
            rt.timeline.add("mode", "House asleep" if asleep else "Up and about", why or None)
        self.hass.bus.async_fire(EVENT_SLEEP, {"asleep": asleep, "why": why})
        actions = self.options.get(CONF_SLEEP_ACTIONS if asleep else CONF_WAKE_ACTIONS) or []
        if actions:
            from .shortcuts import async_run_actions

            try:
                await async_run_actions(self.hass, actions, "bedtime" if asleep else "morning")
            except Exception as err:  # noqa: BLE001
                _LOGGER.warning("Sentient %s actions failed: %s", "bedtime" if asleep else "morning", err)
        return self.describe()
