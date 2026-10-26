"""Diagnostics: a snapshot for bug reports, with anything personal removed."""

from __future__ import annotations

from typing import Any

from homeassistant.components.diagnostics import async_redact_data
from homeassistant.core import HomeAssistant

from . import SentientConfigEntry
from .const import CONF_HOUSE_RULES, CONF_PERSONA

TO_REDACT = {CONF_HOUSE_RULES, CONF_PERSONA, "banned_words", "description", "name", "message", "what", "text"}


async def async_get_config_entry_diagnostics(hass: HomeAssistant, entry: SentientConfigEntry) -> dict[str, Any]:
    rt = entry.runtime_data
    return {
        "options": async_redact_data({**entry.data, **entry.options}, TO_REDACT),
        "memory": {"count": len(rt.memory.items), "categories": sorted({m.category for m in rt.memory.items})},
        "speech": {"quiet_now": rt.speech.quiet_now(), "speaker_now": rt.speech.pick_speaker()},
        "rooms": rt.tracker.scores(),
        "watches": [
            async_redact_data({**w.summary(), "entity_id": w.entity_id, "wakes_on": w.triggers, "spot": w.spot}, TO_REDACT)
            for w in rt.watches.watches
        ],
        "ai": rt.ai.describe(),
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
    }
