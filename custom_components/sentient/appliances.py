"""Appliances: how long things normally run, when they've finished, and when one seems stuck.

Watches the appliances chosen in Settings — or, if none are chosen, every robot vacuum and any power
sensor whose name says washing machine, dryer or dishwasher. An appliance is running while:
* a power sensor reads more than a few watts (5 W by default) — finished once it has stayed below
  that for five minutes (so a pause mid-wash doesn't count; half a minute for short runs like a kettle);
* anything else is in a working state (on, running, cleaning, washing, heating…) — finished when it
  goes back to rest.

Each finished run's length is kept (the last 20). With three or more it knows the usual length:
* when a run finishes it says so, in plain words with no AI (the AI-driven noticer leaves these
  appliances alone, which saves calls);
* if a run goes on well past the usual (half as long again, and at least 20 minutes more), it says once
  that it seems stuck; a robot vacuum reporting an error says so straight away;
* voices know what's running and roughly when it'll be done ("the wash should be done about 3:40").
"""

from __future__ import annotations

from datetime import datetime, timedelta
import logging
import re
from statistics import median
from typing import Any, Callable

from homeassistant.const import EVENT_STATE_CHANGED
from homeassistant.core import Event, HomeAssistant, State, callback
from homeassistant.helpers.event import async_track_time_interval
from homeassistant.helpers.storage import Store
from homeassistant.util import dt as dt_util

from .const import (
    CONF_APPLIANCE_ANNOUNCE,
    CONF_APPLIANCE_WATTS,
    CONF_APPLIANCES,
    DEFAULT_APPLIANCE_WATTS,
    DOMAIN,
)

_LOGGER = logging.getLogger(__name__)

RESTING = {"off", "idle", "standby", "docked", "stopped", "done", "complete", "completed", "finished", "ready",
           "paused", "charging", "charged", "end", "none", "0"}
IGNORED = {"unavailable", "unknown"}
ERROR = {"error", "fault", "stuck", "jammed"}
MIN_RUN = timedelta(minutes=2)
POWER_SETTLE = timedelta(minutes=5)  # a long run (a wash) may pause: wait this long before calling it done
SHORT_SETTLE = timedelta(seconds=30)  # a short run (a kettle) is done when it's done
LONG_RUN = timedelta(minutes=15)
KEEP_RUNS = 20
STUCK_FACTOR = 1.5
STUCK_EXTRA = timedelta(minutes=20)
AUTO_NAMES = re.compile(r"wash|dryer|drier|dishwasher|tumble", re.I)


def _friendly(name: str) -> str:
    """'Washing machine power' -> 'Washing machine'."""
    return re.sub(r"\s+(power|energy|consumption|current|plug|outlet|socket|state|status)$", "", name.strip(), flags=re.I)


def _mins(td: timedelta | float) -> str:
    m = round(td.total_seconds() / 60 if isinstance(td, timedelta) else td)
    if m < 60:
        return f"{m} min"
    return f"{m // 60}h {m % 60:02d}m" if m % 60 else f"{m // 60}h"


class Appliances:
    def __init__(self, hass: HomeAssistant, entry_id: str, options: dict[str, Any], rt_getter: Callable[[], Any]) -> None:
        self.hass = hass
        self.options = options
        self._rt = rt_getter
        self._store: Store[dict[str, Any]] = Store(hass, 1, f"{DOMAIN}.{entry_id}.appliances")
        self.runs: dict[str, list[float]] = {}  # entity -> minutes of finished runs
        self.running: dict[str, str] = {}  # entity -> iso start
        self._low: dict[str, datetime] = {}  # power sensor -> since when it's been low mid-run
        self._warned: set[str] = set()  # stuck already said for this run
        self.last: dict[str, dict[str, Any]] = {}  # entity -> last finished run
        self._unsubs: list = []

    async def async_start(self) -> None:
        data = await self._store.async_load() or {}
        self.runs = {k: list(v) for k, v in (data.get("runs") or {}).items()}
        self.running = dict(data.get("running") or {})
        self.last = dict(data.get("last") or {})
        self._unsubs.append(self.hass.bus.async_listen(EVENT_STATE_CHANGED, self._changed))
        self._unsubs.append(async_track_time_interval(self.hass, self._tick, timedelta(minutes=1)))
        # anything that stopped while Home Assistant was off
        for entity_id in list(self.running):
            st = self.hass.states.get(entity_id)
            if st is not None and st.state not in IGNORED and not self._working(st):
                self.running.pop(entity_id, None)
        self._save()

    @callback
    def async_stop(self) -> None:
        while self._unsubs:
            self._unsubs.pop()()

    def _save(self) -> None:
        self._store.async_delay_save(lambda: {"runs": self.runs, "running": self.running, "last": self.last}, 5)

    # ----- which, and whether it's working -----------------------------------------------
    def watched(self) -> list[str]:
        chosen = [e for e in self.options.get(CONF_APPLIANCES) or [] if self.hass.states.get(e)]
        if chosen:
            return chosen
        auto = [s.entity_id for s in self.hass.states.async_all("vacuum")]
        auto += [s.entity_id for s in self.hass.states.async_all("sensor")
                 if self._is_power(s) and AUTO_NAMES.search(f"{s.entity_id} {s.name}")]
        return auto

    @staticmethod
    def _is_power(st: State) -> bool:
        return st.domain == "sensor" and (
            st.attributes.get("device_class") == "power" or st.attributes.get("unit_of_measurement") in ("W", "kW")
        )

    def _watts(self, st: State) -> float | None:
        try:
            value = float(st.state)
        except ValueError:
            return None
        return value * 1000 if st.attributes.get("unit_of_measurement") == "kW" else value

    def _working(self, st: State) -> bool:
        if self._is_power(st):
            watts = self._watts(st)
            return watts is not None and watts > float(self.options.get(CONF_APPLIANCE_WATTS, DEFAULT_APPLIANCE_WATTS))
        state = st.state.lower()
        return state not in RESTING and state not in IGNORED and state not in ERROR

    def usual(self, entity_id: str) -> float | None:
        runs = self.runs.get(entity_id) or []
        return median(runs) if len(runs) >= 3 else None

    # ----- watching ----------------------------------------------------------------------
    @callback
    def _changed(self, event: Event) -> None:
        new, old = event.data.get("new_state"), event.data.get("old_state")
        if new is None or new.state in IGNORED or new.entity_id not in self.watched():
            return
        entity_id, now = new.entity_id, dt_util.utcnow()
        if new.state.lower() in ERROR and (old is None or old.state.lower() not in ERROR):
            self.hass.async_create_task(self._async_say(f"{_friendly(new.name)} seems to be stuck.", "stuck", entity_id),
                                        eager_start=False)
        working = self._working(new)
        if working:
            self._low.pop(entity_id, None)
            if entity_id not in self.running:
                self.running[entity_id] = now.isoformat()
                self._warned.discard(entity_id)
                self._save()
            return
        if entity_id not in self.running:
            return
        if self._is_power(new):
            self._low.setdefault(entity_id, now)  # finished once it stays low (checked each minute)
            return
        if new.state.lower() in ERROR:
            return  # not finished: waiting to be helped
        self._finish(entity_id, now)

    @callback
    def _tick(self, now: datetime) -> None:
        now = dt_util.as_utc(now)
        for entity_id, since in list(self._low.items()):
            started = dt_util.parse_datetime(self.running.get(entity_id) or "")
            settle = POWER_SETTLE if started is None or since - started > LONG_RUN else SHORT_SETTLE
            if now - since >= settle:
                self._low.pop(entity_id, None)
                if entity_id in self.running:
                    self._finish(entity_id, since)
        for entity_id, started in list(self.running.items()):
            usual = self.usual(entity_id)
            if usual is None or entity_id in self._warned or entity_id in self._low:
                continue
            ran = now - dt_util.parse_datetime(started)
            if ran > max(timedelta(minutes=usual * STUCK_FACTOR), timedelta(minutes=usual) + STUCK_EXTRA):
                self._warned.add(entity_id)
                st = self.hass.states.get(entity_id)
                name = _friendly(st.name if st else entity_id)
                self.hass.async_create_task(self._async_say(
                    f"{name} has been going {_mins(ran)}; it usually takes about {_mins(usual)}. It may be stuck.",
                    "stuck", entity_id), eager_start=False)

    def _finish(self, entity_id: str, ended: datetime) -> None:
        started = dt_util.parse_datetime(self.running.pop(entity_id, "") or "")
        self._warned.discard(entity_id)
        self._save()
        if started is None:
            return
        ran = ended - started
        if ran < MIN_RUN:
            return  # a blip, or someone pressed it by mistake
        usual = self.usual(entity_id)
        self.runs.setdefault(entity_id, []).append(round(ran.total_seconds() / 60, 1))
        del self.runs[entity_id][:-KEEP_RUNS]
        self.last[entity_id] = {"ended": ended.isoformat(), "minutes": round(ran.total_seconds() / 60)}
        self._save()
        st = self.hass.states.get(entity_id)
        name = _friendly(st.name if st else entity_id)
        rt = self._rt()
        if rt is not None:
            rt.timeline.add("appliance", f"{name} finished",
                            f"ran {_mins(ran)}" + (f" (usually {_mins(usual)})" if usual else ""))
        self.hass.async_create_task(self._async_say(f"{name}'s finished.", "finished", entity_id), eager_start=False)

    async def _async_say(self, text: str, what: str, entity_id: str) -> None:
        rt = self._rt()
        if rt is None or not self.options.get(CONF_APPLIANCE_ANNOUNCE, True):
            return
        if rt.speech.mutes is not None and rt.speech.mutes.entity_muted(entity_id):
            return
        try:
            said = await rt.speech.async_say(text, "normal", None, source=f"appliance:{what}")
            if not said.spoken and said.reason not in ("duplicate", "guest_mode") and hasattr(rt, "followyou"):
                rt.followyou.keep(text, source="appliance", hours=2)
        except Exception as err:  # noqa: BLE001
            _LOGGER.debug("Appliance remark not said: %s", err)

    # ----- reading -----------------------------------------------------------------------
    def listing(self) -> list[dict[str, Any]]:
        out = []
        now = dt_util.utcnow()
        for entity_id in self.watched():
            st = self.hass.states.get(entity_id)
            usual = self.usual(entity_id)
            item: dict[str, Any] = {
                "entity_id": entity_id, "name": st.name if st else entity_id,
                "running": entity_id in self.running, "usual_minutes": round(usual) if usual else None,
                "runs_seen": len(self.runs.get(entity_id) or []),
            }
            if started := self.running.get(entity_id):
                start = dt_util.parse_datetime(started)
                item["started"] = dt_util.as_local(start).strftime("%H:%M")
                item["minutes_so_far"] = round((now - start).total_seconds() / 60)
                if usual:
                    item["done_about"] = dt_util.as_local(start + timedelta(minutes=usual)).strftime("%H:%M")
            if last := self.last.get(entity_id):
                item["last_finished"] = dt_util.as_local(dt_util.parse_datetime(last["ended"])).strftime("%a %H:%M")
            out.append(item)
        return out

    def prompt_line(self) -> str | None:
        busy = [a for a in self.listing() if a["running"]]
        if not busy:
            return None
        return "Running now: " + "; ".join(
            f"{a['name']} since {a['started']}" + (f", usually done about {a['done_about']}" if a.get("done_about") else "")
            for a in busy
        ) + "."
