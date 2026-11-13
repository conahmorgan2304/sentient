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
CONF_COGNITIVE_DECIDES = "cognitive_decides"  # off by default: let it decide incident stages once it has earned it
CONF_COGNITIVE_HEADS_UP = "cognitive_quiet_heads_up"  # off by default: let it quieten night heads-ups for household

# Earning the incident stage. Each kind of incident is judged on its own, from the household's reviews: a high
# overall score can't hide a kind it gets wrong. Missed threats are counted on their own and any one stops it.
AUTHORITY_SCOPE = "incident_stage"
AUTHORITY_WINDOW = 60  # the most recent reviews that count, so a change in the house shows up
MIN_HOUSEHOLD = 10  # reviewed household incidents
MIN_OTHERS = 10  # reviewed visitor, false-detection and concerning incidents together
HOUSEHOLD_AGREEMENT = 0.9  # household incidents it called likely household (or resolved)
FALSE_HOUSEHOLD_MAX = 0.05  # other incidents it wrongly called likely household
SEVERITY = {"resolved": 0, "unknown": 0, "observed": 1, "likely household": 1, "investigating": 2, "concerning": 3}
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


ENTRY_FIRST_SECONDS = 120  # an entry opening this soon before a camera report is "came in that way"


def arrival(incident: dict[str, Any]) -> tuple[bool, bool]:
    """(came in through an entry, rang or stood at the door) from the order of the evidence.

    Came in: an entry contact opened, and then — within two minutes, from a different source — a camera
    reported someone. Rang: a doorbell or doorstep visit is part of it."""
    rows = [r for r in (incident.get("evidence") or []) if isinstance(r, dict)]
    rang = any(r.get("kind") == "visit" for r in rows)
    opened = [(t, r.get("source")) for r in rows if r.get("kind") == "contact" and r.get("status") == "observed"
              and str(r.get("text", "")).rsplit(":", 1)[-1].strip().lower() in ("on", "open", "opened")
              and (t := _time(r.get("time"))) is not None]
    seen = [(t, r.get("source")) for r in rows if r.get("kind") in ("detection", "event")
            and (t := _time(r.get("time"))) is not None]
    came_in = any(0 <= (camera - entry).total_seconds() <= ENTRY_FIRST_SECONDS and source != where
                  for entry, source in opened for camera, where in seen)
    first_seen = min((t for t, _ in seen), default=None)
    if came_in and first_seen is not None and opened and min(t for t, _ in opened) > first_seen:
        came_in = False  # someone was seen before anything opened: not an arrival through the entry
    return came_in, rang


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
    came_in, rang = arrival(incident)
    if age is not None and age > 1800:
        candidate = "resolved"
    elif household and level < 2:
        # Reads the same household marks as the existing stages, so a household episode can agree with them.
        candidate = "likely household"
    elif came_in and not rang and level < 2:
        # Its own reasoning, beyond the rules: an entry (the gate, a door) opened first and a camera then saw
        # someone just inside it — the way people who live here come and go, not the way a stranger arrives.
        # Not proof of who: the household's reviews decide whether this holds in this house.
        candidate = "likely household"
        gaps.append("identity_unconfirmed")
    elif rang and level < 2:
        candidate = "observed"  # the doorbell or the doorstep: a caller, not someone from the house
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


# What each decision needs before the core makes it. Quietening a spoken heads-up asks more than naming a stage.
SCOPES = {
    AUTHORITY_SCOPE: {"household": MIN_HOUSEHOLD, "others": MIN_OTHERS, "agreement": HOUSEHOLD_AGREEMENT,
                      "false_household": FALSE_HOUSEHOLD_MAX,
                      "never": ["changes or holds back an alert", "calls an incident less serious than the rules' concerning"]},
    "night_heads_up": {"household": 20, "others": 10, "agreement": 0.95, "false_household": 0.0,
                       "never": ["holds back a phone alert or anything at the away, fence or loitering levels",
                                 "quietens a heads-up it isn't sure is someone from the house"]},
}


def _score(rows: list[dict[str, Any]], who: str) -> dict[str, Any]:
    """How a judge ("core" or "legacy", the usual rules) did on each kind of reviewed incident."""
    household = [r for r in rows if r["label"] == "household"]
    others = [r for r in rows if r["label"] != "household"]
    agreed = sum(r.get(who) in ("likely household", "resolved") for r in household)
    false_household = sum(r.get(who) == "likely household" for r in others)
    missed = sum(r["label"] == "concerning" and SEVERITY.get(r.get(who), 0) < SEVERITY["investigating"] for r in others)
    return {"household": len(household), "agreed": agreed, "others": len(others),
            "false_household": false_household, "missed": missed,
            "household_agreement": round(agreed / len(household), 3) if household else None,
            "wrongly_called_household": round(false_household / len(others), 3) if others else None}


def authority(reviews: list[dict[str, Any]], scope: str = AUTHORITY_SCOPE) -> dict[str, Any]:
    """Whether the cognitive core has earned a decision, from the household's reviews.

    reviews: [{"label": household|visitor|false_detection|concerning|unresolved, "core": the core's stage then,
    "legacy": the usual rules' stage then}]. Each kind of incident is judged on its own; any concerning incident
    it called less serious stops it. The usual rules are scored the same way, so it's clear which judges better.
    """
    need = SCOPES[scope]
    rows = [r for r in reviews if isinstance(r, dict) and r.get("label") in
            ("household", "visitor", "false_detection", "concerning") and isinstance(r.get("core"), str)][-AUTHORITY_WINDOW:]
    core = _score(rows, "core")
    progress = {
        "household_reviewed": core["household"], "household_needed": need["household"],
        "household_agreement": core["household_agreement"],
        "others_reviewed": core["others"], "others_needed": need["others"],
        "wrongly_called_household": core["wrongly_called_household"],
        "missed_threats": core["missed"], "concerning_reviewed": sum(r["label"] == "concerning" for r in rows),
    }
    why = []
    if core["household"] < need["household"]:
        why.append(f"needs {need['household'] - core['household']} more reviewed household incidents")
    elif core["agreed"] / core["household"] < need["agreement"]:
        why.append(f"agreed on {core['agreed']} of {core['household']} household incidents (needs {round(need['agreement'] * 100)}%)")
    if core["others"] < need["others"]:
        why.append(f"needs {need['others'] - core['others']} more reviewed visitor, false-alarm or concerning incidents")
    elif core["false_household"] / core["others"] > need["false_household"]:
        why.append(f"called {core['false_household']} of {core['others']} other incidents household")
    if core["missed"]:
        why.append(f"called {core['missed']} concerning incident{'s' if core['missed'] > 1 else ''} less serious")
    rules_rows = [r for r in rows if isinstance(r.get("legacy"), str)]
    return {"scope": scope, "earned": not why, "why": why, "progress": progress,
            "compared": {"core": core, "rules": _score(rules_rows, "legacy")} if rules_rows else {"core": core},
            "never": need["never"]}


def decided_stage(legacy: str, core: str | None, earned: bool) -> tuple[str, str]:
    """The incident stage the house is told, and who decided it: the core when it has earned it, never less
    serious than the rules' "concerning"."""
    if not earned or not core or core not in SEVERITY:
        return legacy, "rules"
    if legacy == "concerning" and SEVERITY[core] < SEVERITY["concerning"]:
        return legacy, "rules (the core can't lower a concerning incident)"
    return core, "cognitive core"


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
