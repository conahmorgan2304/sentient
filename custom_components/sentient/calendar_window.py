"""Validate calendar event windows consistently before using calendar service responses."""
from __future__ import annotations

from datetime import date, datetime, time, timedelta
from typing import Any

from homeassistant.util import dt as dt_util


def local_midnight(now: datetime, days: int = 0) -> datetime:
    """Compute each boundary as a local date, including short and long DST days."""
    return datetime.combine(dt_util.as_local(now).date() + timedelta(days=days), time.min,
                            tzinfo=dt_util.get_default_time_zone())


def event_start(value: Any) -> datetime | None:
    """Date-only events start at local midnight; timed events retain their actual offset."""
    if not value:
        return None
    raw = str(value)
    try:
        if len(raw) == 10:
            return datetime.combine(date.fromisoformat(raw), time.min, tzinfo=dt_util.get_default_time_zone())
        parsed = dt_util.parse_datetime(raw)
        if parsed is None:
            return None
        if parsed.tzinfo is None:
            parsed = parsed.replace(tzinfo=dt_util.get_default_time_zone())
        return dt_util.as_local(parsed)
    except (TypeError, ValueError, OverflowError):
        return None


def window_events(events: Any, start: datetime, end: datetime) -> list[dict[str, Any]]:
    """Keep only events intersecting [start, end), in chronological order.

    Calendar all-day end dates are exclusive. A missing end is treated as
    a one-day all-day event or a point-in-time timed event, not as indefinite.
    """
    if not isinstance(events, list):
        return []
    lower, upper = dt_util.as_utc(start), dt_util.as_utc(end)
    if upper <= lower:
        return []
    kept = []
    for event in events:
        if not isinstance(event, dict):
            continue
        begins = event_start(event.get("start"))
        if begins is None:
            continue
        finishes = event_start(event.get("end"))
        if event.get("end") and finishes is None:
            continue
        if finishes is None and len(str(event.get("start"))) == 10:
            finishes = begins + timedelta(days=1)
        first = dt_util.as_utc(begins)
        last = dt_util.as_utc(finishes) if finishes else first
        if last < first or first >= upper:
            continue
        if (last > lower and last > first) or (lower <= first < upper and last == first):
            kept.append((first, event))
    kept.sort(key=lambda pair: pair[0])
    return [event for _, event in kept]
