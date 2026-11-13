"""Measured usefulness: report only observed evidence, never invented improvements.

The learning report is descriptive. There is no learned optimisation unless
labelled before/after baselines exist and are independently verified.
"""
from __future__ import annotations

from typing import Any


def report(*, cognitive: dict[str, Any] | None = None,
           intents: dict[str, Any] | None = None,
           voice_timings: list[dict[str, Any]] | None = None) -> dict[str, Any]:
    cognitive = cognitive or {}
    intents = intents or {}
    successful = sum(1 for item in (voice_timings or [])[-20:]
                     if isinstance(item, dict) and item.get("outcome") == "completed")
    failed = sum(1 for item in (voice_timings or [])[-20:]
                 if isinstance(item, dict) and item.get("outcome") in ("failed", "cancelled"))
    samples = successful + failed
    checked = int(cognitive.get("tracked_incidents") or 0)
    intents_seen = int(intents.get("intents_seen") or 0)
    conflict_count = int(intents.get("potential_conflicts") or 0)
    return {
        "mode": "evidence_only",
        "labelled_incident_accuracy": None,
        "improvement_over_baseline": None,
        "incident_stage_agreement": cognitive.get("latest_stage_agreement_rate") if checked >= 10 else None,
        "incident_sample_size": checked,
        "potential_conflict_rate": round(conflict_count / intents_seen, 3) if intents_seen >= 10 else None,
        "action_intents_sample_size": intents_seen,
        "observed_voice_completion_rate": round(successful / samples, 3) if samples >= 5 else None,
        "voice_timing_sample_size": samples,
        "limits": [
            "Stage agreement is not independent accuracy.",
            "Conflicting intents are not confirmed device failures.",
            "Voice completion is not user satisfaction.",
            "No before/after improvement is claimed without labelled outcomes.",
        ],
    }
