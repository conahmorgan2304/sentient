"""Aggregate voice-match uncertainty without asserting identity."""
from __future__ import annotations
from collections import Counter
from typing import Any

def report(turns: list[dict[str, Any]], *, min_samples: int = 20) -> dict[str, Any]:
    counts = Counter()
    for t in turns[:500]:
        if not isinstance(t, dict):
            continue
        reason = t.get("reason")
        if isinstance(reason, str) and reason in {"matched", "below_threshold", "ambiguous", "no_voice", "error"}:
            counts[reason] += 1
    total = sum(counts.values())
    return {"sample_size": total, "matched_fraction": round(counts["matched"] / total, 3)
            if total >= min_samples else None,
            "ambiguous_or_low_confidence": counts["ambiguous"] + counts["below_threshold"],
            "automatic_authorisation": False,
            "note": "A voice match is a confidence estimate, not proof of identity."}
