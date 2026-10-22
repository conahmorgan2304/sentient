"""Where is everyone? A cheap, event-driven activity score per room.

"Newest motion wins" fails for the common case of someone sitting still at a
desk while somebody else walks down the hall. Instead every room gets a score:

* seconds of motion/occupancy in the last *window* minutes, with recent
  seconds counting more than old ones (exponential decay);
* a strong bonus when someone has just spoken to a voice satellite in that
  room — the best evidence there is of where a person is;
* pass-through areas (hallways, landings, stairs) count for a quarter, so
  walking past doesn't steal the announcement.

No polling of sensors: state-change events feed it, and on start-up it is
seeded from the recorder so a restart doesn't wipe what it knew.
"""

from __future__ import annotations

from collections import defaultdict
from datetime import datetime, timedelta
import logging
import math
from typing import Any

from homeassistant.const import EVENT_STATE_CHANGED, STATE_ON
from homeassistant.core import Event, EventStateChangedData, HomeAssistant, State, callback
from homeassistant.helpers import device_registry as dr
from homeassistant.helpers import entity_registry as er
from homeassistant.util import dt as dt_util

from .const import (
    CONF_MOTION_WINDOW,
    CONF_PASS_THROUGH_AREAS,
    CONF_SPEAKERS,
    DEFAULT_MOTION_WINDOW,
)

_LOGGER = logging.getLogger(__name__)

PRESENCE_CLASSES = {"motion", "occupancy", "presence"}
LISTENING = "listening"
VOICE_BONUS = 300.0  # a spoken request is worth five minutes of motion
PASS_THROUGH_WEIGHT = 0.25
ACTIVE_BONUS = 30.0  # a sensor that is on right now: someone is there this moment
MIN_SCORE = 20.0  # below this nobody is confidently anywhere -> default speaker


def area_of(hass: HomeAssistant, entity_id: str) -> str | None:
    """Area of an entity, directly or through its device."""
    entry = er.async_get(hass).async_get(entity_id)
    if entry is None:
        return None
    if entry.area_id:
        return entry.area_id
    if entry.device_id:
        device = dr.async_get(hass).async_get(entry.device_id)
        if device:
            return device.area_id
    return None


def _decayed_seconds(start: datetime, end: datetime, now: datetime, tau: float) -> float:
    """Integral of exp(-(now - t) / tau) dt over [start, end]."""
    if end <= start:
        return 0.0
    a = (now - end).total_seconds()
    b = (now - start).total_seconds()
    return tau * (math.exp(-a / tau) - math.exp(-b / tau))


class ActivityTracker:
    """Keeps a short history of presence and voice activity per area."""

    def __init__(self, hass: HomeAssistant, options: dict[str, Any]) -> None:
        self.hass = hass
        self.options = options
        # entity_id -> list of [start, end | None]
        self._intervals: dict[str, list[list[datetime | None]]] = defaultdict(list)
        # (area_id, when) of voice interactions
        self._voice: list[tuple[str, datetime]] = []
        self._unsub = None

    # ----- settings ---------------------------------------------------------------
    @property
    def window(self) -> timedelta:
        return timedelta(minutes=max(1.0, float(self.options.get(CONF_MOTION_WINDOW, DEFAULT_MOTION_WINDOW))))

    @property
    def pass_through(self) -> set[str]:
        return set(self.options.get(CONF_PASS_THROUGH_AREAS) or [])

    @property
    def speakers(self) -> list[str]:
        return list(self.options.get(CONF_SPEAKERS) or [])

    # ----- lifecycle ----------------------------------------------------------------
    async def async_start(self) -> None:
        self._seed_from_states()
        await self._async_seed_from_history()
        self._unsub = self.hass.bus.async_listen(EVENT_STATE_CHANGED, self._on_state_changed)

    @callback
    def async_stop(self) -> None:
        if self._unsub:
            self._unsub()
            self._unsub = None

    # ----- feeding ------------------------------------------------------------------
    @staticmethod
    def is_presence(state: State | None) -> bool:
        return (
            state is not None
            and state.domain == "binary_sensor"
            and state.attributes.get("device_class") in PRESENCE_CLASSES
        )

    def _seed_from_states(self) -> None:
        for state in self.hass.states.async_all("binary_sensor"):
            if self.is_presence(state) and state.state == STATE_ON:
                self._intervals[state.entity_id] = [[state.last_changed, None]]

    async def _async_seed_from_history(self) -> None:
        """Rebuild the last window from the recorder so restarts don't forget."""
        if "recorder" not in self.hass.config.components:
            return
        try:
            from homeassistant.components.recorder import get_instance, history
        except ImportError:  # pragma: no cover
            return
        entity_ids = [s.entity_id for s in self.hass.states.async_all("binary_sensor") if self.is_presence(s)]
        entity_ids += [s for s in self.speakers]
        if not entity_ids:
            return
        start = dt_util.utcnow() - self.window
        try:
            states: dict[str, list[State]] = await get_instance(self.hass).async_add_executor_job(
                lambda: history.get_significant_states(
                    self.hass, start, None, entity_ids, significant_changes_only=False, minimal_response=False
                )
            )
        except Exception:  # noqa: BLE001 - history is a nice-to-have
            _LOGGER.debug("Could not seed activity from history", exc_info=True)
            return
        for entity_id, rows in states.items():
            if entity_id in self.speakers:
                area = area_of(self.hass, entity_id)
                if area:
                    self._voice.extend((area, s.last_changed) for s in rows if s.state == LISTENING)
                continue
            intervals: list[list[datetime | None]] = []
            for s in rows:
                if s.state == STATE_ON and (not intervals or intervals[-1][1] is not None):
                    intervals.append([max(s.last_changed, start), None])
                elif s.state != STATE_ON and intervals and intervals[-1][1] is None:
                    intervals[-1][1] = s.last_changed
            if intervals:
                self._intervals[entity_id] = intervals

    @callback
    def _on_state_changed(self, event: Event[EventStateChangedData]) -> None:
        new = event.data["new_state"]
        if new is None:
            return
        entity_id = new.entity_id
        if entity_id in self.speakers:
            if new.state == LISTENING:
                area = area_of(self.hass, entity_id)
                if area:
                    self.note_voice(area, new.last_changed)
            return
        if not self.is_presence(new):
            return
        intervals = self._intervals[entity_id]
        open_now = bool(intervals) and intervals[-1][1] is None
        if new.state == STATE_ON and not open_now:
            intervals.append([new.last_changed, None])
        elif new.state != STATE_ON and open_now:
            intervals[-1][1] = new.last_changed
        self._prune()

    def last_active(self, entity_id: str) -> datetime | None:
        """When this sensor last saw movement (now, if it still does) within the window."""
        intervals = self._intervals.get(entity_id)
        if not intervals:
            return None
        end = intervals[-1][1]
        return dt_util.utcnow() if end is None else end

    def note_voice(self, area_id: str, when: datetime | None = None) -> None:
        self._voice.append((area_id, when or dt_util.utcnow()))
        self._prune()

    def _prune(self) -> None:
        cutoff = dt_util.utcnow() - self.window
        for entity_id in list(self._intervals):
            kept = [i for i in self._intervals[entity_id] if i[1] is None or i[1] > cutoff]
            if kept:
                self._intervals[entity_id] = kept
            else:
                del self._intervals[entity_id]
        self._voice = [v for v in self._voice if v[1] > cutoff]

    # ----- scoring --------------------------------------------------------------------
    def scores(self) -> dict[str, float]:
        """Activity score per area (higher = more likely someone is there now)."""
        now = dt_util.utcnow()
        window = self.window
        cutoff = now - window
        tau = window.total_seconds() / 2
        out: dict[str, float] = defaultdict(float)
        for entity_id, intervals in self._intervals.items():
            area = area_of(self.hass, entity_id)
            if not area:
                continue
            weight = PASS_THROUGH_WEIGHT if area in self.pass_through else 1.0
            for start, end in intervals:
                s = max(start, cutoff)
                e = end or now
                out[area] += weight * _decayed_seconds(s, e, now, tau)
            if intervals and intervals[-1][1] is None:
                out[area] += weight * ACTIVE_BONUS
        for area, when in self._voice:
            if when > cutoff:
                out[area] += VOICE_BONUS * math.exp(-(now - when).total_seconds() / window.total_seconds())
        return {a: round(v, 1) for a, v in out.items()}

    def occupied_areas(self, candidates: set[str] | None = None, share: float = 0.5) -> list[str]:
        """Every convincing area, busiest first: anything within *share* of the top score."""
        scored = sorted(
            ((a, v) for a, v in self.scores().items() if candidates is None or a in candidates),
            key=lambda kv: kv[1], reverse=True,
        )
        if not scored or scored[0][1] < MIN_SCORE:
            return []
        floor = max(MIN_SCORE, scored[0][1] * share)
        return [a for a, v in scored if v >= floor]

    def best_area(self, candidates: set[str] | None = None) -> str | None:
        """The area most likely to have someone in it, if anywhere is convincing."""
        scored = {a: v for a, v in self.scores().items() if candidates is None or a in candidates}
        if not scored:
            return None
        area, score = max(scored.items(), key=lambda kv: kv[1])
        return area if score >= MIN_SCORE else None


def inside_sensors(hass: HomeAssistant, options: dict[str, Any]) -> list[str]:
    """Motion/occupancy sensors inside the house.

    The user's choice if they made one; otherwise sensors in rooms on the same floors as the
    speakers (and pass-through areas) — so garden and camera sensors don't count as "inside".
    """
    from homeassistant.helpers import area_registry as ar

    from .const import CONF_INSIDE_SENSORS

    if chosen := options.get(CONF_INSIDE_SENSORS):
        return list(chosen)
    areas = ar.async_get(hass)
    speaker_areas = {a for s in options.get(CONF_SPEAKERS) or [] if (a := area_of(hass, s))}
    floors = {f for a in speaker_areas if (area := areas.async_get_area(a)) and (f := area.floor_id)}
    inside = set(speaker_areas) | set(options.get(CONF_PASS_THROUGH_AREAS) or [])
    inside |= {a.id for a in areas.async_list_areas() if a.floor_id and a.floor_id in floors}
    return [
        st.entity_id for st in hass.states.async_all("binary_sensor")
        if ActivityTracker.is_presence(st) and area_of(hass, st.entity_id) in inside
    ]
