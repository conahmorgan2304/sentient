"""No-network retrieval quality checks on already permission-filtered results.

Ranks existing allowed rows only; does not broaden search or request embeddings.
"""
from __future__ import annotations
import re
from typing import Any

_WORD = re.compile(r"[a-z0-9]{2,}", re.I)
def tokens(text: str) -> set[str]:
    return set(_WORD.findall(text.lower())) - {"the", "and", "for", "was", "are", "with"}

def rerank(query: str, rows: list[dict[str, Any]], *, limit: int = 8) -> list[dict[str, Any]]:
    needed = tokens(query)
    def key(row: dict[str, Any]) -> tuple[float, int]:
        words = tokens(str(row.get("text") or row.get("body") or ""))
        overlap = len(words & needed)
        return ((overlap / len(needed)) if needed else 0, overlap)
    return sorted((r for r in rows[:100] if isinstance(r, dict)), key=key, reverse=True)[:max(0, min(limit, 30))]

def benchmark(cases: list[dict[str, Any]], *, minimum: int = 5) -> dict[str, Any]:
    """Evaluate recall only where a provided document ID is known correct."""
    outcomes = []
    for case in cases[:100]:
        if not isinstance(case, dict) or not isinstance(case.get("candidates"), list):
            continue
        expected = case.get("expected_id")
        if not isinstance(expected, str) or not expected:
            continue
        ranked = rerank(str(case.get("query") or ""), case["candidates"], limit=5)
        outcomes.append(any(r.get("id") == expected for r in ranked))
    return {"labelled_queries": len(outcomes),
            "recall_at_5": round(sum(outcomes) / len(outcomes), 3) if len(outcomes) >= minimum else None,
            "note": "Lexical baseline only; semantic embeddings are not enabled."}
