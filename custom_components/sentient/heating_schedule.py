"""Heating schedules: set a thermostat to a temperature (or off) at a time, on chosen days.

Kept in the alarms store as silent entries tagged with a `heating` spec, so the same clock that runs
alarms runs these. The actions are rebuilt from the spec every time, never trusted from storage:
only the thermostats chosen under Heating zones can be scheduled, and the temperature is held to a
sensible range. Switched off in Settings, the entries stay saved and simply don't run.
"""

from __future__ import annotations

from typing import Any

from homeassistant.core import HomeAssistant

from .const import CONF_HEATING_ENTITIES, CONF_HEATING_SCHEDULE

MIN_TEMP = 5.0
MAX_TEMP = 28.0


def enabled(options: dict[str, Any]) -> bool:
    return bool(options.get(CONF_HEATING_SCHEDULE, False))


def heating_actions(hass: HomeAssistant, options: dict[str, Any], spec: Any) -> tuple[list[dict[str, Any]], str | None]:
    """(the actions, None) or ([], why not)."""
    if not isinstance(spec, dict):
        return [], "No heating instructions"
    entity_id = str(spec.get("entity_id") or "")
    if entity_id not in (options.get(CONF_HEATING_ENTITIES) or []):
        return [], "That thermostat isn't one of the heating zones chosen in Sentient"
    if not entity_id.startswith("climate."):
        return [], "Only thermostats can be scheduled"
    target = {"entity_id": entity_id}
    temperature = spec.get("temperature")
    if temperature in (None, "", "off"):
        return [{"action": "climate.turn_off", "target": target}], None
    try:
        degrees = float(temperature)
    except (TypeError, ValueError):
        return [], "The temperature must be a number"
    if not MIN_TEMP <= degrees <= MAX_TEMP:
        return [], f"The temperature must be between {MIN_TEMP:g}° and {MAX_TEMP:g}°"
    return [{"action": "climate.set_temperature", "target": target, "data": {"temperature": degrees}}], None


def describe(hass: HomeAssistant, spec: dict[str, Any]) -> str:
    st = hass.states.get(str(spec.get("entity_id") or ""))
    zone = st.name if st else str(spec.get("entity_id"))
    t = spec.get("temperature")
    return f"{zone} off" if t in (None, "", "off") else f"{zone} to {float(t):g}°"
