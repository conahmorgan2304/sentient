"""Simulation-only conflict policy for reversible household actions.

Never call Home Assistant services. An approved policy must be implemented
separately with fresh HA permission/state checks. Locks and alarms are excluded.
"""
from __future__ import annotations
from datetime import datetime, timedelta, timezone
from typing import Any

REVERSIBLE = frozenset({"light", "switch", "fan"})
ACTIONS = frozenset({"turn_on", "turn_off"})
MAX_AGE = timedelta(minutes=30)
SOURCE_PRIORITY = {"manual": 40, "guarded_voice": 40, "scheduled_routine": 20,
                   "rules": 15, "motion_lights": 10, "auto_off": 10}

def simulate(candidate: dict[str, Any], incumbent: dict[str, Any] | None = None,
             *, now: datetime | None = None) -> dict[str, Any]:
    """Advisory recommendation; no side effects, no authorisation."""
    clock = now or datetime.now(timezone.utc)
    if clock.tzinfo is None:
        raise ValueError("aware clock required")
    eid, operation, source = (candidate.get(k) for k in ("entity_id", "action", "source"))
    if (not isinstance(eid, str) or eid.count(".") != 1
            or eid.split(".")[0] not in REVERSIBLE or operation not in ACTIONS
            or source not in SOURCE_PRIORITY):
        return {"decision": "unsupported", "may_dispatch": False, "reason": "no_automatic_authority"}
    if not incumbent:
        return {"decision": "no_conflict", "may_dispatch": False, "reason": "observe_only"}
    if incumbent.get("entity_id") != eid or incumbent.get("action") not in ACTIONS:
        return {"decision": "no_conflict", "may_dispatch": False, "reason": "different_target"}
    stamp = incumbent.get("when")
    if isinstance(stamp, str):
        try:
            stamp = datetime.fromisoformat(stamp.replace("Z", "+00:00"))
        except ValueError:
            stamp = None
    if not isinstance(stamp, datetime) or stamp.tzinfo is None:
        return {"decision": "unknown", "may_dispatch": False, "reason": "missing_fresh_evidence"}
    age = clock - stamp.astimezone(timezone.utc)
    if not timedelta(0) <= age <= MAX_AGE:
        return {"decision": "no_conflict", "may_dispatch": False, "reason": "override_expired"}
    if incumbent["action"] == operation:
        return {"decision": "no_conflict", "may_dispatch": False, "reason": "same_action"}
    old_priority = SOURCE_PRIORITY.get(incumbent.get("source"))
    if old_priority is None:
        return {"decision": "unknown", "may_dispatch": False, "reason": "unknown_owner"}
    new_priority = SOURCE_PRIORITY[source]
    if old_priority > new_priority:
        decision, reason = "recommend_hold", "higher_priority_existing_intent"
    elif old_priority < new_priority:
        decision, reason = "recommend_override", "higher_priority_new_intent"
    else:
        decision, reason = "needs_human_review", "equal_priority_conflict"
    return {"decision": decision, "reason": reason, "may_dispatch": False}
