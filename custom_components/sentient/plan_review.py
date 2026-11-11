"""Local plan preflight for guarded multi-device requests.

Plans are only validated descriptions. This module neither executes nor
approves actions; the guarded Control authority continues to own permissions,
confirmations, state verification and service calls.
"""
from __future__ import annotations

from dataclasses import dataclass
from typing import Any

MAX_STEPS = 20
SENSITIVE = {"lock", "unlock", "open_cover", "close_cover"}
SENSITIVE_DOMAINS = {"lock", "cover"}


@dataclass(frozen=True)
class PlanReview:
    status: str
    reasons: tuple[str, ...]
    steps: tuple[dict[str, str], ...]
    requires_confirmation: bool

    def as_dict(self) -> dict[str, Any]:
        return {"status": self.status, "reasons": list(self.reasons),
                "steps": [dict(step) for step in self.steps],
                "requires_confirmation": self.requires_confirmation,
                "execute": False}


def review_plan(actions: Any, *, validated: bool = False) -> PlanReview:
    """Review *previously permission-validated* explicit entity actions.

    This is not an authorisation substitute. A caller must run the regular
    `Control.validate` immediately before requesting execution as well.
    """
    if not validated:
        return PlanReview("blocked", ("not_authorised",), (), False)
    if not isinstance(actions, list) or not 1 <= len(actions) <= MAX_STEPS:
        return PlanReview("blocked", ("invalid_step_count",), (), False)
    steps: list[dict[str, str]] = []
    targets: set[str] = set()
    needs_confirmation = False
    for action in actions:
        if not isinstance(action, dict):
            return PlanReview("blocked", ("invalid_step",), (), False)
        eid, operation = action.get("entity_id"), action.get("action")
        if not isinstance(eid, str) or "." not in eid or not isinstance(operation, str):
            return PlanReview("blocked", ("invalid_step",), (), False)
        if eid in targets:
            return PlanReview("blocked", ("conflicting_target",), (), False)
        targets.add(eid)
        domain = eid.split(".", 1)[0]
        if operation in SENSITIVE or domain in SENSITIVE_DOMAINS:
            needs_confirmation = True
        steps.append({"entity_id": eid, "action": operation})
    return PlanReview("reviewed", (), tuple(steps), needs_confirmation)
