"""Read-only assessment of correlated camera/security incident evidence.

Time correlation alone cannot prove identity, route, or intrusion. This module
offers explanation data only; existing alert, suppression and escalation rules
remain authoritative.
"""
from __future__ import annotations

from collections import Counter
from typing import Any

from .cognitive import decision

# Frigate events (kind "event") are camera detections just as much as a person sensor is.
CAMERA_KINDS = frozenset({"detection", "event"})


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
    camera_reports = sum(kinds[k] for k in CAMERA_KINDS)
    if camera_reports:
        reasons.append("camera_detection_report")
    if kinds["visit"]:
        reasons.append("doorbell_or_doorstep_visit")
    if kinds["clip"]:
        reasons.append("clip_available")
    if kinds["alert"]:
        reasons.append("alert_rule_triggered")
    if source_count > 1:
        reasons.append("multiple_sources_close_in_time")
    if unknown:
        reasons.append("unsettled_evidence")
    # Independent sensor kinds can corroborate that activity occurred, not
    # who was there or whether any particular route was taken.
    contact_seen = any(row.get("kind") == "contact" and row.get("status") == "observed"
                       for row in evidence)
    camera_seen = any(row.get("kind") in CAMERA_KINDS and row.get("status") == "observed"
                      for row in evidence)
    if contact_seen and camera_seen:
        reasons.append("contact_and_camera_activity")
    elif kinds["contact"] and camera_reports:
        reasons.append("contact_and_camera_reports_unconfirmed")
    status = ("observed" if observed and not (inferred or unknown) else
              "mixed" if observed else "inferred" if inferred else "unknown")
    gaps = []
    if unknown:
        gaps.append("unsettled_evidence")
    if not evidence:
        gaps.append("no_evidence")
    if not (contact_seen and camera_seen):
        gaps.append("unconfirmed_cross_sensor_link")
    return {
        "status": status,
        # Uses the same non-actuating decision envelope as other household
        # assessments. It records evidence only AFTER callers filter access.
        "decision": decision(
            "security_incident_assessment",
            evidence=[f"{r.get('kind', 'unknown')}:{r.get('status', 'unknown')}" for r in evidence[-8:]],
            gaps=gaps,
            choice=status,
            reasons=reasons,
        ),
        "reasons": reasons,
        "source_count": source_count,
        "reported_observations": observed,
        "inferences": inferred,
        "unknown_reports": unknown,
        "identity_confirmed": False,
        "route_confirmed": False,
        "corroboration": "reported_activity" if contact_seen and camera_seen else "unconfirmed",
        "limitation": "Events near in time may be unrelated. No person identity, route, or intrusion is proven.",
    }
