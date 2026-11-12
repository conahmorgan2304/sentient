"""Cognitive Coordinator 2.0 shadow-mode contracts.

These tests protect the permission boundary: no HA service calls, speaker
tracking, automatic retries or changes to authoritative alert decisions.
"""
from datetime import datetime, timedelta, timezone

import pytest

from custom_components.sentient.cognitive_coordinator import (
    MAX_TRACKED,
    CognitiveCoordinator,
    assess_observations,
    investigate,
    review_outcome,
)
from custom_components.sentient.incident_reasoning import assess_incident


NOW = datetime(2026, 11, 12, 20, 0, tzinfo=timezone.utc)


def _episode(incident_id="event-1", end=None, level=0):
    return {"id": incident_id, "end": (end or NOW).isoformat(), "alert_level": level,
            "evidence": [
                {"kind": "contact", "status": "observed", "source": "binary_sensor.gate"},
                {"kind": "detection", "status": "observed", "source": "camera.garden"},
            ]}


def test_world_quality_never_reports_physical_safety():
    data = assess_observations([
        {"entity_id": "binary_sensor.gate", "state": "off", "status": "reported",
         "reported_at": (NOW - timedelta(seconds=8)).isoformat()},
        {"entity_id": "lock.front", "state": "unknown", "status": "reported",
         "reported_at": NOW.isoformat()},
    ], now=NOW)
    assert data["reported"] == 1 and data["unknown"] == 1
    assert data["coverage"] == "partial"
    assert "unknown" in data["decision"]["gaps"]
    assert "secure" not in data and "authorise" not in data


def test_old_and_future_readings_are_explicitly_uncertain():
    data = assess_observations([
        {"entity_id": "sensor.old", "state": "20", "status": "reported",
         "reported_at": (NOW - timedelta(hours=2)).isoformat()},
        {"entity_id": "sensor.future", "state": "20", "status": "observed",
         "reported_at": (NOW + timedelta(hours=2)).isoformat()},
        {"entity_id": "sensor.undated", "state": "20", "status": "reported"},
    ], now=NOW)
    assert (data["old_report"], data["future"], data["undated"]) == (1, 1, 1)
    assert data["decision"]["choice"] == "uncertain"


def test_duplicate_reports_do_not_inflate_world_observations():
    data = assess_observations([{
        "entity_id": "sensor.one", "state": "5", "status": "reported",
        "reported_at": NOW.isoformat(),
    }] * 30, now=NOW)
    assert data["total"] == 1 and data["reported"] == 1


def test_shadow_investigation_never_confirms_route_or_identity():
    item = _episode()
    assessment = assess_incident(item)
    result = investigate(item, assessment, baseline_stage="investigating", now=NOW)
    assert result["stage"] == "investigating"
    assert result["agrees"] is True
    assert result["authoritative"] is False
    assert result["route_confirmed"] is False
    assert result["identity_confirmed"] is False
    assert result["decision"]["ai_used"] is False


def test_shadow_preserves_high_priority_alert_even_if_evidence_unsettled():
    item = _episode(level=2)
    item["evidence"] = [{"kind": "detection", "status": "unknown", "source": "camera.gate"}]
    result = investigate(item, assess_incident(item), baseline_stage="concerning", now=NOW)
    assert result["stage"] == "concerning"
    assert "some_evidence_unsettled" in result["uncertainty"]


def test_coordinator_counts_only_new_or_changed_incident_assessments():
    item = _episode()
    assessment = assess_incident(item)
    core = CognitiveCoordinator()
    core.incident(item, assessment, "observed", now=NOW)
    core.incident(item, assessment, "observed", now=NOW)
    assert core.summary()["incidents_compared"] == 1
    item["evidence"].append({"kind": "alert", "status": "inferred", "source": "camera.garden"})
    core.incident(item, assess_incident(item), "investigating", now=NOW)
    assert core.summary()["incidents_compared"] == 2
    assert core.summary()["autonomous_actions"] == 0


def test_shadow_metrics_remain_bounded_and_store_no_identifying_evidence():
    core = CognitiveCoordinator()
    for i in range(MAX_TRACKED + 20):
        item = _episode(f"event-{i}")
        core.incident(item, assess_incident(item), "observed", now=NOW)
    assert len(core._seen) <= MAX_TRACKED
    assert len(core._recent) <= MAX_TRACKED
    assert core.summary()["incidents_compared"] == MAX_TRACKED + 20


def test_outcome_learning_is_read_only_and_requires_verified_results():
    core = CognitiveCoordinator()
    good = core.outcome({"assessment": {"state": "reported_complete"}})
    pending = core.outcome({"assessment": {"state": "unverified"}})
    assert good["lesson"] == "reported_success"
    assert pending["lesson"] == "needs_verification"
    assert good["physical_completion_confirmed"] is False
    assert not pending["retry_authorised"]
    assert core.summary()["reported_successes"] == 1
    assert core.summary()["outcomes_needing_verification"] == 1


def test_naive_now_is_rejected_instead_of_guessing_timezone():
    with pytest.raises(ValueError):
        assess_observations([], now=datetime(2026, 11, 12, 20, 0))


def test_new_conversation_subject_discards_prior_investigation():
    from custom_components.sentient.sessions import Sessions
    from tests.test_voice_sessions_control import request

    sessions = Sessions({"conversation_sessions": True, "conversation_cues": True})
    session = sessions.get(request("Why did the gate alert?"))
    sessions.begin(session, "Why did the gate alert?")
    session.active = True
    session.investigation = {
        "subject": "gate", "findings": ["old camera evidence"],
        "next_question": "Was someone in the garden?",
    }
    sessions.begin(session, "On another note, what is the weather?")
    assert session.investigation is None


def test_short_followup_keeps_same_session_investigation_without_reopening_microphone():
    from custom_components.sentient.sessions import Sessions
    from tests.test_voice_sessions_control import request

    sessions = Sessions({"conversation_sessions": True, "conversation_cues": True})
    session = sessions.get(request("Why did the gate alert?"))
    sessions.begin(session, "Why did the gate alert?")
    session.investigation = {"subject": "gate", "findings": [], "next_question": "What happened next?"}
    sessions.begin(session, "What happened next?")
    assert session.investigation["subject"] == "gate"
    assert not session.waiting  # a new turn alone must never re-open an Assist mic


def test_multi_step_progress_never_resends_failed_or_unverified_actions():
    from custom_components.sentient.cognitive_coordinator import task_progress

    report = task_progress({"actions": [
        {"entity_id": "light.kitchen", "status": "verified"},
        {"entity_id": "switch.hall", "status": "failed"},
        {"entity_id": "lock.front", "status": "not_started"},
    ]})
    assert report["steps"] == 3
    assert report["reported_complete"] == 1
    assert report["failed"] == 1
    assert report["not_started"] == 1
    assert report["state"] == "partial_or_failed"
    assert report["can_continue_automatically"] is False
    assert report["requires_fresh_state_check"] is True
    assert "no_automatic_retry" in report["decision"]["reasons"]


def test_incident_collection_preserves_legacy_stage_and_counts_one_shadow_revision(hass):
    from homeassistant.util import dt as dt_util
    from custom_components.sentient.incidents import Incidents

    incidents = Incidents(hass, "cognitive-shadow", {"security_incidents": True})
    now = dt_util.utcnow()
    incidents.items = [{
        "id": "gate-event", "start": now.isoformat(), "end": now.isoformat(),
        "sources": ["binary_sensor.gate", "camera.garden"], "alert_level": 1,
        "evidence": [
            {"source": "binary_sensor.gate", "kind": "contact", "status": "observed",
             "text": "Gate open", "time": now.isoformat()},
            {"source": "camera.garden", "kind": "detection", "status": "observed",
             "text": "Person reported", "time": now.isoformat()},
        ],
    }]
    first = incidents.recent()[0]
    assert first["stage"] == "investigating"  # existing algorithm is authoritative
    assert first["cognitive_shadow"]["authoritative"] is False
    assert incidents.cognitive.summary()["incidents_compared"] == 1
    incidents.recent()
    assert incidents.cognitive.summary()["incidents_compared"] == 1


def test_delayed_verification_revises_one_batch_instead_of_double_counting():
    core = CognitiveCoordinator()
    result = {"recorded_at": "2026-11-12T20:00:01+00:00",
              "assessment": {"state": "unverified"},
              "actions": [{"entity_id": "switch.kettle", "status": "attempted"}]}
    core.outcome(result)
    core.outcome(result)
    assert core.summary()["outcomes_needing_verification"] == 1
    result["assessment"] = {"state": "reported_complete"}
    result["actions"][0]["status"] = "verified"
    core.outcome(result)
    assert core.summary()["outcomes_needing_verification"] == 0
    assert core.summary()["reported_successes"] == 1
    assert core.summary()["reported_completion_rate"] is None  # 10 samples needed


def test_shadow_comparison_sees_corrected_status_without_event_count_change():
    item = _episode()
    core = CognitiveCoordinator()
    core.incident(item, assess_incident(item), "investigating", now=NOW)
    assert core.summary()["incidents_compared"] == 1
    item["evidence"][1]["status"] = "unknown"
    core.incident(item, assess_incident(item), "investigating", now=NOW)
    assert core.summary()["incidents_compared"] == 2


def test_shadow_exposes_bounded_latency_measurement_without_service_calls():
    core = CognitiveCoordinator()
    episode = _episode()
    core.incident(episode, assess_incident(episode), "investigating", now=NOW)
    status = core.summary()
    assert status["evaluations"] == 1
    assert status["mean_evaluation_ms"] >= 0
    assert status["slowest_evaluation_ms"] >= 0
    assert status["autonomous_actions"] == 0


def test_shadow_incident_metrics_do_not_retain_original_incident_ids():
    core = CognitiveCoordinator()
    episode = _episode("private-person-location-record")
    core.incident(episode, assess_incident(episode), "observed", now=NOW)
    assert "private-person-location-record" not in core._seen
    assert "private-person-location-record" not in core._recent
    assert list(core._seen) == list(core._recent)
    assert all(len(key) == 32 for key in core._seen)


def test_shadow_comparison_notices_evidence_kind_change_with_same_counts():
    core = CognitiveCoordinator()
    item = _episode("revision-1")
    core.incident(item, assess_incident(item), "observed", now=NOW)
    item["evidence"][1]["kind"] = "event"
    core.incident(item, assess_incident(item), "observed", now=NOW)
    assert core.summary()["incidents_compared"] == 2
    core.incident(item, assess_incident(item), "observed", now=NOW)
    assert core.summary()["incidents_compared"] == 2


def test_shadow_agreement_uses_latest_incident_stage_not_noisy_revision_count():
    core = CognitiveCoordinator()
    for index in range(10):
        episode = _episode(f"anonymous-{index}")
        core.incident(episode, assess_incident(episode), "observed", now=NOW)
    metrics = core.summary()
    assert metrics["tracked_incidents"] == 10
    assert metrics["latest_stage_agreement_rate"] is not None
    first = _episode("anonymous-0")
    for _ in range(20):
        first["evidence"].append({"source": "camera.gate", "kind": "event", "status": "inferred"})
        core.incident(first, assess_incident(first), "observed", now=NOW)
    assert core.summary()["tracked_incidents"] == 10
    assert core.summary()["latest_stage_agreement_rate"] == metrics["latest_stage_agreement_rate"]
