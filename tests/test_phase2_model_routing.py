"""Automatic provider choice only when explicit and backed by measured samples."""
from custom_components.sentient.model_routing import choose, stats


def _options(**extra):
    return {"ai_routing_enabled": True,
            "ai_routing_alternative": "ai_task.fast",
            "ai_routing_primary_cost": 0.04,
            "ai_routing_alternative_cost": 0.01,
            "ai_routing_max_p95_ms": 4000, **extra}


def _observations():
    return {"ai_task.main": [(True, 1300)] * 24,
            "ai_task.fast": [(True, 450)] * 24}


def test_routing_requires_opt_in_and_measured_samples():
    job = {"task_name": "Sentient: evening summary", "instructions": "Summarise"}
    assert not choose({}, job, _observations(), default_entity="ai_task.main")["switched"]
    assert choose(_options(), job, {}, default_entity="ai_task.main")["reason"] == "insufficient_measured_service_calls"
    routed = choose(_options(), job, _observations(), default_entity="ai_task.main")
    assert routed["switched"]
    assert routed["entity_id"] == "ai_task.fast"
    assert routed["billed_cost_known"] is False


def test_provider_is_never_changed_for_image_structured_or_explicit_requests():
    for key, value in (("entity_id", "ai_task.main"), ("attachments", [{"media_content_type": "image/jpeg"}]),
                       ("structure", {"a": "b"})):
        job = {"task_name": "Sentient: urgent incident", key: value}
        choice = choose(_options(), job, _observations(), default_entity="ai_task.main")
        assert choice["entity_id"] == "ai_task.main"
        assert not choice["switched"]
    assert not choose(_options(), {"task_name": "Non-Sentient"}, _observations(),
                      default_entity="ai_task.main")["switched"]


def test_routing_rejects_unreliable_or_invalid_cost_samples():
    bad = _observations()
    bad["ai_task.fast"] = [(False, 450)] * 3 + [(True, 450)] * 21
    assert not choose(_options(), {"task_name": "Sentient: summary"}, bad,
                      default_entity="ai_task.main")["switched"]
    assert not choose(_options(ai_routing_primary_cost=None),
                      {"task_name": "Sentient: summary"}, _observations(),
                      default_entity="ai_task.main")["switched"]
    assert not choose(_options(ai_routing_max_p95_ms="invalid"),
                      {"task_name": "Sentient: summary"}, _observations(),
                      default_entity="ai_task.main")["switched"]
    assert stats([(True, 10)] * 4)["success_rate"] is None
