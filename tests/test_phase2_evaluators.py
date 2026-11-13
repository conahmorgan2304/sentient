"""Phase 2 tools are pure, conservative and never operate real devices."""
from datetime import datetime, timedelta, timezone

from custom_components.sentient.action_policy import simulate
from custom_components.sentient.outcome_learning import evaluate
from custom_components.sentient.retrieval_quality import rerank, benchmark
from custom_components.sentient.model_advisor import recommend
from custom_components.sentient.document_quality import assess, citation_confidence
from custom_components.sentient.learning_effectiveness import compare

NOW = datetime(2026, 11, 13, 17, 0, tzinfo=timezone.utc)


def test_manual_override_prevents_motion_light_reversal_in_simulation():
    old = {"entity_id": "light.girls_room", "action": "turn_off",
           "source": "manual", "when": NOW.isoformat()}
    new = {"entity_id": "light.girls_room", "action": "turn_on", "source": "motion_lights"}
    result = simulate(new, old, now=NOW + timedelta(seconds=30))
    assert result["decision"] == "recommend_hold"
    assert result["may_dispatch"] is False
    assert simulate(new, old, now=NOW + timedelta(hours=2))["decision"] == "no_conflict"


def test_security_domains_and_uncertain_evidence_can_never_authorise():
    for entity in ("lock.front", "alarm_control_panel.alarmo", "climate.heating"):
        assert simulate({"entity_id": entity, "source": "manual", "action": "turn_off"},
                        now=NOW)["decision"] == "unsupported"
    assert simulate({"entity_id": "light.girls_room", "source": "auto_off",
                     "action": "turn_off"},
                    {"entity_id": "light.girls_room", "source": "manual", "action": "turn_on"},
                    now=NOW)["decision"] == "unknown"


def test_independent_labels_need_samples_and_do_not_change_security():
    items = [{"human_label": "false_detection", "predicted_stage": "resolved"}] * 12
    result = evaluate(items)
    assert result["stage_match_rate"] == 1
    assert result["policy_changes"] == 0
    assert evaluate(items[:2])["stage_match_rate"] is None
    assert evaluate([{"human_label": "unresolved", "predicted_stage": "observed"}])["sample_size"] == 0


def test_retrieval_keeps_only_candidates_and_reports_labelled_quality():
    rows = [{"id": "a", "text": "Kettle instructions"}, {"id": "b", "text": "Boiler reset pressure instructions"}]
    assert rerank("boiler pressure", rows)[0]["id"] == "b"
    assert benchmark([{"query": "boiler pressure", "candidates": rows, "expected_id": "b"}],
                     minimum=1)["recall_at_5"] == 1
    assert benchmark([], minimum=1)["recall_at_5"] is None


def test_model_advisor_refuses_short_samples_and_does_not_switch():
    rows = [{"model": "fast", "samples": 24, "success_rate": .95, "p95_ms": 600,
             "cost_per_call": .01},
            {"model": "unmeasured", "samples": 3, "success_rate": 1, "p95_ms": 5,
             "cost_per_call": 0}]
    result = recommend(rows, max_cost=.02, max_p95_ms=700)
    assert result["suggested_model"] == "fast"
    assert result["automatic_switching"] is False


def test_scanned_pages_not_mistaken_for_good_citations():
    quality = assess(["", "Text " * 30])
    assert quality["likely_scanned_pages"] == 1
    assert quality["ocr_recommended"] is True
    assert citation_confidence("boiler pressure", "boiler manual")["verified_answer"] is False


def test_improvement_is_not_claimed_causal():
    old = {"samples": 50, "completion_rate": .6}
    new = {"samples": 60, "completion_rate": .8}
    result = compare(old, new, metric="completion_rate")
    assert result["difference"] == .2
    assert result["causal_improvement"] is None
    assert compare(old, {"samples": 2, "completion_rate": 1}, metric="completion_rate")["measured"] is False
