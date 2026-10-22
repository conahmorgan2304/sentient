"""Night watch: a door or gate opening when it normally never does.

No AI, nothing fixed about any house:

* it learns from the last four weeks when each watched door, gate or window
  usually opens — weekdays and weekends separately;
* an opening at a time it has (almost) never opened before is "unusual";
* if somebody was moving about inside just before (someone up and leaving, like
  going to work at ten to seven), it's the household: noted on the timeline only;
* if nobody was — the gate at 1am with the house asleep — it's an alert: on the
  timeline, to phones, and optionally said out loud straight through quiet hours.

Until there's a week of history, anything opening in quiet hours with nobody up
counts as unusual.
"""

from __future__ import annotations

from datetime import datetime, timedelta
import logging
from typing import Any, Callable

from homeassistant.const import EVENT_STATE_CHANGED
from homeassistant.core import Event, EventStateChangedData, HomeAssistant, State, callback
from homeassistant.helpers.event import async_track_time_change
from homeassistant.util import dt as dt_util

from .const import (
    CONF_LEARN_ENTITIES,
    CONF_NIGHT_WATCH,
    CONF_NIGHT_WATCH_ANNOUNCE,
    CONF_NIGHT_WATCH_ENTITIES,
    CONF_NIGHT_WATCH_SPEAKERS,
    DEFAULT_NIGHT_WATCH,
)
from .presence import inside_sensors

_LOGGER = logging.getLogger(__name__)

WINDOW_DAYS = 28
MIN_DAYS = 7
NEAR_MIN = 45  # an opening within this many minutes of the same time counts as "the same time"
RARE_SHARE = 0.1  # opened at this time on fewer than 10% of days...
RARE_COUNT = 1  # ...and no more than once
INSIDE_BEFORE = timedelta(minutes=10)
INSIDE_ROOMS = 2  # sensors with fresh movement in that time
REPEAT = timedelta(minutes=10)
OPEN_CLASSES = {"door", "window", "garage_door", "opening", "gate"}


def _openings(states: list[State]) -> list[datetime]:
    out, prev = [], None
    for st in states:
        if st.state in ("unavailable", "unknown"):
            continue
        if st.state == "on" and prev == "off":
            out.append(dt_util.as_local(st.last_changed))
        prev = st.state
    return out


def how_usual(openings: list[datetime], when: datetime, days_known: int) -> dict[str, Any]:
    """How often this opened around this time of day on the same kind of day."""
    when = dt_util.as_local(when)
    weekend = when.weekday() >= 5
    minute = when.hour * 60 + when.minute
    same_days = max(1, round(days_known * (2 / 7 if weekend else 5 / 7)))
    near_days = set()
    for t in openings:
        if (t.weekday() >= 5) != weekend:
            continue
        diff = abs(t.hour * 60 + t.minute - minute)
        if min(diff, 1440 - diff) <= NEAR_MIN:
            near_days.add(t.date())
    times = len(near_days)
    return {"times": times, "share": times / same_days, "rare": times <= RARE_COUNT and times / same_days < RARE_SHARE}


class NightWatch:
    """Watches doors and gates for openings at times they never normally open."""

    def __init__(self, hass: HomeAssistant, options: dict[str, Any], runtime: Callable[[], Any]) -> None:
        self.hass = hass
        self.options = options
        self._rt = runtime
        self.history: dict[str, list[datetime]] = {}
        self.days_known = 0
        self.events: list[dict[str, Any]] = []  # recent unusual openings
        self._last_alert: dict[str, datetime] = {}
        self._unsubs: list = []

    @property
    def enabled(self) -> bool:
        return bool(self.options.get(CONF_NIGHT_WATCH, DEFAULT_NIGHT_WATCH))

    @property
    def entities(self) -> list[str]:
        if chosen := self.options.get(CONF_NIGHT_WATCH_ENTITIES):
            return list(chosen)
        return [
            s.entity_id for s in self.hass.states.async_all("binary_sensor")
            if s.attributes.get("device_class") in OPEN_CLASSES
        ]

    @callback
    def async_start(self) -> None:
        self._unsubs.append(self.hass.bus.async_listen(EVENT_STATE_CHANGED, self._on_state_changed))
        self._unsubs.append(async_track_time_change(self.hass, self._nightly, hour=3, minute=50, second=0))
        from homeassistant.helpers.start import async_at_started

        @callback
        def _started(_hass: HomeAssistant) -> None:  # once every door sensor exists
            self.hass.async_create_background_task(self.async_learn(), "sentient_night_watch_learn")

        self._unsubs.append(async_at_started(self.hass, _started))

    @callback
    def async_stop(self) -> None:
        while self._unsubs:
            self._unsubs.pop()()

    @callback
    def _nightly(self, _now: datetime) -> None:
        self.hass.async_create_background_task(self.async_learn(), "sentient_night_watch_learn")

    async def async_learn(self) -> None:
        if "recorder" not in self.hass.config.components or not self.entities:
            return
        from homeassistant.components.recorder import get_instance, history

        start = dt_util.utcnow() - timedelta(days=WINDOW_DAYS)
        try:
            rows: dict[str, list[State]] = await get_instance(self.hass).async_add_executor_job(
                lambda: history.get_significant_states(
                    self.hass, start, None, self.entities, significant_changes_only=True, minimal_response=False
                )
            )
        except Exception:  # noqa: BLE001 - best effort
            _LOGGER.warning("Sentient could not read door history for the night watch", exc_info=True)
            return
        self.history = {e: _openings(rows.get(e, [])) for e in self.entities}
        firsts = [s[0].last_changed for s in rows.values() if s]
        self.days_known = min(WINDOW_DAYS, (dt_util.utcnow() - min(firsts)).days) if firsts else 0

    # ----- deciding -----------------------------------------------------------------
    def someone_inside(self, before: datetime) -> str | None:
        """Someone up and about inside in the last few minutes: fresh movement in two different
        places, or a light or switch turned on by hand. A pet crossing one sensor, or a sensor
        that flickers all night, isn't someone getting up to leave."""
        since = before - INSIDE_BEFORE
        for entity_id in self.options.get(CONF_LEARN_ENTITIES) or []:
            st = self.hass.states.get(entity_id)
            if (
                st is not None and st.domain in ("light", "switch") and st.state == "on"
                and since <= st.last_changed <= before and st.context.parent_id is None
            ):
                return st.name
        tracker = self._rt().tracker
        if tracker is None:
            return None
        movers = tracker.fresh_movers(inside_sensors(self.hass, self.options), since)
        if len(movers) < INSIDE_ROOMS:
            return None
        names = [st.name for e in movers if (st := self.hass.states.get(e))]
        return " and ".join(sorted(names)[:2]) or "inside"

    def judge(self, entity_id: str, when: datetime) -> dict[str, Any]:
        rt = self._rt()
        if self.days_known >= MIN_DAYS:
            usual = how_usual(self.history.get(entity_id, []), when, self.days_known)
            unusual = usual["rare"]
        else:
            usual = {"times": None}
            unusual = rt.speech.quiet_now()
        return {"unusual": unusual, "inside": self.someone_inside(when), **usual}

    @callback
    def _on_state_changed(self, event: Event[EventStateChangedData]) -> None:
        new, old = event.data["new_state"], event.data["old_state"]
        if new is None or old is None or not self.enabled or new.entity_id not in self.entities:
            return
        if new.state != "on" or old.state != "off":
            return
        verdict = self.judge(new.entity_id, new.last_changed)
        self.history.setdefault(new.entity_id, []).append(dt_util.as_local(new.last_changed))
        if not verdict["unusual"]:
            return
        local = dt_util.as_local(new.last_changed)
        rt = self._rt()
        record = {"time": local.isoformat(), "entity_id": new.entity_id, "name": new.name, "inside": verdict["inside"]}
        self.events = [record, *self.events][:30]
        if verdict["inside"]:
            rt.timeline.add("noticed", f"{new.name} opened at {local:%H:%M}, not a usual time",
                            f"someone was up inside first ({verdict['inside']}), so not alerting")
            return
        now = dt_util.utcnow()
        if (last := self._last_alert.get(new.entity_id)) and now - last < REPEAT:
            return
        self._last_alert[new.entity_id] = now
        record["alert"] = True
        text = f"{new.name} opened at {local:%H:%M}. Nobody was moving inside and it doesn't normally open at this time."
        rt.timeline.add("security", text, "night watch", alert=True)
        if self.options.get(CONF_NIGHT_WATCH_ANNOUNCE):
            spoken = f"Heads up: the {new.name.lower()} has just opened, and nobody's been moving inside."
            speakers = list(self.options.get(CONF_NIGHT_WATCH_SPEAKERS) or []) or None
            self.hass.async_create_task(rt.speech.async_say(spoken, "urgent", speakers, source="night_watch"))

    def overnight(self, hours: int = 12) -> list[str]:
        cutoff = dt_util.now() - timedelta(hours=hours)
        out = []
        for e in self.events:
            when = dt_util.parse_datetime(e["time"])
            if when and when >= cutoff:
                out.append(f"{e['name']} opened at {when:%H:%M}" + (" with nobody up (alerted)" if e.get("alert") else " (someone was up)"))
        return out

    def describe(self) -> dict[str, Any]:
        return {
            "on": self.enabled,
            "announces": bool(self.options.get(CONF_NIGHT_WATCH_ANNOUNCE)),
            "watching": [st.name if (st := self.hass.states.get(e)) else e for e in self.entities],
            "days_of_history": self.days_known,
            "learning": self.days_known < MIN_DAYS,
            "recent_unusual": self.overnight(72),
        }
