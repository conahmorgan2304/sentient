"""Short-lived session-owned cognitive working context.

This is *not* another long-term memory store. It never reads household state,
never bypasses an owner/session boundary and cannot authorise an action.
The content is explicitly marked as old context that must be rechecked.
"""
from __future__ import annotations

from datetime import datetime, timedelta, timezone
from typing import Any

MAX_AGE = timedelta(minutes=5)


def refresh(previous: dict[str, Any] | None, *, goal: str, topic: str,
            entities: list[str], kind: str | None, changed: bool,
            now: datetime | None = None) -> dict[str, Any]:
    now = now or datetime.now(timezone.utc)
    if now.tzinfo is None:
        raise ValueError("working-memory time must be aware")
    now = now.astimezone(timezone.utc)
    old = previous if isinstance(previous, dict) else {}
    try:
        updated = datetime.fromisoformat(str(old.get("updated_at") or ""))
        if updated.tzinfo is None:
            raise ValueError("missing timezone")
        expired = now - updated.astimezone(timezone.utc) > MAX_AGE or updated > now
    except (ValueError, TypeError):
        expired = True
    linked = not changed and not expired and bool(kind)
    context = {
        "goal": str((old.get("goal") if linked else goal) or "")[:200],
        "topic": str((old.get("topic") if linked else topic) or "")[:200],
        "entities": [str(e)[:90] for e in entities[:8]],
        "followup_kind": str(kind or "")[:30],
        "updated_at": now.isoformat(),
        "evidence_is_stale": True,
        "requires_fresh_permission_check": True,
        "can_authorise_actions": False,
    }
    return context


def public_view(context: dict[str, Any] | None) -> dict[str, Any] | None:
    """Keep only bounded owner-local context for a model or session screen."""
    if not context:
        return None
    # This description appears in Home Assistant's Mind diagnostics, not
    # exclusively in the user's private model context. The free-form goal
    # and topic can themselves contain private names, rooms or entity IDs.
    # Keep them only on the session object; publish status flags, not text.
    return {k: context[k] for k in ("followup_kind", "evidence_is_stale",
                                    "requires_fresh_permission_check",
                                    "can_authorise_actions") if k in context}
