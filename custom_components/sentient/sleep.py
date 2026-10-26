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


class Sleep:
    def __init__(self, hass: HomeAssistant, entry_id: str, options: dict[str, Any], rt_getter: Callable[[], Any]) -> None:
        self.hass = hass
        self.options = options
        self._rt = rt_getter
        self._store: Store[dict[str, Any]] = Store(hass, 1, f"{DOMAIN}.{entry_id}.sleep")
        self.asleep = False
        self.since: str | None = None
        self._moves: list[tuple[datetime, str]] = []
        self._unsubs: list = []

    async def async_start(self) -> None:
        data = await self._store.async_load() or {}
        self.asleep, self.since = bool(data.get("asleep")), data.get("since")
        self.history: list[dict[str, str]] = data.get("history", [])  # [{"slept": iso, "woke": iso}]
        self._unsubs.append(self.hass.bus.async_listen(EVENT_STATE_CHANGED, self._changed))
        self._unsubs.append(async_track_time_interval(self.hass, self._tick, timedelta(minutes=1)))

    @callback
    def async_stop(self) -> None:
        while self._unsubs:
            self._unsubs.pop()()

    @property
    def enabled(self) -> bool:
        return bool(self.options.get(CONF_SLEEP, True))

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
                "since_at": self.since, **{f"usual_{k}": v for k, v in self.usual().items()}}

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
        if not self.enabled or self.asleep or rt is None or not rt.speech.quiet_hours_now():
            return
        sensors = [s for e in inside_sensors(self.hass, self.options) if (s := self.hass.states.get(e))]
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
        if not by_hand and (new.state != "on" or new.entity_id not in inside_sensors(self.hass, self.options)):
            return
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
