"""Dependency-free local explanation of security assessments.

When the chosen AI provider is down, this can still summarise the local
sensor-based security verdict. No model, networking or autonomous control.
"""
from __future__ import annotations

from typing import Any


def explain_security_offline(report: dict[str, Any]) -> str:
    """Return a bounded statement without claiming physical verification."""
    assessment = report.get("assessment") or {}
    verdict = assessment.get("verdict")
    reasons = set(assessment.get("reasons") or [])
    if verdict == "problem":
        detail = report.get("summary", "")
        if not isinstance(detail, str):
            detail = ""
        if not detail.strip():
            detail = "A monitored security sensor reported a concern."
        warning = " Other readings could not be confirmed." if not assessment.get("confirmed") else ""
        return (detail[:350].strip() + warning).strip()
    if verdict == "unknown":
        if "restricted_visibility" in reasons:
            return "I cannot confirm the house is secure: some security readings are outside this request's access."
        if "unsettled_readings" in reasons:
            return "I cannot confirm the house is secure because some sensors have no settled reading."
        return "I cannot confirm the house is secure with the available sensors."
    if verdict == "clear" and assessment.get("confirmed"):
        return "The monitored security devices currently report no problems. This is not a physical verification."
    return "I cannot confirm the house's current security state."
