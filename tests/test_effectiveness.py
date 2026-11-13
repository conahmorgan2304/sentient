"""Learning effectiveness can report samples, not claim unmeasured gains."""
from custom_components.sentient.effectiveness import report


def test_effectiveness_requires_samples_and_never_invents_improvement():
    empty = report()
    assert empty["improvement_over_baseline"] is None
    assert empty["labelled_incident_accuracy"] is None
    assert empty["incident_stage_agreement"] is None
    assert empty["potential_conflict_rate"] is None
    assert empty["observed_voice_completion_rate"] is None


def test_effectiveness_uses_bounded_measured_counts():
    result = report(
        cognitive={"tracked_incidents": 10, "latest_stage_agreement_rate": 0.8},
        intents={"intents_seen": 20, "potential_conflicts": 3},
        voice_timings=[{"outcome": "completed"}] * 8 +
                      [{"outcome": "failed"}] * 2,
    )
    assert result["incident_stage_agreement"] == 0.8
    assert result["potential_conflict_rate"] == 0.15
    assert result["observed_voice_completion_rate"] == 0.8
    assert result["voice_timing_sample_size"] == 10
    assert result["improvement_over_baseline"] is None
    assert result["labelled_incident_accuracy"] is None
