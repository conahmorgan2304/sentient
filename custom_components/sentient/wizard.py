"""The setup wizard: a few short steps on the sidebar page, filled in from what the house already has.

Each step is a handful of settings. Anything already set is shown as it is; anything not set yet is
suggested from Home Assistant itself — by device type and the room a device is in rather than by its
name, wherever possible. Suggestions are only filled in, never saved, until the person presses Next.
The last step says a test line on the chosen speakers.
"""

from __future__ import annotations

import re
from typing import Any

import voluptuous as vol

from homeassistant.components import websocket_api
from homeassistant.core import HomeAssistant
from homeassistant.helpers import entity_registry as er

from .const import (
    CONF_AI_TASK_ENTITY,
    CONF_ANIMAL_SENSORS,
    CONF_APPLIANCES,
    CONF_BACKUP_AGENT,
    CONF_CHAT_AGENT,
    CONF_DAILY_AI_LIMIT,
    CONF_DEFAULT_SPEAKER,
    CONF_DOOR_CAMERA,
    CONF_DOOR_TRIGGERS,
    CONF_DRIVE_CAMERA,
    CONF_DRIVE_TRIGGERS,
    CONF_ENERGY,
    CONF_INSIDE_SENSORS,
    CONF_MORNING_BRIEFING,
    CONF_PERSONA,
    CONF_PERSONALITY,
    CONF_PERSONALITY_NOTES,
    CONF_QUIET_END,
    CONF_QUIET_START,
    CONF_SLEEP,
    CONF_SPEAKERS,
    CONF_VOICE_AGENT,
    DEFAULT_BACKUP_AGENT,
    DEFAULT_PERSONA,
    DOMAIN,
)

CONF_WIZARD_DONE = "wizard_done"

# (id, title, icon, what it's for, keys)
STEPS: list[tuple[str, str, str, str, list[str]]] = [
    ("name", "Name & personality", "mdi:account-voice",
     "What the house is called and how it talks. Every voice and every remark uses this.",
     [CONF_PERSONA, CONF_PERSONALITY, CONF_PERSONALITY_NOTES]),
    ("speakers", "Speakers", "mdi:speaker-wireless",
     "Where it talks. Voice speakers are ticked for you; add Echos or other speakers if you like. The default is used when it can't tell where people are.",
     [CONF_SPEAKERS, CONF_DEFAULT_SPEAKER]),
    ("rooms", "Rooms & movement", "mdi:motion-sensor",
     "Motion sensors inside the house — how it knows which room people are in, and when the house has gone to bed. Ticked for you from the rooms your speakers are in.",
     [CONF_INSIDE_SENSORS]),
    ("quiet", "Quiet hours", "mdi:weather-night",
     "When to keep it down: nothing but urgent messages between these times.",
     [CONF_QUIET_START, CONF_QUIET_END, CONF_SLEEP]),
    ("ai", "The AI", "mdi:robot-outline",
     "The AI you already have in Home Assistant. Background jobs (cameras, briefings, the nightly think) use an AI task; voices use a conversation agent, with Home Assistant's own as the backup.",
     [CONF_AI_TASK_ENTITY, CONF_VOICE_AGENT, CONF_BACKUP_AGENT, CONF_CHAT_AGENT, CONF_DAILY_AI_LIMIT]),
    ("cameras", "Doorbell & drive", "mdi:doorbell-video",
     "Optional. The doorbell camera and what rings it, for visitors and parcels; a camera that sees the drive, for cars.",
     [CONF_DOOR_CAMERA, CONF_DOOR_TRIGGERS, CONF_DRIVE_CAMERA, CONF_DRIVE_TRIGGERS]),
    ("extras", "Extras", "mdi:star-four-points-outline",
     "Optional. Appliances to follow (finished, or stuck), animals living outside, energy use and a morning briefing.",
     [CONF_APPLIANCES, CONF_ANIMAL_SENSORS, CONF_ENERGY, CONF_MORNING_BRIEFING]),
]

ANIMAL_NAMES = re.compile(r"hutch|kennel|coop|aviary|vivarium|terrarium|tank|stable|pen\b|run\b|kitten|puppy", re.I)
APPLIANCE_NAMES = re.compile(r"wash|dryer|drier|dishwasher|tumble", re.I)
DOOR_NAMES = re.compile(r"door|bell", re.I)
RING_NAMES = re.compile(r"ring|bell|visitor|press|button", re.I)


def _usable(hass: HomeAssistant, entity_id: str) -> bool:
    entry = er.async_get(hass).async_get(entity_id)
    st = hass.states.get(entity_id)
    return st is not None and st.state != "unavailable" and not (entry and (entry.disabled_by or entry.hidden_by))


def _area(hass: HomeAssistant, entity_id: str) -> str | None:
    from .presence import area_of

    return area_of(hass, entity_id)


def _same_device(hass: HomeAssistant, a: str, b: str) -> bool:
    reg = er.async_get(hass)
    ea, eb = reg.async_get(a), reg.async_get(b)
    return bool(ea and eb and ea.device_id and ea.device_id == eb.device_id)


def suggest(hass: HomeAssistant, options: dict[str, Any], own_agent: str | None) -> dict[str, Any]:
    """What to fill in where nothing is set yet."""
    out: dict[str, Any] = {}
    states = hass.states

    sats = [s.entity_id for s in states.async_all("assist_satellite") if _usable(hass, s.entity_id)]
    if sats:
        out[CONF_SPEAKERS] = sats
        out[CONF_DEFAULT_SPEAKER] = sats[0]

    from .presence import inside_sensors

    if found := inside_sensors(hass, {**options, CONF_INSIDE_SENSORS: [], CONF_SPEAKERS: options.get(CONF_SPEAKERS) or sats}):
        out[CONF_INSIDE_SENSORS] = [e for e in found if _usable(hass, e)]

    tasks = [s.entity_id for s in states.async_all("ai_task") if _usable(hass, s.entity_id)]
    if tasks:
        out[CONF_AI_TASK_ENTITY] = next((t for t in tasks if "snapshot" not in t and "frigate" not in t), tasks[0])
    agents = [s.entity_id for s in states.async_all("conversation")
              if s.entity_id not in (DEFAULT_BACKUP_AGENT, own_agent) and _usable(hass, s.entity_id)]
    preferred = _preferred_agent(hass)
    if preferred and preferred in agents:
        out[CONF_VOICE_AGENT] = preferred
    elif agents:
        out[CONF_VOICE_AGENT] = next((a for a in agents if not re.search(r"snapshot|frigate|automation|live", a)), agents[0])
    out[CONF_BACKUP_AGENT] = DEFAULT_BACKUP_AGENT
    if own_agent:
        out[CONF_CHAT_AGENT] = own_agent

    cams = [s for s in states.async_all("camera") if _usable(hass, s.entity_id)]
    door = next((c.entity_id for c in cams if DOOR_NAMES.search(f"{c.entity_id} {c.name}")), None)
    if door:
        out[CONF_DOOR_CAMERA] = door
        rings = [s.entity_id for s in states.async_all(("binary_sensor", "event"))
                 if RING_NAMES.search(f"{s.entity_id} {s.name}") and (_same_device(hass, s.entity_id, door)
                                                                      or DOOR_NAMES.search(s.entity_id))]
        if rings:
            out[CONF_DOOR_TRIGGERS] = rings[:3]
    drive = next((c.entity_id for c in cams if re.search(r"drive", f"{c.entity_id} {c.name}", re.I)), None)
    if drive:
        out[CONF_DRIVE_CAMERA] = drive

    appliances, areas_seen = [], set()
    for s in states.async_all("vacuum"):
        area = _area(hass, s.entity_id)
        if _usable(hass, s.entity_id) and (area is None or area not in areas_seen):
            appliances.append(s.entity_id)  # one robot per room: the same robot often has two integrations
            areas_seen.add(area)
    appliances += [s.entity_id for s in states.async_all("sensor")
                   if s.attributes.get("device_class") == "power" and APPLIANCE_NAMES.search(f"{s.entity_id} {s.name}")]
    if appliances:
        out[CONF_APPLIANCES] = appliances
    animals = [s.entity_id for s in states.async_all("sensor")
               if s.attributes.get("device_class") == "temperature" and ANIMAL_NAMES.search(f"{s.entity_id} {s.name}")]
    if animals:
        out[CONF_ANIMAL_SENSORS] = animals
    return out


def _preferred_agent(hass: HomeAssistant) -> str | None:
    try:
        from homeassistant.components.assist_pipeline import async_get_pipeline

        agent = async_get_pipeline(hass).conversation_engine
        return agent if agent and agent.startswith("conversation.") else None
    except Exception:  # noqa: BLE001 - no pipelines yet
        return None


def needs_setup(options: dict[str, Any]) -> bool:
    return not options.get(CONF_WIZARD_DONE) and not options.get(CONF_SPEAKERS)


def _entry_and_agent(hass: HomeAssistant):
    entries = hass.config_entries.async_loaded_entries(DOMAIN)
    if not entries:
        return None, None
    entry = entries[0]
    own = er.async_get(hass).async_get_entity_id("conversation", DOMAIN, f"{entry.entry_id}_voice")
    return entry, own


@websocket_api.websocket_command({vol.Required("type"): "sentient/wizard", vol.Optional("language"): str})
@websocket_api.require_admin
@websocket_api.async_response
async def ws_wizard(hass: HomeAssistant, connection, msg: dict[str, Any]) -> None:
    from .websocket import _all_fields, _current, _labels, _serialize, _with_defaults

    entry, own = _entry_and_agent(hass)
    if entry is None:
        connection.send_error(msg["id"], "not_loaded", "Sentient is not set up")
        return
    current = _current(entry)
    data = _with_defaults(current)
    suggested = []
    for key, value in suggest(hass, current, own).items():
        if current.get(key) in (None, "", []):  # never over anything already chosen
            data[key] = value
            suggested.append(key)
    fields = _all_fields()
    labels, helpers, selectors = await _labels(hass, msg.get("language") or hass.config.language)
    steps = [
        {"id": sid, "title": title, "icon": icon, "blurb": blurb,
         "schema": _serialize(vol.Schema({fields[k][0]: fields[k][1] for k in keys if k in fields}))}
        for sid, title, icon, blurb, keys in STEPS
    ]
    connection.send_result(msg["id"], {
        "show": needs_setup(current), "done": bool(current.get(CONF_WIZARD_DONE)), "steps": steps, "data": data,
        "suggested": suggested, "labels": labels, "helpers": helpers, "selector": selectors,
        "persona": current.get(CONF_PERSONA) or DEFAULT_PERSONA, "own_agent": own,
    })


@websocket_api.websocket_command({vol.Required("type"): "sentient/wizard/save", vol.Required("step"): str,
                                  vol.Required("data"): dict})
@websocket_api.require_admin
@websocket_api.async_response
async def ws_wizard_save(hass: HomeAssistant, connection, msg: dict[str, Any]) -> None:
    from .websocket import _all_fields, _current, _with_defaults

    entry, _own = _entry_and_agent(hass)
    keys = next((s[4] for s in STEPS if s[0] == msg["step"]), None)
    if entry is None or keys is None:
        connection.send_error(msg["id"], "unknown", "Sentient isn't set up, or there's no such step")
        return
    fields = _all_fields()
    schema = vol.Schema({fields[k][0]: fields[k][1] for k in keys if k in fields})
    submitted = {k: v for k, v in msg["data"].items() if k in keys and v is not None and v != ""}
    try:
        clean = schema(submitted)
    except vol.Invalid as err:
        connection.send_error(msg["id"], "invalid", str(err))
        return
    options = _current(entry)
    for key in keys:  # only this step's settings change
        options.pop(key, None)
    options.update(clean)
    if CONF_PERSONA in clean:
        options[CONF_PERSONA] = str(clean[CONF_PERSONA]).strip() or DEFAULT_PERSONA
    hass.config_entries.async_update_entry(entry, options=options)
    connection.send_result(msg["id"], {"saved": msg["step"], "data": _with_defaults(options)})


@websocket_api.websocket_command({vol.Required("type"): "sentient/wizard/finish", vol.Optional("test"): bool})
@websocket_api.require_admin
@websocket_api.async_response
async def ws_wizard_finish(hass: HomeAssistant, connection, msg: dict[str, Any]) -> None:
    from .websocket import _current

    entry, _own = _entry_and_agent(hass)
    if entry is None:
        connection.send_error(msg["id"], "not_loaded", "Sentient is not set up")
        return
    if msg.get("test"):
        persona = _current(entry).get(CONF_PERSONA) or DEFAULT_PERSONA
        result = await entry.runtime_data.speech.async_say(
            f"Hello, I'm {persona}. This is how I'll sound in this room.", "urgent", None, source="settings"
        )
        connection.send_result(msg["id"], result.as_dict())
        return
    options = {**_current(entry), CONF_WIZARD_DONE: True}
    hass.config_entries.async_update_entry(entry, options=options)
    entry.runtime_data.timeline.add("mode", "Set up with the wizard", None)
    connection.send_result(msg["id"], {"done": True})


def async_register(hass: HomeAssistant) -> None:
    websocket_api.async_register_command(hass, ws_wizard)
    websocket_api.async_register_command(hass, ws_wizard_save)
    websocket_api.async_register_command(hass, ws_wizard_finish)
