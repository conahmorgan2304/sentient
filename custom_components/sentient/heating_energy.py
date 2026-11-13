"""Evidence-based heating energy comparison from a real cumulative kWh sensor.

This is an observed before/after difference, *not causal savings*. Weather,
occupancy, tariffs, boiler efficiency, heating schedules and missing Recorder
samples can all change usage independently of Sentient's preheating.
"""
from __future__ import annotations

from collections import defaultdict
from datetime import date, datetime, timedelta
import math
from typing import Any


def _numeric(state: Any) -> float | None:
    try:
        value = float(state.state)
        return value if math.isfinite(value) and value >= 0 else None
    except (TypeError, ValueError, AttributeError, OverflowError):
        return None


def compare_cumulative(states: list[Any], changed: date, *,
                       tariff_per_kwh: float | None = None,
                       minimum_days: int = 5) -> dict[str, Any]:
    """Compare matched weekdays during 14 full calendar days either side."""
    if not isinstance(changed, date) or isinstance(changed, datetime):
        return {"status": "invalid_start_date"}
    groups: dict[date, list[tuple[datetime, float]]] = defaultdict(list)
    for state in states[:30000]:
        when = getattr(state, "last_updated", None)
        value = _numeric(state)
        if not isinstance(when, datetime) or when.tzinfo is None or value is None:
            continue
        unit = (getattr(state, "attributes", {}) or {}).get("unit_of_measurement")
        if unit != "kWh":
            continue
        if changed - timedelta(days=14) <= when.date() < changed + timedelta(days=14):
            groups[when.date()].append((when, value))
    amounts = {}
    for day, values in groups.items():
        values.sort()
        # Require long enough daily coverage so a handful of power on/off
        # records cannot look like a complete day's consumption.
        span = (values[-1][0] - values[0][0]).total_seconds() / 3600
        delta = values[-1][1] - values[0][1]
        if len(values) >= 2 and span >= 18 and delta >= 0:
            amounts[day] = round(delta, 3)
    pairs = []
    for day in range(14):
        earlier = changed - timedelta(days=14-day)
        later = changed + timedelta(days=day)
        if earlier in amounts and later in amounts and earlier.weekday() == later.weekday():
            pairs.append((amounts[earlier], amounts[later]))
    if len(pairs) < minimum_days:
        return {"status": "insufficient_comparable_days", "matched_days": len(pairs),
                "observed_change_kwh_per_day": None, "causal_savings_established": False}
    before = sum(x for x, _ in pairs) / len(pairs)
    after = sum(y for _, y in pairs) / len(pairs)
    difference = after - before
    result = {"status": "measured", "matched_days": len(pairs),
              "before_kwh_per_day": round(before, 3), "after_kwh_per_day": round(after, 3),
              "observed_change_kwh_per_day": round(difference, 3),
              "causal_savings_established": False,
              "note": "Observed kWh change only: no weather/occupancy normalization or counterfactual baseline."}
    if isinstance(tariff_per_kwh, (int, float)) and math.isfinite(tariff_per_kwh) and tariff_per_kwh >= 0:
        result["estimated_tariff_change_per_day"] = round(difference * tariff_per_kwh, 3)
        result["tariff_is_user_estimate"] = True
    return result
