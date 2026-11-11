"""Explain the outcome of a guarded plan without making another service call.

Review / execute / verify are separate phases; this evaluator receives the
existing Control outcome rows and cannot grant a new action or retry a batch.
"""
from __future__ import annotations

from collections import Counter
from typing import Any

SUCCESS = frozenset({"verified"})
INCOMPLETE = frozenset({"attempted", "not_started", "skipped", "failed"})


def evaluate_outcomes(actions: list[dict[str, Any]]) -> dict[str, Any]:
    """Only reported state verification counts as complete.

    `attempted` means Home Assistant accepted a service call, not that a
    physical action happened; never retry uncertain requests automatically.
    """
    rows = [x for x in actions[:20] if isinstance(x, dict)]
    counts = Counter(str(row.get("status") or "unknown") for row in rows)
    if not rows:
        state = "unknown"
    elif counts["verified"] == len(rows):
        state = "reported_complete"
    elif counts["verified"]:
        state = "partial"
    elif counts["failed"]:
        # A refused or erroring step is a failure even if an earlier step was sent; never call it merely unverified.
        state = "failed"
    elif counts["attempted"]:
        state = "unverified"
    else:
        state = "not_completed"
    return {
        "state": state,
        "verified": counts["verified"],
        "attempted": counts["attempted"],
        "failed": counts["failed"],
        "not_started": counts["not_started"] + counts["skipped"],
        "can_retry_automatically": False,
        "physical_completion_confirmed": False,
        "next_step": "none" if state == "reported_complete" else
                     "check_device_state_before_any_new_request",
    }
