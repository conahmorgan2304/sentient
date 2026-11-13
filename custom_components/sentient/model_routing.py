"""Conservative, explicitly enabled AI Task routing for text-only background jobs.

A different provider is chosen only with at least 20 observed service outcomes
for each entity, a user-supplied price estimate for *both* (not billed prices),
and comparable success/latency. Vision and structured output keep their current
provider because successful text jobs do not establish those capabilities.
"""
from __future__ import annotations

import math
from typing import Any


def stats(rows: list[tuple[bool, int]], *, minimum: int = 20) -> dict[str, Any]:
    valid = [(ok, ms) for ok, ms in rows[-100:]
             if type(ok) is bool and isinstance(ms, (float, int)) and math.isfinite(ms) and 0 <= ms <= 600000]
    n = len(valid)
    if n < minimum:
        return {"samples": n, "success_rate": None, "p95_ms": None}
    times = sorted(ms for _, ms in valid)
    return {"samples": n, "success_rate": round(sum(ok for ok, _ in valid)/n, 3),
            "p95_ms": round(times[min(n-1, int(n*.95))])}


def family(data: dict[str, Any]) -> str:
    """The kind of job, from its task name: "Sentient: worth saying?" → "worth saying?"."""
    name = str(data.get("task_name") or "")
    return (name.split(":", 1)[1] if ":" in name else name).strip()[:40] or "other"


FAMILY_MINIMUM = 10  # measured calls of one kind of job before its own record can stop the switch


def choose(options: dict[str, Any], data: dict[str, Any], observations: dict[str, list],
           *, default_entity: str | None, by_family: dict[tuple[str, str], list] | None = None) -> dict[str, Any]:
    result = {"entity_id": default_entity, "switched": False,
              "reason": "default_provider", "estimate_only": True}
    if options.get("ai_routing_enabled") is not True:
        return result
    if data.get("entity_id") or data.get("attachments") or data.get("structure"):
        return {**result, "reason": "explicit_or_multimodal_or_structured"}
    if not str(data.get("task_name") or "").startswith("Sentient"):
        return {**result, "reason": "unknown_task"}
    secondary = options.get("ai_routing_alternative")
    if not isinstance(default_entity, str) or not isinstance(secondary, str) or (
        not default_entity.startswith("ai_task.") or not secondary.startswith("ai_task.")
        or default_entity == secondary
    ):
        return {**result, "reason": "missing_whitelisted_provider"}
    try:
        price_main = float(options.get("ai_routing_primary_cost"))
        price_alt = float(options.get("ai_routing_alternative_cost"))
    except (ValueError, TypeError, OverflowError):
        return {**result, "reason": "no_comparable_cost_estimates"}
    if (not math.isfinite(price_main) or not math.isfinite(price_alt)
            or not 0 <= price_alt < price_main <= 100):
        return {**result, "reason": "alternative_not_cheaper"}
    main = stats(observations.get(default_entity) or [])
    alt = stats(observations.get(secondary) or [])
    if main["success_rate"] is None or alt["success_rate"] is None:
        return {**result, "reason": "insufficient_measured_service_calls"}
    try:
        max_latency = float(options.get("ai_routing_max_p95_ms") or 4000)
    except (TypeError, ValueError, OverflowError):
        return {**result, "reason": "invalid_latency_limit"}
    if (not math.isfinite(max_latency) or not 500 <= max_latency <= 30000):
        return {**result, "reason": "invalid_latency_limit"}
    if (alt["success_rate"] < max(.95, main["success_rate"] - .02)
            or alt["p95_ms"] > max_latency):
        return {**result, "reason": "reliability_or_latency_limit"}
    # measured per kind of job as well: a cheaper AI that's fine overall but keeps failing at one kind of job
    # stops getting that kind (and gets it back when its record recovers)
    kind = family(data)
    alt_kind = stats((by_family or {}).get((secondary, kind)) or [], minimum=FAMILY_MINIMUM)
    main_kind = stats((by_family or {}).get((default_entity, kind)) or [], minimum=FAMILY_MINIMUM)
    if alt_kind["success_rate"] is not None and alt_kind["success_rate"] < max(
            .9, (main_kind["success_rate"] if main_kind["success_rate"] is not None else 1.0) - .05):
        return {**result, "reason": f"worse_at_{kind}"[:60]}
    return {"entity_id": secondary, "switched": True,
            "reason": "cheaper_estimated_cost_with_measured_service_reliability",
            "estimate_only": True, "billed_cost_known": False}
