"""Measured heating comparisons are never labelled causal cost savings."""
from datetime import datetime, timedelta, timezone
from types import SimpleNamespace

from custom_components.sentient.heating_energy import compare_cumulative


def readings(start, *, kwh_per_day=12, number_of_days=28):
    rows = []
    counter = 300.0
    for d in range(number_of_days):
        date = start + timedelta(days=d)
        rows.append(SimpleNamespace(state=str(counter), last_updated=date + timedelta(hours=1),
                                    attributes={"unit_of_measurement": "kWh"}))
        counter += kwh_per_day
        rows.append(SimpleNamespace(state=str(counter), last_updated=date + timedelta(hours=23),
                                    attributes={"unit_of_measurement": "kWh"}))
    return rows


def test_real_cumulative_kwh_comparison_uses_matched_weekdays():
    start = datetime(2026, 10, 1, tzinfo=timezone.utc)
    records = readings(start, kwh_per_day=12)
    result = compare_cumulative(records, (start + timedelta(days=14)).date(), tariff_per_kwh=.25)
    assert result["status"] == "measured"
    assert result["matched_days"] == 14
    assert result["before_kwh_per_day"] == 12
    assert result["after_kwh_per_day"] == 12
    assert result["observed_change_kwh_per_day"] == 0
    assert result["causal_savings_established"] is False


def test_heating_energy_withholds_claims_for_missing_history_or_wrong_unit():
    start = datetime(2026, 10, 1, tzinfo=timezone.utc)
    records = readings(start, number_of_days=2)
    result = compare_cumulative(records, (start + timedelta(days=14)).date())
    assert result["status"] == "insufficient_comparable_days"
    assert result["observed_change_kwh_per_day"] is None
    wrong = readings(start)
    for x in wrong:
        x.attributes = {"unit_of_measurement": "W"}
    assert compare_cumulative(wrong, (start + timedelta(days=14)).date())["status"] == "insufficient_comparable_days"
