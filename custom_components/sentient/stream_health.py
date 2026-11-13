"""Provider-neutral stream lifecycle evaluation, including Nest.

Read-only: a grey frame or unobserved video is not treated as working playback.
"""
from __future__ import annotations
from typing import Any

def assess(events: list[dict[str, Any]], *, first_frame_deadline_ms: int = 12000) -> dict[str, Any]:
    state = "unverified"
    first_frame = None
    for row in events[:100]:
        if not isinstance(row, dict):
            continue
        stage = row.get("stage")
        elapsed = row.get("elapsed_ms")
        if stage == "frame" and isinstance(elapsed, (int, float)) and 0 <= elapsed <= 600000:
            first_frame = elapsed if first_frame is None else min(first_frame, elapsed)
            state = "working"
        elif stage in {"error", "expired", "stalled"} and state != "working":
            state = "failed"
        elif stage in {"requested", "connected", "buffering"} and state == "unverified":
            state = "unverified"
    if first_frame is not None and first_frame > first_frame_deadline_ms:
        state = "slow_first_frame"
    return {"playback": state, "first_frame_ms": first_frame,
            "fallback_recommended": state in {"failed", "slow_first_frame"},
            "verified_live_on_this_device": False,
            "note": "Inspect actual Home Assistant/Nest player diagnostics for live verification."}
