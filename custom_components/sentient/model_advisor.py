"""Evidence-only provider/model suitability report. No automatic switching."""
from __future__ import annotations
from typing import Any

def recommend(candidates: list[dict[str, Any]], *, max_cost: float | None = None,
              max_p95_ms: float | None = None, min_samples: int = 20) -> dict[str, Any]:
    qualified = []
    for row in candidates[:40]:
        if not isinstance(row, dict):
            continue
        try:
            n = int(row.get("samples") or 0)
            success = float(row.get("success_rate"))
            p95 = float(row.get("p95_ms"))
            cost = float(row.get("cost_per_call"))
            if (n < min_samples or not 0 <= success <= 1 or not 0 <= p95 < 1e9
                    or not 0 <= cost < 1e6 or not isinstance(row.get("model"), str)):
                continue
        except (TypeError, ValueError, OverflowError):
            continue
        if (max_cost is not None and cost > max_cost) or (max_p95_ms is not None and p95 > max_p95_ms):
            continue
        qualified.append(((-success, cost, p95), row["model"]))
    qualified.sort()
    return {"suggested_model": qualified[0][1] if qualified else None,
            "qualified_models": len(qualified), "automatic_switching": False,
            "note": "Only measured labelled samples qualify; provider selection is unchanged."}
