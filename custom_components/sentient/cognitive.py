"""Shared, deterministic assessment of household evidence.

This is the first small cognitive-kernel boundary: features supply facts, the
assessor returns a bounded decision with reasons, and callers still control
delivery and actions. No LLM, service calls, global state or hidden actuation.
"""
from __future__ import annotations

from dataclasses import dataclass
from typing import Literal

Verdict = Literal["problem", "unknown", "clear"]


@dataclass(frozen=True)
class Assessment:
    """A decision supported by facts, never an instruction to perform an action."""

    verdict: Verdict
    reasons: tuple[str, ...]
    confirmed: bool

    @property
    def secure(self) -> bool | None:
        """Only a complete, settled reading may establish a secure perimeter."""
        if self.verdict == "problem":
            return False
        if self.verdict == "clear":
            return True
        return None

    def as_dict(self) -> dict:
        return {"verdict": self.verdict, "reasons": list(self.reasons),
                "confirmed": self.confirmed}


def assess_security(*, open_count: int, unlocked_count: int,
                    alarm_triggered: bool, unknown_count: int,
                    perimeter_present: bool, access_limited: bool) -> Assessment:
    """Prioritise known danger without disguising gaps in observation.

    A known opening or unlocked lock is a problem even if other sensors are
    missing; otherwise unknown/missing/hidden inputs prevent a 'clear' result.
    This only reads already-authorised, current evidence passed by the caller.
    """
    reasons: list[str] = []
    if open_count:
        reasons.append("opening_reported")
    if unlocked_count:
        reasons.append("lock_not_secure")
    if alarm_triggered:
        reasons.append("alarm_alert")
    if unknown_count:
        reasons.append("unsettled_readings")
    if not perimeter_present:
        reasons.append("no_perimeter_readings")
    if access_limited:
        reasons.append("restricted_visibility")

    problem = bool(open_count or unlocked_count or alarm_triggered)
    gaps = bool(unknown_count or not perimeter_present or access_limited)
    if problem:
        return Assessment("problem", tuple(reasons), confirmed=not gaps)
    if gaps:
        return Assessment("unknown", tuple(reasons), confirmed=False)
    return Assessment("clear", (), confirmed=True)
