"""Read-only assessment of correlated camera/security incident evidence.

Time correlation alone cannot prove identity, route, or intrusion. This module
offers explanation data only; existing alert, suppression and escalation rules
remain authoritative.
"""
from __future__ import annotations

from collections import Counter
from typing import Any


def assess_incident(incident: dict[str, Any]) -> dict[str, Any]:
    evidence = [r for r in (incident.get("evidence") or [])[-50:] if isinstance(r, dict)]
    kinds = Counter(r.get("kind") for r in evidence)
    observed = sum(r.get("status") == "observed" for r in evidence)
    inferred = sum(r.get("status") == "inferred" for r in evidence)
    unknown = sum(r.get("status") == "unknown" for r in evidence)
    source_count = len({r.get("source") for r in evidence if isinstance(r.get("source"), str)})
    reasons: list[str] = []
    if kinds["contact"]:
        reasons.append("entry_contact_report")
    if kinds["detection"]:
        reasons.append("camera_detection_report")
    if kinds["clip"]:
        reasons.append("clip_available")
    if kinds["alert"]:
        reasons.append("alert_rule_triggered")
    if source_count > 1:
        reasons.append("multiple_sources_close_in_time")
    if unknown:
        reasons.append("unsettled_evidence")
    return {
        "status": "observed" if observed and not (inferred or unknown) else
                  "mixed" if observed else "inferred" if inferred else "unknown",
        "reasons": reasons,
        "source_count": source_count,
        "reported_observations": observed,
        "inferences": inferred,
        "unknown_reports": unknown,
        "identity_confirmed": False,
        "route_confirmed": False,
        "limitation": "Events near in time may be unrelated. No person identity, route, or intrusion is proven.",
    }
