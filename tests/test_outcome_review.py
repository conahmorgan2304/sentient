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
