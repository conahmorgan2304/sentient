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
    # Unrecognised or absent statuses are unknown; never silently treat an
    # incomplete sensor report as settled evidence.
    unknown = sum(r.get("status") not in ("observed", "inferred") for r in evidence)
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
    # Corroboration needs two identifiable, distinct sources, not merely two
    # evidence kinds. A duplicated/mislabelled source cannot corroborate itself.
    contact_sources = {
        row["source"] for row in evidence
        if row.get("kind") == "contact" and row.get("status") == "observed"
        and isinstance(row.get("source"), str) and row["source"].strip()
    }
    camera_sources = {
        row["source"] for row in evidence
        if row.get("kind") in CAMERA_KINDS and row.get("status") == "observed"
        and isinstance(row.get("source"), str) and row["source"].strip()
    }
    corroborated = any(contact != camera for contact in contact_sources for camera in camera_sources)
    if corroborated:
        reasons.append("contact_and_camera_activity")
    elif kinds["contact"] and camera_reports:
        reasons.append("contact_and_camera_reports_unconfirmed")
    status = ("mixed" if sum(bool(v) for v in (observed, inferred, unknown)) > 1 else
              "observed" if observed else "inferred" if inferred else "unknown")
    gaps = []
    if unknown:
        gaps.append("unsettled_evidence")
    if not evidence:
        gaps.append("no_evidence")
    if not corroborated:
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
        "corroboration": "reported_activity" if corroborated else "unconfirmed",
        "limitation": "Events near in time may be unrelated. No person identity, route, or intrusion is proven.",
    }
