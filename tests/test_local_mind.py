"""No-network explanations must preserve uncertainty."""
from custom_components.sentient.local_mind import explain_security_offline


def test_offline_unknown_does_not_claim_secure():
    result = explain_security_offline({"assessment": {
        "verdict": "unknown", "confirmed": False, "reasons": ["unsettled_readings"]}})
    assert "cannot confirm" in result


def test_offline_clear_qualifies_sensor_reports():
    result = explain_security_offline({"assessment": {
        "verdict": "clear", "confirmed": True, "reasons": []}})
    assert "not a physical verification" in result


def test_offline_problem_keeps_original_details():
    result = explain_security_offline({"summary": "The gate is open.", "assessment": {
        "verdict": "problem", "confirmed": False, "reasons": ["opening_reported", "unsettled_readings"]}})
    assert "gate is open" in result
    assert "could not be confirmed" in result


def test_missing_assessment_fails_closed():
    assert "cannot confirm" in explain_security_offline({})
