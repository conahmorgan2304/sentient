"""Outcome evaluation never turns a service response into proof of real-world action."""
from custom_components.sentient.outcome_review import evaluate_outcomes


def test_reported_complete_is_not_physical_proof():
    row = evaluate_outcomes([{"entity_id": "light.one", "status": "verified"}])
    assert row["state"] == "reported_complete"
    assert not row["physical_completion_confirmed"]


def test_partly_completed_plan_is_not_replayed():
    row = evaluate_outcomes([
        {"entity_id": "light.one", "status": "verified"},
        {"entity_id": "light.two", "status": "failed"},
        {"entity_id": "light.three", "status": "not_started"},
    ])
    assert row["state"] == "partial"
    assert row["not_started"] == 1
    assert row["can_retry_automatically"] is False


def test_attempted_only_is_unverified():
    row = evaluate_outcomes([{"entity_id": "cover.garage", "status": "attempted"}])
    assert row["state"] == "unverified"
    assert row["next_step"] == "check_device_state_before_any_new_request"


def test_empty_plan_is_unknown():
    assert evaluate_outcomes([])["state"] == "unknown"


def test_guarded_outcome_uses_shared_decision_record_without_action_permission():
    row = evaluate_outcomes([
        {"entity_id": "light.hall", "status": "verified"},
        {"entity_id": "light.kitchen", "status": "attempted"},
    ])
    note = row["decision"]
    assert note["kind"] == "guarded_action_outcome"
    assert note["choice"] == "partial"
    assert "unverified_device_state" in note["gaps"]
    assert note["ai_used"] is False
    assert row["can_retry_automatically"] is False
