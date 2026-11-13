"""Phase 2 tools are pure, conservative and never operate real devices."""
from datetime import datetime, timedelta, timezone

from custom_components.sentient.action_policy import simulate
from custom_components.sentient.outcome_learning import evaluate, reviewed_feedback
from custom_components.sentient.retrieval_quality import rerank, benchmark
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


def test_existing_frigrate_feedback_is_not_falsely_reported_as_accuracy():
    rows = [{"feedback": {"value": "false_positive"}}] * 4 + [{"feedback": {"value": "useful"}}] * 8
    result = reviewed_feedback(rows)
    assert result["reviewed_samples"] == 12
    assert result["reviewed_correction_fraction"] == .333
    assert result["confirmed_population_error_rate"] is None
    assert result["changes_to_frigate_settings"] == 0


def test_malformed_evaluation_inputs_are_ignored_not_executed():
    assert evaluate([{"human_label": [], "predicted_stage": "observed"},
                     {"human_label": "household", "predicted_stage": []}])["sample_size"] == 0
    assert rerank("boiler", [None, {"id": "a", "text": "boiler"}])[0]["id"] == "a"
