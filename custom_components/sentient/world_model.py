"""Bounded world-model view over *already authorised* Home Assistant context.

No independent sensor scanning, identity guesses, storage, polling or actions.
A world view summarises a caller's existing permission-filtered snapshot, never
widens its visibility. Sensor reports are evidence, not physical verification.
"""
from __future__ import annotations

from collections import Counter
from datetime import datetime, timezone
from typing import Any

MAX_READINGS = 8
MAX_CHANGES = 6
MAX_AGE_SECONDS = 3600


def _time(value: Any) -> datetime | None:
    if not isinstance(value, str):
        return None
    try:
        dt = datetime.fromisoformat(value.replace("Z", "+00:00"))
    except ValueError:
        return None
    if dt.tzinfo is None:
        return None
    return dt.astimezone(timezone.utc)


def prompt_view(view: dict[str, Any]) -> dict[str, Any]:
    """The part worth spending prompt tokens on: counts, and only the readings that need care.

    Every reading already carries its own age and status in the snapshot, so repeating each one here would
    roughly double the context sent on every voice turn.
    """
    flagged = [{"entity_id": row["entity_id"], "certainty": row["certainty"]}
               for row in view.get("observations") or [] if row.get("certainty") != "reported"]
    return {"uncertainty": view.get("uncertainty") or {}, "needs_care": flagged,
            "recent_change_count": len(view.get("recent_changes") or []), "coverage": view.get("coverage", "partial"),
            "interpretation": view.get("interpretation", "")}


def summarise_world(snapshot: dict[str, Any]) -> dict[str, Any]:
    """Derive a small explainable status from a visibility-filtered snapshot.

    `snapshot` must be supplied by HouseContext.snapshot *after* its exposure,
    permission, private-room and guest-mode checks. No caller should supply
    unfiltered Home Assistant state.
    """
    seen: set[str] = set()
    observations: list[dict[str, Any]] = []
    counts: Counter[str] = Counter()
    now = _time(snapshot.get("as_of"))
    for row in (snapshot.get("current_readings") or [])[:MAX_READINGS]:
        if not isinstance(row, dict):
            continue
        entity = row.get("entity_id")
        if not isinstance(entity, str) or not entity or entity in seen:
            continue
        seen.add(entity)
        status = row.get("status")
        reported = _time(row.get("reported_at"))
        age = (now - reported).total_seconds() if now and reported else None
        if status not in ("reported", "observed") or row.get("state") in ("unknown", "unavailable"):
            certainty = "unknown"
        elif age is None or age < -60:
            # A clock that reports from the future is not a trustworthy live sample.
            certainty = "undated"
        elif age > MAX_AGE_SECONDS:
            # Old reports are not proof of staleness; we simply flag their age.
            certainty = "old_report"
        else:
            certainty = "reported"
        counts[certainty] += 1
        observations.append({"entity_id": entity, "certainty": certainty,
                             "reported_at": reported.isoformat() if reported else None,
                             "report_age_seconds": max(0, int(age)) if age is not None and age >= -60 else None})
    changes: list[dict[str, str]] = []
    for row in (snapshot.get("recent_changes") or [])[:MAX_CHANGES]:
        if not isinstance(row, dict) or row.get("entity_id") not in seen:
            continue
        occurred = _time(row.get("at"))
        expires = _time(row.get("expires"))
        if not occurred or not expires or not now or occurred > now or expires < now:
            continue
        changes.append({"entity_id": row["entity_id"], "at": occurred.isoformat(),
                        "kind": "reported_change"})
    return {
        "as_of": now.isoformat() if now else None,
        "observations": observations,
        "recent_changes": changes,
        "coverage": "partial",
        "uncertainty": {
            "unknown_count": counts["unknown"],
            "undated_count": counts["undated"],
            "old_report_count": counts["old_report"],
        },
        "interpretation": "Sensor reports are not proof of physical state or causality; no identity or occupancy inferred.",
    }
