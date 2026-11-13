"""Offline, read-only security decision replay.

The caller supplies synthetic or explicitly approved, redacted scenarios.
No Home Assistant services, files, cameras, AI calls or event subscriptions.
Agreement with existing rules is NOT accuracy; independent labels are needed.
"""
from __future__ import annotations

from collections import Counter
from datetime import datetime, timezone
from typing import Any

from .cognitive_coordinator import investigate
from .incident_reasoning import assess_incident
from .incidents import stage


def evaluate_scenarios(scenarios: list[dict[str, Any]], *,
                       now: datetime | None = None, limit: int = 100) -> dict[str, Any]:
    """Evaluate bounded scenario *copies*. Never retain incident text or IDs."""
    clock = now or datetime.now(timezone.utc)
    if clock.tzinfo is None:
        raise ValueError("Replay requires an aware datetime")
    results = Counter()
    compared = 0
    labelled = 0
    legacy_correct = 0
    shadow_correct = 0
    for scenario in scenarios[:max(0, min(limit, 500))]:
        if not isinstance(scenario, dict) or not isinstance(scenario.get("incident"), dict):
            results["invalid_scenario"] += 1
            continue
        incident = scenario["incident"]
        # Scenarios may be imported from hand-edited JSON. One malformed
        # example must not abort the entire offline validation batch.
        evidence = incident.get("evidence")
        if (not isinstance(evidence, list) or len(evidence) > 200
                or any(not isinstance(row, dict) for row in evidence)
                or not isinstance(incident.get("alert_level", 0), int)):
            results["invalid_scenario"] += 1
            continue
        try:
            assessment = assess_incident(incident)
            old, _ = stage(incident, assessment, now=clock)
            proposed = investigate(incident, assessment, baseline_stage=old, now=clock)
        except (TypeError, ValueError, KeyError, AttributeError, OverflowError):
            results["invalid_scenario"] += 1
            continue
        compared += 1
        results["stage_agreement" if proposed["stage"] == old else "stage_disagreement"] += 1
        if "expected_corroboration" in scenario:
            expected = scenario["expected_corroboration"]
            if expected not in ("reported_activity", "unconfirmed"):
                results["invalid_corroboration_label"] += 1
            else:
                results["corroboration_correct" if assessment["corroboration"] == expected
                        else "corroboration_incorrect"] += 1
        expected = scenario.get("expected_stage")
        if expected is not None:
            if expected not in ("unknown", "observed", "investigating", "likely household", "concerning", "resolved"):
                results["invalid_stage_label"] += 1
            else:
                labelled += 1
                legacy_correct += old == expected
                shadow_correct += proposed["stage"] == expected
    return {
        "mode": "offline_only",
        "scenarios_compared": compared,
        "scenarios_with_independent_labels": labelled,
        "stage_agreement_count": results["stage_agreement"],
        "stage_disagreement_count": results["stage_disagreement"],
        "legacy_label_accuracy": round(legacy_correct / labelled, 3) if labelled else None,
        "shadow_label_accuracy": round(shadow_correct / labelled, 3) if labelled else None,
        "corroboration_correct": results["corroboration_correct"],
        "corroboration_incorrect": results["corroboration_incorrect"],
        "invalid_scenario_count": results["invalid_scenario"],
        "invalid_label_count": results["invalid_stage_label"] + results["invalid_corroboration_label"],
        "automatic_actions": 0,
        "note": "Synthetic or independently labelled replay, not live security ground truth.",
    }
