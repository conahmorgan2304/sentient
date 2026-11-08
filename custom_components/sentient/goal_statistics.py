"""Comparable daily goal values from recorder statistics or observed scalar history."""
from __future__ import annotations

from bisect import bisect_left
from datetime import datetime, timedelta
import logging
import math

from homeassistant.core import HomeAssistant
from homeassistant.util import dt as dt_util

from .numeric import finite_float

_LOGGER = logging.getLogger(__name__)
VERSION = 2
TOTALS = {'total', 'total_increasing'}


def boundaries(end: datetime, days: int) -> list[datetime]:
    """UTC boundaries of local calendar days, including 23/25-hour days."""
    midnight = dt_util.as_local(end).replace(hour=0, minute=0, second=0, microsecond=0)
    return [dt_util.as_utc(midnight - timedelta(days=days - i)) for i in range(days + 1)]


def _time(value):
    if isinstance(value, datetime):
        return dt_util.as_utc(value)
    if isinstance(value, str):
        parsed = dt_util.parse_datetime(value)
        return dt_util.as_utc(parsed) if parsed else None
    number = finite_float(value)
    if number is not None:
        try:
            return dt_util.utc_from_timestamp(number)
        except (OverflowError, OSError, ValueError):
            pass
    return None


def _native(hass, entity, bounds, kind, unit):
    from homeassistant.components.recorder.statistics import get_metadata, statistics_during_period

    metadata = get_metadata(hass, statistic_ids={entity}).get(entity)
    if not metadata or kind is None:
        return {}
    meta = metadata[1]
    total = kind in TOTALS
    if total and not meta.get('has_sum'):
        return {}
    units = None
    if meta.get('unit_of_measurement') != unit:
        if not meta.get('unit_class') or unit is None:
            return {}  # never silently mix different units
        units = {meta['unit_class']: unit}
    field = 'change' if total else 'mean'
    rows = statistics_during_period(hass, bounds[0], bounds[-1] - timedelta(microseconds=1),
                                    {entity}, 'day', units, {field}).get(entity, [])
    values = {}
    for row in rows:
        start, end = _time(row.get('start')), _time(row.get('end'))
        value = finite_float(row.get(field))
        if (start not in bounds[:-1] or end is None or end != bounds[bounds.index(start) + 1]
                or value is None or (kind == 'total_increasing' and value < 0)):
            continue
        values[dt_util.as_local(start).date().isoformat()] = value
    return values


def _history(states, bounds, kind, unit):
    """Recorded increments or duration-weighted scalar averages; incomplete days are omitted.

    Counter increments belong to the local day of the report. No cross-midnight interpolation
    or extrapolation of consumption beyond a report is invented.
    """
    ordered = sorted(states, key=lambda s: s.last_updated)
    times = [dt_util.as_utc(s.last_updated) for s in ordered]
    def value(state):
        if state is None or state.attributes.get('unit_of_measurement') != unit:
            return None
        if state.attributes.get('state_class') != kind:
            return None
        number = finite_float(state.state)
        return None if kind == 'total_increasing' and number is not None and number < 0 else number
    out = {}
    for start, end in zip(bounds, bounds[1:]):
        left, right = bisect_left(times, start), bisect_left(times, end)
        current = value(ordered[left - 1]) if left else None
        valid = current is not None
        cursor, terms = start, []
        previous = ordered[left - 1] if left else None
        for index in range(left, right):
            when, reading = times[index], value(ordered[index])
            if kind in TOTALS:
                if current is not None and reading is not None:
                    delta = reading - current
                    reset = (ordered[index].attributes.get('last_reset') is not None
                             and (previous is None or ordered[index].attributes.get('last_reset') != previous.attributes.get('last_reset')))
                    terms.append(reading if reset or kind == 'total_increasing' and delta < 0 else delta)
                elif when != start:
                    valid = False
            elif current is not None:
                terms.append(current * (when - cursor).total_seconds())
            elif when > cursor:
                valid = False
            if reading is None:
                valid = False
            elif when == start and current is None:
                valid = True  # a real starting sample, not an invented zero
            current, cursor, previous = reading, when, ordered[index]
        if current is None:
            valid = False
        if kind not in TOTALS and current is not None:
            terms.append(current * (end - cursor).total_seconds())
        if not valid:
            continue
        try:
            result = math.fsum(terms)
            if kind not in TOTALS:
                result /= (end - start).total_seconds()
        except (OverflowError, ValueError):
            continue
        if finite_float(result) is not None:
            out[dt_util.as_local(start).date().isoformat()] = result
    return out


async def daily_values(hass: HomeAssistant, entity_id: str, days: int, end: datetime | None = None) -> list[tuple[str, float]]:
    if 'recorder' not in hass.config.components:
        return []
    from homeassistant.components.recorder import get_instance, history

    state = hass.states.get(entity_id)
    if state is None:
        return []
    days = max(1, min(int(days), 120))
    bounds = boundaries(end or dt_util.utcnow(), days)
    kind, unit = state.attributes.get('state_class'), state.attributes.get('unit_of_measurement')
    def read():
        try:
            native = _native(hass, entity_id, bounds, kind, unit)
        except Exception:  # unavailable statistics can still have usable raw history
            _LOGGER.debug('Native daily goal statistics unavailable for %s', entity_id, exc_info=True)
            native = {}
        if len(native) == days:
            return sorted(native.items())
        rows = history.get_significant_states(hass, bounds[0], bounds[-1], [entity_id],
            include_start_time_state=True, significant_changes_only=False, minimal_response=False)
        fallback = _history(rows.get(entity_id, []), bounds, kind, unit)
        return sorted({**fallback, **native}.items())
    return await get_instance(hass).async_add_executor_job(read)
