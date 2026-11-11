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
