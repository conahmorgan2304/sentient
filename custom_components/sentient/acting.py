"""Carrying out a few device actions safely, for watches that act and plans.

The same checks as rules, plus one more: every device must be exposed to voice assistants (so Sentient only
touches what the house already lets the voice control). Steps are built from what the voice said
("turn off" + "hall light"), checked, carried out as ordinary Home Assistant actions, and then each one is read
back from the device itself so the answer can say what didn't happen.

Never: alarms, locks, scripts, scenes, templates, device actions, or anything on the never-touch list.
"""
from __future__ import annotations

from typing import Any

from homeassistant.core import HomeAssistant

MAX_STEPS = 12
STEP_KEYS = ("brightness_pct", "temperature")


def exposed(hass: HomeAssistant, entity_id: str) -> bool:
    try:
        from homeassistant.components.homeassistant.exposed_entities import async_should_expose
    except ImportError:  # pragma: no cover - very old Home Assistant
        return True
    return bool(async_should_expose(hass, "conversation", entity_id))


def build(hass: HomeAssistant, steps: list[dict[str, Any]]) -> tuple[list[dict[str, Any]], str | None]:
    """Spoken steps -> Home Assistant actions, or the reason they can't be used (for the voice to say)."""
    from .never import blocked
    from .rules import safe_actions
    from .watches import resolve_entity

    if not steps:
        return [], "say what to do"
    if len(steps) > MAX_STEPS:
        return [], f"that's more than {MAX_STEPS} steps; split it up"
    actions: list[dict[str, Any]] = []
    for step in steps:
        service = str(step.get("action") or "").strip()
        if "." not in service:
            return [], f"'{service}' isn't an action like light.turn_off"
        domain = service.split(".", 1)[0]
        target = resolve_entity(hass, str(step.get("target") or ""), domain) or resolve_entity(hass, str(step.get("target") or ""))
        if target is None:
            return [], f"I can't find '{step.get('target')}' — ask which device they mean"
        if not exposed(hass, target):
            name = getattr(hass.states.get(target), "name", target)
            return [], f"{name} isn't one the voice is allowed to control"
        if blocked(hass, target):
            name = getattr(hass.states.get(target), "name", target)
            return [], f"{name} is on the never-touch list"
        data = {k: step[k] for k in STEP_KEYS if step.get(k) is not None}
        actions.append({"action": service, "target": {"entity_id": target}, **({"data": data} if data else {})})
    if why := safe_actions(actions):
        return [], why
    return actions, None


_OPENING = {"door", "window", "garage_door", "opening", "gate"}


def thing(hass: HomeAssistant, entity_ref: str) -> str:
    """'the back door' for whatever was named (an entity id or the words used)."""
    from .watches import resolve_entity

    entity_id = entity_ref if "." in entity_ref and hass.states.get(entity_ref) else resolve_entity(hass, entity_ref)
    state = hass.states.get(entity_id) if entity_id else None
    return f"the {(state.name if state is not None else entity_ref).lower()}"


def state_phrase(hass: HomeAssistant, entity_ref: str, value: str) -> str:
    """'open' rather than 'on' for a door; otherwise the state as said."""
    from .watches import resolve_entity

    entity_id = entity_ref if "." in entity_ref and hass.states.get(entity_ref) else resolve_entity(hass, entity_ref)
    state = hass.states.get(entity_id) if entity_id else None
    if state is not None and state.attributes.get("device_class") in _OPENING:
        return {"on": "open", "off": "closed"}.get(value, value)
    return value


def words(hass: HomeAssistant, actions: list[dict[str, Any]], past: bool = False) -> str:
    """'turn off the hall light and turn on the kitchen light at 30%' (or 'turned off …' when past)."""
    parts = []
    for a in actions:
        service = str(a.get("action") or "")
        verb = service.split(".", 1)[-1].replace("_", " ")
        if past:
            verb = {"turn on": "turned on", "turn off": "turned off", "toggle": "toggled", "start": "started",
                    "stop": "stopped", "return to base": "sent back", "set temperature": "set",
                    "open cover": "opened", "close cover": "closed", "media pause": "paused",
                    "select option": "set", "set value": "set", "press": "pressed"}.get(verb, verb)
        else:
            verb = {"set temperature": "set", "open cover": "open", "close cover": "close", "return to base": "send back",
                    "media pause": "pause", "select option": "set", "set value": "set"}.get(verb, verb)
        target = (a.get("target") or {}).get("entity_id")
        state = hass.states.get(target) if isinstance(target, str) else None
        name = (state.name if state is not None else str(target)).lower()
        data = a.get("data") or {}
        extra = f" at {data['brightness_pct']}%" if "brightness_pct" in data else (
            f" to {data['temperature']}°" if "temperature" in data else "")
        if verb == "send back":
            parts.append(f"send the {name} back{extra}")
        else:
            parts.append(f"{verb} the {name}{extra}")
    if not parts:
        return ""
    return parts[0] if len(parts) == 1 else ", ".join(parts[:-1]) + " and " + parts[-1]


async def async_carry_out(hass: HomeAssistant, actions: list[dict[str, Any]], name: str,
                          verify_seconds: float | None = None) -> dict[str, Any]:
    """Run the actions, then check each device. Returns {'said': sentence, 'not_done': [...], 'done': [...]}."""
    from .never import touches
    from .plans import async_verify, describe, plan
    from .rules import safe_actions
    from .shortcuts import async_run_actions

    if why := safe_actions(actions):
        return {"said": f"I didn't do it: {why}.", "not_done": actions, "done": [], "error": why}
    if touches(hass, actions):
        return {"said": "I didn't do it: one of those devices is on the never-touch list.", "not_done": actions,
                "done": [], "error": "never-touch"}
    for a in actions:  # exposure can change between being asked and being done
        target = (a.get("target") or {}).get("entity_id")
        if isinstance(target, str) and not exposed(hass, target):
            return {"said": "I didn't do it: one of those devices is no longer one the voice can control.",
                    "not_done": actions, "done": [], "error": "not exposed"}
    planned = plan(hass, actions)
    try:
        await async_run_actions(hass, actions, name)
    except Exception as err:  # noqa: BLE001 - a failed device is reported, not raised
        return {"said": f"I tried to {words(hass, actions)}, but it didn't work.", "not_done": actions, "done": [],
                "error": type(err).__name__}
    outcome = await async_verify(hass, planned, verify_seconds)
    missed = describe(hass, outcome)
    if missed:
        said = missed
    else:
        said = f"I've {words(hass, actions, past=True)}."
    return {"said": said, "not_done": outcome.get("not_done") or [], "done": outcome.get("done") or []}
