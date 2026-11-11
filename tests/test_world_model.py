"""World-model boundary: bounded, visibility-preserving and honest."""
from custom_components.sentient.world_model import summarise_world


def test_world_model_does_not_scan_or_invent_devices():
    snapshot = {
        "as_of": "2026-11-11T17:00:00+00:00",
        "current_readings": [
            {"entity_id": "binary_sensor.gate", "status": "reported", "state": "off",
             "reported_at": "2026-11-11T16:59:30+00:00"},
            {"entity_id": "binary_sensor.shed", "status": "unknown", "state": "unavailable",
             "reported_at": "2026-11-11T16:59:30+00:00"},
            {"entity_id": "binary_sensor.gate", "status": "reported", "state": "off",
             "reported_at": "2026-11-11T16:59:30+00:00"},
        ],
        "recent_changes": [
            {"entity_id": "binary_sensor.gate", "at": "2026-11-11T16:59:00+00:00",
             "expires": "2026-11-11T17:29:00+00:00"},
            {"entity_id": "binary_sensor.hidden", "at": "2026-11-11T16:59:00+00:00",
             "expires": "2026-11-11T17:29:00+00:00"},
        ],
    }
    view = summarise_world(snapshot)
    assert [r["entity_id"] for r in view["observations"]] == [
        "binary_sensor.gate", "binary_sensor.shed"]
    assert view["uncertainty"]["unknown_count"] == 1
    assert [c["entity_id"] for c in view["recent_changes"]] == ["binary_sensor.gate"]
    assert view["coverage"] == "partial"


def test_old_report_is_not_declared_sensor_failure():
    result = summarise_world({
        "as_of": "2026-11-11T17:00:00+00:00",
        "current_readings": [{"entity_id": "binary_sensor.door", "status": "reported",
                              "state": "off", "reported_at": "2026-11-10T17:00:00+00:00"}],
    })
    assert result["observations"][0]["certainty"] == "old_report"
    assert result["uncertainty"]["old_report_count"] == 1


def test_invalid_dates_are_not_treated_as_recent():
    result = summarise_world({
        "as_of": "bad",
        "current_readings": [{"entity_id": "sensor.one", "status": "reported",
                              "state": "20", "reported_at": "bad"}],
        "recent_changes": [{"entity_id": "sensor.one", "at": "bad", "expires": "bad"}],
    })
    assert result["observations"][0]["certainty"] == "undated"
    assert not result["recent_changes"]


def test_snapshot_is_bounded():
    result = summarise_world({"as_of": "2026-11-11T17:00:00+00:00",
        "current_readings": [
            {"entity_id": f"sensor.sensor_{i}", "state": "ok", "status": "reported",
             "reported_at": "2026-11-11T16:59:00+00:00"} for i in range(100)]})
    assert len(result["observations"]) == 8


def test_future_dated_sensor_is_not_treated_as_current():
    result = summarise_world({
        "as_of": "2026-11-11T17:00:00+00:00",
        "current_readings": [{"entity_id": "binary_sensor.gate", "status": "reported",
                              "state": "off", "reported_at": "2026-11-11T19:00:00+00:00"}],
    })
    assert result["observations"][0]["certainty"] == "undated"
    assert result["observations"][0]["report_age_seconds"] is None
