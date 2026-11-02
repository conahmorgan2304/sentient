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


def test_temperatures_said_as_whole_degrees():
    assert clean_message("It is getting a bit chilly, only 18.2 degrees.", []) == "It is getting a bit chilly, only 18 degrees."
    assert clean_message("It's 15.6°C in here", []) == "It's 16°C in here"
    assert clean_message("It used 2.4 kWh today", []) == "It used 2.4 kWh today"  # other numbers left alone


async def test_same_remark_not_repeated_within_hours(hass) -> None:
    from datetime import timedelta
    from unittest.mock import patch

    from homeassistant.util import dt as dt_util

    from custom_components.sentient.speech import SpeechRouter

    router = SpeechRouter(hass, {"speakers": [], "quiet_start": "00:00:00", "quiet_end": "00:00:00"})
    router._remember_said("The kettle has finished boiling.")
    assert router._gate("The kettle has finished boiling.", "normal", "notice").reason == "said_recently"
    assert router._gate("The kettle has finished boiling.", "normal", "announce:Kettle", check_duplicate=False) is None  # only the noticer's remarks
    later = dt_util.utcnow() + timedelta(hours=4)
    with patch("custom_components.sentient.speech.dt_util.utcnow", return_value=later):
        assert router._gate("The kettle has finished boiling.", "normal", "notice", check_duplicate=False) is None
    router.options = {**router.options, "remark_repeat_hours": 0}
    assert router._gate("The kettle has finished boiling.", "normal", "notice", check_duplicate=False) is None
