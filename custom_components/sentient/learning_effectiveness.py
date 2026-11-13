"""Observed before/after comparisons without claims of causation."""
from __future__ import annotations
from typing import Any

def compare(before: dict[str, Any], after: dict[str, Any], *, metric: str,
            min_samples: int = 20) -> dict[str, Any]:
    try:
        b, a = before[metric], after[metric]
        bn, an = int(before["samples"]), int(after["samples"])
        b, a = float(b), float(a)
        if not (bn >= min_samples and an >= min_samples and 0 <= b <= 1 and 0 <= a <= 1):
            raise ValueError()
    except (TypeError, ValueError, KeyError, OverflowError):
        return {"measured": False, "difference": None, "causal_improvement": None}
    return {"measured": True, "before": b, "after": a, "difference": round(a-b, 3),
            "samples_before": bn, "samples_after": an, "causal_improvement": None,
            "note": "Observed association only; changing household conditions may explain differences."}
