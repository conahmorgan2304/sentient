"""Security incident assessment keeps correlation separate from proof."""
from custom_components.sentient.incident_reasoning import assess_incident


def test_cross_camera_correlation_never_proves_identity():
    incident = {"evidence": [
        {"source": "binary_sensor.gate", "kind": "contact", "status": "observed"},
        {"source": "camera.shed", "kind": "detection", "status": "inferred"},
    ]}
    result = assess_incident(incident)
    assert result["source_count"] == 2
    assert result["status"] == "mixed"
    assert not result["identity_confirmed"]
    assert not result["route_confirmed"]
    assert "multiple_sources_close_in_time" in result["reasons"]


def test_unknown_evidence_remains_unknown():
    result = assess_incident({"evidence": [
        {"source": "binary_sensor.gate", "kind": "contact", "status": "unknown"}]})
    assert result["status"] == "unknown"
    assert result["unknown_reports"] == 1


def test_empty_incident_does_not_imply_security():
    result = assess_incident({})
    assert result["status"] == "unknown"
    assert not result["reasons"]


def test_two_observed_signal_types_support_activity_not_a_person_route():
    result = assess_incident({"evidence": [
        {"source": "binary_sensor.gate", "kind": "contact", "status": "observed"},
        {"source": "camera.garden", "kind": "detection", "status": "observed"},
    ]})
    assert result["corroboration"] == "reported_activity"
    assert "contact_and_camera_activity" in result["reasons"]
    assert result["identity_confirmed"] is False
    assert result["route_confirmed"] is False


def test_inferred_detection_is_not_corroborated_as_observed():
    result = assess_incident({"evidence": [
        {"source": "binary_sensor.gate", "kind": "contact", "status": "observed"},
        {"source": "camera.garden", "kind": "detection", "status": "inferred"},
    ]})
    assert result["corroboration"] == "unconfirmed"
    assert "contact_and_camera_reports_unconfirmed" in result["reasons"]


def test_incident_assessment_uses_shared_non_actuating_decision_record():
    result = assess_incident({"evidence": [
        {"source": "binary_sensor.gate", "kind": "contact", "status": "observed"},
        {"source": "camera.garden", "kind": "detection", "status": "unknown"},
    ]})
    decision = result["decision"]
    assert decision["kind"] == "security_incident_assessment"
    assert decision["choice"] == "mixed"
    assert "unsettled_evidence" in decision["gaps"]
    assert decision["ai_used"] is False
    assert result["identity_confirmed"] is False
    assert result["route_confirmed"] is False


def test_same_sensor_cannot_corroborate_its_own_contact_and_camera_reports():
    result = assess_incident({"evidence": [
        {"source": "binary_sensor.gate", "kind": "contact", "status": "observed"},
        {"source": "binary_sensor.gate", "kind": "event", "status": "observed"},
    ]})
    assert result["corroboration"] == "unconfirmed"
    assert "contact_and_camera_activity" not in result["reasons"]
    assert "unconfirmed_cross_sensor_link" in result["decision"]["gaps"]


def test_missing_source_never_claims_independent_camera_corroboration():
    result = assess_incident({"evidence": [
        {"kind": "contact", "status": "observed"},
        {"source": "camera.gate", "kind": "detection", "status": "observed"},
    ]})
    assert result["corroboration"] == "unconfirmed"


def test_inferred_and_unknown_incident_reports_remain_mixed():
    result = assess_incident({"evidence": [
        {"source": "camera.garden", "kind": "detection", "status": "inferred"},
        {"source": "camera.shed", "kind": "detection", "status": "unknown"},
    ]})
    assert result["status"] == "mixed"
    assert result["unknown_reports"] == 1
    assert "unsettled_evidence" in result["decision"]["gaps"]


def test_missing_evidence_status_is_counted_as_unknown():
    result = assess_incident({"evidence": [
        {"source": "camera.garden", "kind": "detection"},
    ]})
    assert result["status"] == "unknown"
    assert result["unknown_reports"] == 1
