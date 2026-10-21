"""Pure rules: quiet hours and message cleaning."""

from datetime import datetime, time

from custom_components.sentient.speech import clean_message, in_quiet_hours


def test_quiet_hours_over_midnight():
    start, end = time(21), time(8)
    assert in_quiet_hours(datetime(2026, 1, 1, 22, 0), start, end)
    assert in_quiet_hours(datetime(2026, 1, 1, 7, 59), start, end)
    assert not in_quiet_hours(datetime(2026, 1, 1, 8, 0), start, end)
    assert not in_quiet_hours(datetime(2026, 1, 1, 20, 59), start, end)


def test_quiet_hours_same_day_and_disabled():
    assert in_quiet_hours(datetime(2026, 1, 1, 13, 0), time(12), time(14))
    assert not in_quiet_hours(datetime(2026, 1, 1, 13, 0), time(9), time(9))


def test_clean_message_strips_forms_of_address():
    banned = ["sir", "madam"]
    assert clean_message("Sir, the gate is open.", banned) == "The gate is open."
    assert clean_message("The gate is open, sir.", banned) == "The gate is open."
    assert clean_message("Good evening sir, dinner is ready", banned) == "Good evening, dinner is ready"
    assert clean_message("Sirius is bright tonight", banned) == "Sirius is bright tonight"
    assert clean_message("sir", banned) == ""
