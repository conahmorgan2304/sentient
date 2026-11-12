"""Read-only Cognitive Core 2.0 coordinator and bounded shadow evaluation.

Off unless Settings → Safety & security → "Compare security incidents (testing)" is on. It adds nothing to
what the voice is told. Callers supply only already-authorised, appropriately filtered evidence. This
module never reads Home Assistant state, calls services, triggers speech or
grants permission. Existing alert/automation rules remain authoritative.

The coordinator observes the same incident/outcome repeatedly, but counts a
revision only when the evidence/verification changed. It keeps no spoken text,
names, identifiers or original event data in its counters.
"""
from __future__ import annotations

from collections import Counter, OrderedDict, deque
from hashlib import blake2s
from secrets import token_bytes
from datetime import datetime, timezone
from time import perf_counter_ns
from typing import Any

from .cognitive import decision

CONF_COGNITIVE_SHADOW = "cognitive_shadow"  # off by default: a second opinion, measured, never acted on
MAX_TRACKED = 128
PROPOSED = frozenset({"observed", "investigating", "likely household", "concerning", "resolved", "unknown"})
HOUSEHOLD_WORDS = ("someone from the house", "false alarm", "household")


def _time(value: Any) -> datetime | None:
    if not isinstance(value, str):
        return None
    try:
        stamp = datetime.fromisoformat(value.replace("Z", "+00:00"))
    except ValueError:
        return None
    if stamp.tzinfo is None:
        return None
    return stamp.astimezone(timezone.utc)


def _as_utc(now: datetime | None) -> datetime:
    stamp = now or datetime.now(timezone.utc)
    if stamp.tzinfo is None:
        raise ValueError("Cognitive Core requires an offset-aware timestamp")
    return stamp.astimezone(timezone.utc)


def investigate(incident: dict[str, Any], assessment: dict[str, Any], *,
                baseline_stage: str, now: datetime | None = None) -> dict[str, Any]:
    """Offer a second explanation without changing the existing incident stage.

    Only observed, independent signals support an investigation; a high
    alert level is retained as concerning even with missing evidence.
    A quiet episode can resolve in shadow mode, but never suppresses alerts.
    """
    now = _as_utc(now)
    observed = int(assessment.get("reported_observations") or 0)
    unknown = int(assessment.get("unknown_reports") or 0)
    sources = int(assessment.get("source_count") or 0)
    level = int(incident.get("alert_level") or 0)
    end = _time(incident.get("end"))
    gaps = list(assessment.get("decision", {}).get("gaps") or [])[:6]
    if end is None or end > now:
        gaps.append("end_time_unsettled")
    age = (now - end).total_seconds() if end is not None else None
    household = any(word in str(row.get("text", "")).lower() for row in (incident.get("evidence") or [])
                    if isinstance(row, dict) for word in HOUSEHOLD_WORDS)
    if age is not None and age > 1800:
        candidate = "resolved"
    elif household and level < 2:
        # Reads the same household marks as the existing stages, so a household episode can agree with them.
        candidate = "likely household"
    elif level >= 2:
        candidate = "concerning"
    elif unknown and not observed and not level:
        candidate = "unknown"
    elif level == 1 or sources > 1:
        candidate = "investigating"
    elif observed:
        candidate = "observed"
    else:
        candidate = "unknown"
    # A mixture of reports cannot establish a secure or verified conclusion.
    if unknown:
        gaps.append("some_evidence_unsettled")
    if assessment.get("corroboration") != "reported_activity":
        gaps.append("cross_sensor_link_unconfirmed")
    return {
        "stage": candidate,
        "legacy_stage": baseline_stage,
        "agrees": candidate == baseline_stage,
        "uncertainty": sorted(set(gaps))[:6],
        "identity_confirmed": False,
        "route_confirmed": False,
        "authoritative": False,
        "decision": decision(
            "incident_shadow_investigation",
            evidence=[f"reported:{observed}", f"sources:{sources}", f"alert_level:{level}"],
            gaps=gaps,
            choice=candidate,
            reasons=["evidence_only", "no_alert_changes"],
        ),
    }


def task_progress(outcome: dict[str, Any]) -> dict[str, Any]:
    """Read-only progress report for an already attempted guarded action batch.

    No service calls, retries, automatic rollback or inferred physical safety.
    A failed step stops the existing Control batch; this only describes it.
    """
    rows = [row for row in (outcome.get("actions") or [])[:20] if isinstance(row, dict)]
    counts = Counter(str(row.get("status") or "unknown") for row in rows)
    if not rows:
        state = "unknown"
    elif counts["verified"] == len(rows):
        state = "reported_complete"
    elif counts["failed"]:
        state = "partial_or_failed"
    elif counts["attempted"] or counts["not_started"] or counts["skipped"]:
        state = "needs_verification"
    else:
        state = "unknown"
    return {
        "state": state,
        "steps": len(rows),
        "reported_complete": counts["verified"],
        "attempted_unverified": counts["attempted"],
        "failed": counts["failed"],
        "not_started": counts["not_started"] + counts["skipped"],
        "can_continue_automatically": False,
        "requires_fresh_state_check": state != "reported_complete",
        "decision": decision(
            "guarded_task_progress",
            evidence=[f"{name}:{counts[name]}" for name in
                      ("verified", "attempted", "failed", "not_started", "skipped") if counts[name]],
            gaps=[] if state == "reported_complete" else ["unfinished_or_unverified_steps"],
            choice=state,
            reasons=["no_automatic_retry", "reported_state_not_physical_proof"],
        ),
    }


def review_outcome(outcome: dict[str, Any]) -> dict[str, Any]:
    """Feedback limited to reported action status: never an automatic retry."""
    summary = outcome.get("assessment") or {}
    state = str(summary.get("state") or "unknown")
    if state == "reported_complete":
        lesson = "reported_success"
    elif state in ("failed", "partial", "unverified", "not_completed"):
        lesson = "needs_verification"
    else:
        lesson = "insufficient_evidence"
    return {
        "lesson": lesson,
        "retry_authorised": False,
        "physical_completion_confirmed": False,
        "decision": decision(
            "action_outcome_shadow_feedback",
            evidence=[f"outcome:{state}"],
            gaps=[] if lesson == "reported_success" else ["device_result_unsettled"],
            choice=lesson,
            reasons=["read_only_learning", "no_automatic_retry"],
        ),
    }


class CognitiveCoordinator:
    """Bounded per-runtime counters; no personal or raw event data stored."""

    def __init__(self) -> None:
        # Use per-runtime keyed digests rather than retaining incident IDs or
        # personally identifiable source IDs in shadow metrics.
        self._key = token_bytes(32)
        self._seen: dict[str, tuple[Any, ...]] = {}
        self._recent: deque[str] = deque(maxlen=MAX_TRACKED)
        self.incidents = Counter()
        self.outcomes = Counter()
        self._outcome_seen = OrderedDict()
        self._evaluations = 0
        self._elapsed_ns = 0
        self._slowest_ns = 0

    def reset(self) -> None:
        """Discard shadow statistics in place so other runtime users keep the same coordinator."""
        self.__init__()

    def _digest(self, identifier: str) -> str:
        return blake2s(identifier.encode("utf-8", errors="replace"), key=self._key, digest_size=16).hexdigest()

    def incident(self, item: dict[str, Any], assessment: dict[str, Any],
                 baseline_stage: str, *, now: datetime | None = None) -> dict[str, Any]:
        started_ns = perf_counter_ns()
        shadow = investigate(item, assessment, baseline_stage=baseline_stage, now=now)
        elapsed_ns = perf_counter_ns() - started_ns
        self._evaluations += 1
        self._elapsed_ns += elapsed_ns
        self._slowest_ns = max(self._slowest_ns, elapsed_ns)
        # Deduplicate periodic Mind-card redraws; count only changed evidence.
        identifier = str(item.get("id") or "")
        if identifier:
            key = self._digest(identifier)
            # Count changes to evidence composition, including when one report
            # is corrected but the total number of reports stays unchanged.
            # Store only aggregated counts, never source names or event text.
            evidence_counts = Counter(
                (str(row.get("kind") or "unknown"), str(row.get("status") or "unknown"))
                for row in (item.get("evidence") or [])[-50:] if isinstance(row, dict)
            )
            signature = (
                item.get("end"), tuple(sorted(evidence_counts.items())),
                int(item.get("alert_level") or 0), baseline_stage, shadow["stage"],
                assessment.get("status"), assessment.get("corroboration"),
                assessment.get("reported_observations"), assessment.get("inferences"),
                assessment.get("unknown_reports"), assessment.get("source_count"),
            )
            if self._seen.get(key) != signature:
                if key not in self._seen:
                    if len(self._recent) == MAX_TRACKED:
                        self._seen.pop(self._recent[0], None)
                    self._recent.append(key)
                self._seen[key] = signature
                self.incidents["compared"] += 1
                self.incidents["agreed" if shadow["agrees"] else "disagreed"] += 1
        return shadow

    def outcome(self, outcome: dict[str, Any]) -> dict[str, Any]:
        feedback = review_outcome(outcome)
        feedback["task"] = task_progress(outcome)
        # Control sets recorded_at once per batch and keeps it through its
        # delayed verification. Use that stable timestamp when available.
        key = outcome.get("recorded_at") or id(outcome)
        new = feedback["lesson"]
        old = self._outcome_seen.get(key)
        if old != new:
            if old:
                self.outcomes[old] = max(0, self.outcomes[old] - 1)
            self.outcomes[new] += 1
        self._outcome_seen[key] = new
        self._outcome_seen.move_to_end(key)
        while len(self._outcome_seen) > MAX_TRACKED:
            self._outcome_seen.popitem(last=False)
        return feedback

    def summary(self) -> dict[str, Any]:
        checked = self.outcomes["reported_success"] + self.outcomes["needs_verification"]
        # The cumulative revision count includes repeat evidence changes. Use
        # the latest stage for each tracked incident when estimating agreement,
        # otherwise one very noisy camera can dominate the comparison.
        tracked = len(self._seen)
        current_agreement = sum(signature[3] == signature[4] for signature in self._seen.values())
        return {
            "mode": "shadow",
            "tracked_incidents": tracked,
            "latest_stage_agreement_rate": round(current_agreement / tracked, 3) if tracked >= 10 else None,
            "agreement_sample_minimum": 10,
            "evaluations": self._evaluations,
            "mean_evaluation_ms": round(self._elapsed_ns / self._evaluations / 1_000_000, 3)
                                  if self._evaluations else 0,
            "slowest_evaluation_ms": round(self._slowest_ns / 1_000_000, 3),
            "reported_completion_rate": round(self.outcomes["reported_success"] / checked, 3) if checked >= 10 else None,
            "incidents_compared": self.incidents["compared"],
            "incidents_agreed": self.incidents["agreed"],
            "incidents_disagreed": self.incidents["disagreed"],
            "reported_successes": self.outcomes["reported_success"],
            "outcomes_needing_verification": self.outcomes["needs_verification"],
            "autonomous_actions": 0,
        }
