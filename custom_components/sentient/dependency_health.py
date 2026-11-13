"""Privacy-safe dependency-chain diagnostics for Sentient's major functions.

An unavailable upstream component is different from a confirmed failed action.
Only aggregate statuses are exported: no camera names, speaker IDs or room labels.
"""
from __future__ import annotations

from typing import Any

_UNKNOWN = {"unknown", "unavailable"}


def evaluate(dependencies: dict[str, bool | None],
             chains: dict[str, tuple[str, ...]] | None = None) -> dict[str, Any]:
    """Return root causes and impacted features without inventing device state.

    True = available, False = confirmed unavailable, None = not measured.
    Feature status is unknown if no dependency is known failed but at least
    one dependency was not measured. This never diagnoses a failed action.
    """
    chains = chains or {
        "voice_conversation": ("voice_pipeline", "tts"),
        "speaker_personalisation": ("voice_pipeline", "speaker_recognition"),
        "camera_investigation": ("frigate", "camera_evidence"),
        "spoken_security_alert": ("camera_evidence", "tts"),
        "learned_heating": ("recorder", "climate"),
    }
    results = {}
    for feature, required in chains.items():
        unavailable = [part for part in required if dependencies.get(part) is False]
        unchecked = [part for part in required if dependencies.get(part) is None]
        results[feature] = {
            "status": "degraded" if unavailable else "unknown" if unchecked else "available",
            "failed_dependencies": unavailable,
            "unverified_dependencies": unchecked,
        }
    return {
        "features": results,
        "degraded_count": sum(v["status"] == "degraded" for v in results.values()),
        "unknown_count": sum(v["status"] == "unknown" for v in results.values()),
        "note": "Dependency availability does not prove an end-to-end feature works.",
    }


def runtime_dependencies(rt: Any, hass: Any) -> dict[str, bool | None]:
    """Conservatively derive availability from running integration state."""
    components = getattr(getattr(hass, "config", None), "components", set()) or set()
    def running(name: str) -> bool | None:
        instance = getattr(rt, name, None)
        if instance is None:
            return None
        stopped = getattr(instance, "_stopped", None)
        return None if stopped is None else not bool(stopped)

    climate_states = getattr(getattr(hass, "states", None), "async_all", None)
    climate = None
    if callable(climate_states):
        try:
            states = climate_states("climate")
            climate = any(s.state not in _UNKNOWN for s in states) if states else None
        except (TypeError, ValueError):
            climate = None
    return {
        "voice_pipeline": None,  # Requires an actual recent end-to-end test
        "tts": None,  # Configuration alone does not prove TTS playback
        "speaker_recognition": None,  # Matching must be proven by actual turns
        "frigate": True if "frigate" in components else None,
        "camera_evidence": running("security_events"),
        "recorder": True if "recorder" in components else False,
        "climate": climate,
    }
