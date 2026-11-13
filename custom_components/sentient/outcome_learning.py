"""Privacy-minimal, independently labelled security evaluations.

Human labels are independent evidence; shadow/legacy agreement is not accuracy.
No label changes cameras, alarms, permissions or notification rules.
"""
from __future__ import annotations
from collections import Counter
from typing import Any

LABELS = frozenset({"false_detection", "household", "visitor", "concerning", "unresolved"})
PREDICTIONS = frozenset({"observed", "investigating", "likely household", "concerning", "resolved"})
CORRECT = {"false_detection": {"resolved"}, "household": {"likely household", "resolved"},
           "visitor": {"observed", "investigating"}, "concerning": {"concerning"}}

def evaluate(rows: list[dict[str, Any]], *, minimum: int = 10, limit: int = 500) -> dict[str, Any]:
    """Aggregate independent labels; withhold accuracy if sample too small."""
    valid = []
    counts = Counter()
    for item in rows[:limit]:
        if not isinstance(item, dict):
            continue
        label, predicted = item.get("human_label"), item.get("predicted_stage")
        if label not in LABELS or predicted not in PREDICTIONS:
            continue
        counts[label] += 1
        if label == "unresolved":
            continue
        valid.append(int(predicted in CORRECT.get(label, set())))
    n = len(valid)
    return {"mode": "independent_labels_only", "sample_size": n,
            "unresolved_count": counts["unresolved"],
            "label_counts": {key: counts[key] for key in sorted(LABELS)},
            "stage_match_rate": round(sum(valid) / n, 3) if n >= minimum else None,
            "accuracy_established": n >= minimum,
            "policy_changes": 0,
            "limitations": "Stage matching is not person identification or proof of incident truth."}

def reviewed_feedback(rows: list[dict[str, Any]], *, minimum: int = 10) -> dict[str, Any]:
    """Summarise existing Frigate household corrections without claiming accuracy.

    Useful/not useful are opinions, and feedback is a non-random subset.
    """
    counts = Counter()
    for item in rows[:500]:
        if not isinstance(item, dict):
            continue
        feedback = item.get("feedback")
        value = feedback.get("value") if isinstance(feedback, dict) else None
        if isinstance(value, str) and value in {"useful", "not_useful", "false_positive", "cat"}:
            counts[value] += 1
    reviewed = sum(counts.values())
    corrected = counts["false_positive"] + counts["cat"]
    return {"reviewed_samples": reviewed, "possible_misidentifications": corrected,
            "reviewed_correction_fraction": round(corrected/reviewed, 3) if reviewed >= minimum else None,
            "confirmed_population_error_rate": None,
            "changes_to_frigate_settings": 0,
            "note": "Only volunteered corrections were reviewed; not a population false-positive rate."}
