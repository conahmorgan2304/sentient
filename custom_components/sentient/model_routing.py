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


def choose(options: dict[str, Any], data: dict[str, Any], observations: dict[str, list],
           *, default_entity: str | None) -> dict[str, Any]:
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
    return {"entity_id": secondary, "switched": True,
            "reason": "cheaper_estimated_cost_with_measured_service_reliability",
            "estimate_only": True, "billed_cost_known": False}
