"""Contract tests for the shared evidence-to-decision boundary."""
import pytest

from custom_components.sentient.cognitive import assess_security


@pytest.mark.parametrize(("kwargs", "expected", "secure", "confirmed"), [
    ({}, "clear", True, True),
    ({"open_count": 1}, "problem", False, True),
    ({"unlocked_count": 1}, "problem", False, True),
    ({"alarm_triggered": True}, "problem", False, True),
    ({"unknown_count": 1}, "unknown", None, False),
    ({"perimeter_present": False}, "unknown", None, False),
    ({"access_limited": True}, "unknown", None, False),
    ({"open_count": 1, "unknown_count": 1}, "problem", False, False),
    ({"alarm_triggered": True, "access_limited": True}, "problem", False, False),
])
def test_security_assessment(kwargs, expected, secure, confirmed):
    defaults = dict(open_count=0, unlocked_count=0, alarm_triggered=False,
                    unknown_count=0, perimeter_present=True, access_limited=False)
    result = assess_security(**(defaults | kwargs))
    assert result.verdict == expected
    assert result.secure is secure
    assert result.confirmed is confirmed
    assert isinstance(result.as_dict()["reasons"], list)


def test_reasons_are_explainable_and_no_actuation_is_performed():
    result = assess_security(open_count=1, unlocked_count=0,
                             alarm_triggered=False, unknown_count=2,
                             perimeter_present=True, access_limited=True)
    assert result.reasons == ("opening_reported", "unsettled_readings",
                              "restricted_visibility")
    assert result.as_dict()["verdict"] == "problem"


async def test_security_check_exposes_reasoned_verdict_without_claiming_unknown_safe(hass):
    from custom_components.sentient.security import check

    hass.states.async_set("binary_sensor.gate", "off", {"device_class": "door"})
    result = check(hass)
    assert result["secure"] is True
    assert result["assessment"]["verdict"] == "clear"

    hass.states.async_set("binary_sensor.gate", "unavailable", {"device_class": "door"})
    result = check(hass)
    assert result["secure"] is None
    assert result["assessment"]["confirmed"] is False
    assert "unsettled_readings" in result["assessment"]["reasons"]

    hass.states.async_set("binary_sensor.gate", "on", {"device_class": "door"})
    hass.states.async_set("lock.front", "unavailable")
    result = check(hass)
    assert result["secure"] is False
    assert result["assessment"]["confirmed"] is False
    assert "opening_reported" in result["assessment"]["reasons"]
