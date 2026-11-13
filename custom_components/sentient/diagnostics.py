"""Diagnostics: a snapshot for bug reports, with anything personal removed."""

from __future__ import annotations

from typing import Any

from homeassistant.components.diagnostics import async_redact_data
from homeassistant.core import HomeAssistant

from . import SentientConfigEntry
from .const import (
    CONF_AWAY_PEOPLE, CONF_BINS_CALENDAR, CONF_BRIEFING_CALENDARS, CONF_FACE_SENSOR, CONF_HOUSE_RULES, CONF_NOTIFY_TARGETS,
    CONF_PERSONA, CONF_PHONE_APPS, CONF_SEARXNG_URL, CONF_TANDOOR_TOKEN, CONF_TANDOOR_URL, CONF_WELCOME_PEOPLE,
)

TO_REDACT = {CONF_HOUSE_RULES, CONF_PERSONA, "banned_words", "description", "name", "message", "what", "text",
             "personality_notes", "style_notes", "known_cars", "face_people", "clip_looks",
             CONF_TANDOOR_TOKEN, CONF_TANDOOR_URL, CONF_SEARXNG_URL,  # secrets and addresses on the house's network
             CONF_NOTIFY_TARGETS, CONF_PHONE_APPS, CONF_WELCOME_PEOPLE, CONF_AWAY_PEOPLE, CONF_BRIEFING_CALENDARS,
             CONF_BINS_CALENDAR, CONF_FACE_SENSOR,  # phones, people and calendars name who lives here
             "pronunciations", "warnings_region", "unexplained_entities"}  # names, where the house is, what's watched


async def async_get_config_entry_diagnostics(hass: HomeAssistant, entry: SentientConfigEntry) -> dict[str, Any]:
    rt = entry.runtime_data
    from .camsecurity import camera_for

    out_startup = getattr(rt, "startup", None) or {}
    return {
        "startup": out_startup,
        "options": async_redact_data({**entry.data, **entry.options}, TO_REDACT),
        "memory": {"count": len(rt.memory.items), "categories": sorted({m.category for m in rt.memory.items})},
        "brain": await _brain(hass, rt),
        "speech": {"quiet_now": rt.speech.quiet_now(), "speaker_now": rt.speech.pick_speaker()},
        "rooms": rt.tracker.scores(),
        "watches": [
            async_redact_data({**w.summary(), "entity_id": w.entity_id, "wakes_on": w.triggers, "spot": w.spot}, TO_REDACT)
            for w in rt.watches.watches
        ],
        "ai": rt.ai.describe(),
        "camera_security": {
            "enabled": bool(rt.camsec.options.get("camera_security")),
            "camera_matches": [{"sensor": sensor, "camera": camera_for(hass, sensor)} for sensor in rt.camsec.sensors],
            "pending_alert_checks": len(rt.camsec._tasks),
        },
        "clip_watch": {
            "enabled": rt.clipwatch.enabled,
            "active_cameras": sorted(rt.clipwatch._busy),
            "retained_summaries": len(rt.clipwatch.items),
            "skipped": dict(rt.clipwatch.skipped),
        },
        "shared_clip_analysis": rt.clip_analysis.describe(),
        "event_evidence": {"enabled": rt.security_events.on("security_event_evidence"),
                           "retained": len(rt.security_events.items), "mqtt_subscribed": rt.security_events._unsub is not None},
        "noticing": [async_redact_data(d, {"changes", "message"}) for d in list(rt.noticer.decisions)[:10]],
        "routines": [
            {"entity_id": r.entity_id, "state": r.state, "days": getattr(r, "days", "link"), "support": r.support,
             "adopted": r.adopted, "declined": r.declined, "offered": bool(r.suggested)}
            for r in rt.learner.all()
        ],
        "last_learned": rt.learner.last_run,
        "visitors_today": len(rt.visitors.today()),
        "spots": [{"camera": s["camera"]} for s in rt.spots.spots.values()],
        "last_tidy": async_redact_data(rt.upkeep.last_tidy or {}, {"merged"}),
        "backup_privacy": await rt.autobackup.async_privacy_summary(),
        "dependency_chains": _dependency_chains(rt, hass),
    }


async def _brain(hass: HomeAssistant, rt: Any) -> dict[str, Any] | None:
    """Counts and switches only: no changes, beliefs, conversations or names."""
    brain = getattr(rt, "brain", None)
    if brain is None or not brain.started:
        return None
    try:
        status = await brain.async_status()
    except Exception as err:  # noqa: BLE001 - diagnostics must download even if the database is unhappy
        return {"error": str(err)[:200]}
    return {
        "ledger_on": status.get("ledger_on"), "ledger_rows": status.get("ledger"), "disk_mb": status.get("disk_mb"),
        "search": status.get("search"), "recovered_from_damage": bool(brain.db.recovered), "warming": status.get("warming"),
        "gating": status.get("gating"), "prediction_percent": (status.get("prediction") or {}).get("percent"),
        "beliefs": len(status.get("beliefs") or []), "memories_copied": status.get("memories"),
        "case_memory_entries": len(brain.cases),
    }


def _dependency_chains(rt: Any, hass: HomeAssistant) -> dict[str, Any]:
    """Aggregate availability without exporting household entity IDs."""
    from .dependency_health import evaluate, runtime_dependencies

    return evaluate(runtime_dependencies(rt, hass))
