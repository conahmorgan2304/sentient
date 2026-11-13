"""Service metrics are bounded, local and never imply price or answer quality."""
from custom_components.sentient.ai import AIGate


def test_recent_ai_metrics_withhold_claims_until_sample_threshold(hass):
    gate = AIGate(hass, "phase2-test", {})
    assert gate.observed_performance()["completion_rate"] is None
    for i in range(10):
        gate._observed_calls.append((i < 8, 100 + i))
    report = gate.observed_performance()
    assert report["sample_size"] == 10
    assert report["completion_rate"] == .8
    assert report["p95_ms"] == 109
    assert report["billed_cost"] is None
    assert report["model_quality"] is None
    assert "instructions" not in str(report)
