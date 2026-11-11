"""Guarded planning must not grant permission or trigger execution."""
from custom_components.sentient.plan_review import review_plan


def test_unvalidated_plan_fails_closed():
    assert review_plan([{"entity_id": "light.kitchen", "action": "turn_on"}]).status == "blocked"


def test_multi_device_plan_is_descriptive():
    result = review_plan([
        {"entity_id": "light.kitchen", "action": "turn_on"},
        {"entity_id": "light.hall", "action": "turn_off"}], validated=True)
    assert result.status == "reviewed"
    assert not result.as_dict()["execute"]
    assert result.as_dict()["steps"][1]["entity_id"] == "light.hall"


def test_sensitive_steps_need_confirmation():
    result = review_plan([{"entity_id": "lock.front", "action": "unlock"}], validated=True)
    assert result.requires_confirmation


def test_duplicate_target_fails_closed():
    result = review_plan([
        {"entity_id": "light.one", "action": "turn_on"},
        {"entity_id": "light.one", "action": "turn_off"}], validated=True)
    assert result.status == "blocked"
    assert "conflicting_target" in result.reasons


def test_too_many_steps_fails_closed():
    result = review_plan([
        {"entity_id": f"light.{i}", "action": "turn_on"} for i in range(21)], validated=True)
    assert result.status == "blocked"
