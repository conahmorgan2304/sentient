"""Session cognitive working context is bounded, temporary and never an authorisation."""
from datetime import datetime, timedelta, timezone

from custom_components.sentient.working_memory import refresh, public_view


NOW = datetime(2026, 11, 13, 14, 0, tzinfo=timezone.utc)


def test_related_followup_keeps_goal_but_never_device_state():
    first = refresh(None, goal="Who was at the gate?", topic="Security",
                    entities=["camera.gate"], kind=None, changed=False, now=NOW)
    related = refresh(first, goal="What about the shed?", topic="Security",
                      entities=["camera.shed"], kind="follow_up", changed=False,
                      now=NOW + timedelta(seconds=20))
    assert related["goal"] == "Who was at the gate?"
    assert related["entities"] == ["camera.shed"]
    assert related["requires_fresh_permission_check"]
    assert related["can_authorise_actions"] is False


def test_subject_change_and_inactivity_drop_context():
    first = refresh(None, goal="The gate", topic="Security",
                    entities=["camera.gate"], kind=None, changed=False, now=NOW)
    changed = refresh(first, goal="Kitchen heating", topic="Heating",
                      entities=["climate.kitchen"], kind="follow_up", changed=True,
                      now=NOW + timedelta(seconds=30))
    assert changed["goal"] == "Kitchen heating"
    expired = refresh(first, goal="Garden watering", topic="Garden",
                      entities=[], kind="follow_up", changed=False,
                      now=NOW + timedelta(minutes=6))
    assert expired["goal"] == "Garden watering"


def test_working_context_only_keeps_bounded_safe_fields():
    record = refresh(None, goal="x" * 800, topic="t" * 500,
                     entities=["light." + "z" * 200] * 80,
                     kind="question", changed=False, now=NOW)
    assert len(record["goal"]) == 200
    assert len(record["entities"]) == 8
    assert all(len(v) <= 90 for v in record["entities"])
    assert "updated_at" not in public_view(record)
    assert not public_view(record)["can_authorise_actions"]
    assert public_view(None) is None


def test_working_context_never_exposes_unchecked_entity_candidates_to_model():
    record = refresh(None, goal="Why?", topic="Heating",
                     entities=["climate.private", "lock.hidden"], kind="explanation",
                     changed=False, now=NOW)
    visible = public_view(record)
    assert "entities" not in visible
    assert "climate.private" not in str(visible)
    assert "lock.hidden" not in str(visible)
