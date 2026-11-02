"""Never touch these: devices Sentient must never switch, dim or change by itself.

One list, checked everywhere Sentient acts on its own or on a voice request it carries out: rules,
routines and offers, auto-off, away lights, motion lights, light shows, learned routines, the
heating, scenes and guarded voice control. The voice is also told not to touch them. Reading them,
reporting on them and announcing about them is fine. An empty list changes nothing.
"""

from __future__ import annotations

from typing import Any

from homeassistant.core import HomeAssistant

from .const import CONF_NEVER_ENTITIES, DOMAIN


def never_list(hass: HomeAssistant) -> set[str]:
    entries = hass.config_entries.async_loaded_entries(DOMAIN)
    rt = getattr(entries[0], "runtime_data", None) if entries else None
    return set((getattr(rt, "options", None) or {}).get(CONF_NEVER_ENTITIES) or [])


def blocked(hass: HomeAssistant, entity_id: str | None) -> bool:
    return bool(entity_id) and entity_id in never_list(hass)


def allowed(hass: HomeAssistant, entity_ids: list[str]) -> list[str]:
    """The ones that may be touched."""
    never = never_list(hass)
    return [e for e in entity_ids if e not in never]


def touches(hass: HomeAssistant, config: Any) -> list[str]:
    """Never-touch devices named anywhere in some actions (or a scene's entity list)."""
    never = never_list(hass)
    if not never:
        return []
    found: list[str] = []

    def walk(value: Any) -> None:
        if isinstance(value, str):
            for part in value.replace(",", " ").split():
                if part in never and part not in found:
                    found.append(part)
        elif isinstance(value, dict):
            for v in value.values():
                walk(v)
        elif isinstance(value, (list, tuple, set)):
            for v in value:
                walk(v)

    walk(config)
    return found


def names(hass: HomeAssistant, entity_ids: list[str]) -> str:
    out = []
    for e in entity_ids:
        st = hass.states.get(e)
        out.append(st.name if st else e)
    return ", ".join(out)
