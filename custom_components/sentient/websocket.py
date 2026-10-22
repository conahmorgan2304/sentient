"""Websocket commands behind the sidebar page's Settings tab.

The page draws Home Assistant's own form (the same pickers as the options
flow) from the schema sent here, so every setting has one definition: the
schemas in config_flow.py.
"""

from __future__ import annotations

from typing import Any

import voluptuous as vol

from homeassistant.components import websocket_api
from homeassistant.core import HomeAssistant, callback
from homeassistant.exceptions import HomeAssistantError
from homeassistant.helpers import config_validation as cv
from homeassistant.helpers import selector
from homeassistant.helpers.translation import async_get_translations

from .config_flow import _briefing_schema, _speech_schema
from .const import (
    CONF_AI_TASK_ENTITY,
    CONF_SECOND_OPINION,
    CONF_NOTIFY_TARGETS,
    CONF_CHIME,
    CONF_DIGEST_TIME,
    CONF_WELCOME,
    CONF_SPEAK_VOLUME,
    CONF_NIGHT_VOLUME,
    CONF_DUCK_MEDIA,
    CONF_CRITICAL,
    CONF_ENERGY,
    CONF_ENERGY_PRICE,
    CONF_CRITICAL_IGNORE,
    CONF_WELCOME_PEOPLE,
    CONF_NOTIFY_WHEN,
    CONF_ALL_OCCUPIED,
    CONF_ANNOUNCE_VISITORS,
    CONF_BANNED_WORDS,
    CONF_BATTERY_THRESHOLD,
    CONF_BRIEFING_CALENDARS,
    CONF_BRIEFING_SECTIONS,
    CONF_BRIEFING_SYSTEM,
    CONF_BRIEFING_TIME,
    CONF_BRIEFING_TODO,
    CONF_BRIEFING_WEATHER,
    CONF_BRIEFINGS,
    CONF_CAMERA_COOLDOWN,
    CONF_CHAT_AGENT,
    CONF_CHATTINESS,
    CONF_DAILY_AI_LIMIT,
    CONF_DEFAULT_SPEAKER,
    CONF_DOOR_CAMERA,
    CONF_DOOR_TRIGGERS,
    CONF_FACE_SENSOR,
    CONF_FALLBACK_SPEAKERS,
    CONF_HEALTH_IGNORE,
    CONF_HOUSE_RULES,
    CONF_LEARN_ENTITIES,
    CONF_MOTION_WINDOW,
    CONF_NOTICE_ENTITIES,
    CONF_PARCEL_ALERTS,
    CONF_PASS_THROUGH_AREAS,
    CONF_PERSONA,
    CONF_PROMPT_MEMORIES,
    CONF_QUIET_END,
    CONF_QUIET_START,
    CONF_SIDEBAR,
    CONF_SPEAKERS,
    CONF_SUGGEST_ROUTINES,
    CONF_SUPPRESS_ENTITIES,
    CONF_SUPPRESS_SECONDS,
    DEFAULT_PERSONA,
    DOMAIN,
    MEMORY_CATEGORIES,
)

# (id, title, icon, blurb, keys) — the order the Settings tab shows them in
GROUPS: list[tuple[str, str, str, str, list[str]]] = [
    ("voice", "Voice", "mdi:account-voice", "Name, speakers and how it talks.", [
        CONF_PERSONA, CONF_SPEAKERS, CONF_DEFAULT_SPEAKER, CONF_FALLBACK_SPEAKERS, CONF_CHIME, CONF_SPEAK_VOLUME,
        CONF_NIGHT_VOLUME, CONF_DUCK_MEDIA, CONF_ALL_OCCUPIED,
        CONF_CHATTINESS, CONF_BANNED_WORDS, CONF_HOUSE_RULES,
    ]),
    ("quiet", "Quiet & rooms", "mdi:weather-night", "When to keep it down and how rooms are worked out.", [
        CONF_QUIET_START, CONF_QUIET_END, CONF_MOTION_WINDOW, CONF_PASS_THROUGH_AREAS,
        CONF_SUPPRESS_ENTITIES, CONF_SUPPRESS_SECONDS,
    ]),
    ("briefing", "Briefings", "mdi:text-box-outline", "What a briefing covers and when.", [
        CONF_BRIEFINGS, CONF_BRIEFING_SECTIONS, CONF_BRIEFING_WEATHER, CONF_BRIEFING_TODO,
        CONF_BRIEFING_CALENDARS, CONF_BRIEFING_SYSTEM, CONF_BRIEFING_TIME, CONF_DIGEST_TIME,
    ]),
    ("noticing", "Noticing & learning", "mdi:radar", "What it watches for and learns from.", [
        CONF_NOTICE_ENTITIES, CONF_LEARN_ENTITIES, CONF_SUGGEST_ROUTINES, CONF_WELCOME, CONF_WELCOME_PEOPLE,
    ]),
    ("door", "Door & cameras", "mdi:doorbell-video", "Doorbell log, parcels, faces and camera checks.", [
        CONF_DOOR_CAMERA, CONF_DOOR_TRIGGERS, CONF_FACE_SENSOR, CONF_ANNOUNCE_VISITORS, CONF_PARCEL_ALERTS,
        CONF_AI_TASK_ENTITY, CONF_SECOND_OPINION, CONF_CAMERA_COOLDOWN, CONF_DAILY_AI_LIMIT,
    ]),
    ("phone", "Phone notifications", "mdi:cellphone-message", "What also goes to people's phones.", [
        CONF_NOTIFY_TARGETS, CONF_NOTIFY_WHEN,
    ]),
    ("safety", "Safety alerts", "mdi:fire-alert", "Smoke, CO, gas and leaks — always said everywhere.", [
        CONF_CRITICAL, CONF_CRITICAL_IGNORE,
    ]),
    ("energy", "Energy", "mdi:lightning-bolt", "Electricity use from the Energy dashboard's devices.", [
        CONF_ENERGY, CONF_ENERGY_PRICE,
    ]),
    ("health", "House health", "mdi:home-heart", "What counts as a problem.", [
        CONF_BATTERY_THRESHOLD, CONF_HEALTH_IGNORE,
    ]),
    ("page", "This page", "mdi:monitor-dashboard", "The sidebar page and its Ask box.", [
        CONF_SIDEBAR, CONF_CHAT_AGENT, CONF_PROMPT_MEMORIES,
    ]),
]


def _all_fields() -> dict[str, tuple[Any, Any]]:
    """Every setting: key -> (voluptuous marker, selector), defaults not tied to current values."""
    fields: dict[str, tuple[Any, Any]] = {
        CONF_PERSONA: (vol.Required(CONF_PERSONA, default=DEFAULT_PERSONA), selector.TextSelector())
    }
    for schema in (_speech_schema({}), _briefing_schema({})):
        for marker, value in schema.schema.items():
            fields[str(marker)] = (marker, value)
    return fields


def group_schema(group_id: str) -> vol.Schema:
    keys = next(g[4] for g in GROUPS if g[0] == group_id)
    fields = _all_fields()
    return vol.Schema({fields[k][0]: fields[k][1] for k in keys if k in fields})


def _serialize(schema: vol.Schema) -> list[dict[str, Any]]:
    """The schema as the frontend's form wants it (selectors only, so no extra library needed)."""
    out = []
    for marker, value in schema.schema.items():
        field: dict[str, Any] = {"name": str(marker), "required": isinstance(marker, vol.Required)}
        if marker.default is not vol.UNDEFINED:
            field["default"] = marker.default()
        field.update(cv.custom_serializer(value))
        out.append(field)
    return out


def _current(entry) -> dict[str, Any]:
    return {**entry.data, **entry.options}


def _entry(hass: HomeAssistant):
    entries = hass.config_entries.async_loaded_entries(DOMAIN)
    return entries[0] if entries else None


def _with_defaults(current: dict[str, Any]) -> dict[str, Any]:
    data: dict[str, Any] = {}
    for key, (marker, _sel) in _all_fields().items():
        if key in current and current[key] is not None:
            data[key] = current[key]
        elif marker.default is not vol.UNDEFINED:
            data[key] = marker.default()
    return data


@callback
def async_register(hass: HomeAssistant) -> None:
    websocket_api.async_register_command(hass, ws_get_settings)
    websocket_api.async_register_command(hass, ws_save_settings)
    websocket_api.async_register_command(hass, ws_update_memory)
    websocket_api.async_register_command(hass, ws_list_memories)
    websocket_api.async_register_command(hass, ws_list_spots)
    websocket_api.async_register_command(hass, ws_learn_spot)
    websocket_api.async_register_command(hass, ws_set_spot)
    websocket_api.async_register_command(hass, ws_forget_spot)
    websocket_api.async_register_command(hass, ws_look)
    websocket_api.async_register_command(hass, ws_motion_list)
    websocket_api.async_register_command(hass, ws_timeline)
    websocket_api.async_register_command(hass, ws_digest)
    websocket_api.async_register_command(hass, ws_mutes)
    websocket_api.async_register_command(hass, ws_shortcuts)
    websocket_api.async_register_command(hass, ws_shortcut_save)
    websocket_api.async_register_command(hass, ws_shortcut_delete)
    websocket_api.async_register_command(hass, ws_export)
    websocket_api.async_register_command(hass, ws_energy)
    websocket_api.async_register_command(hass, ws_import)
    websocket_api.async_register_command(hass, ws_motion_save)
    websocket_api.async_register_command(hass, ws_motion_delete)
    websocket_api.async_register_command(hass, ws_motion_night)


@websocket_api.websocket_command({vol.Required("type"): "sentient/settings", vol.Optional("language"): str})
@websocket_api.require_admin
@websocket_api.async_response
async def ws_get_settings(hass: HomeAssistant, connection, msg: dict[str, Any]) -> None:
    entry = _entry(hass)
    if entry is None:
        connection.send_error(msg["id"], "not_loaded", "Sentient is not set up")
        return
    lang = msg.get("language") or hass.config.language
    options = await async_get_translations(hass, lang, "options", {DOMAIN})
    selectors = await async_get_translations(hass, lang, "selector", {DOMAIN})
    prefix = f"component.{DOMAIN}.options.step."
    labels: dict[str, str] = {}
    helpers: dict[str, str] = {}
    for step in ("settings", "briefing"):
        for key in _all_fields():
            if (label := options.get(f"{prefix}{step}.data.{key}")) and key not in labels:
                labels[key] = label
            if (helper := options.get(f"{prefix}{step}.data_description.{key}")) and key not in helpers:
                helpers[key] = helper
    groups = [
        {
            "id": gid, "title": title, "icon": icon, "blurb": blurb,
            "schema": _serialize(group_schema(gid)),
        }
        for gid, title, icon, blurb, _keys in GROUPS
    ]
    connection.send_result(
        msg["id"],
        {
            "groups": groups,
            "data": _with_defaults(_current(entry)),
            "labels": labels,
            "helpers": helpers,
            # "chattiness.options.quiet" -> "Quiet", for the form's dropdowns
            "selector": {k.removeprefix(f"component.{DOMAIN}.selector."): v for k, v in selectors.items()},
            "categories": MEMORY_CATEGORIES,
        },
    )


@websocket_api.websocket_command(
    {vol.Required("type"): "sentient/settings/save", vol.Required("group"): str, vol.Required("data"): dict}
)
@websocket_api.require_admin
@websocket_api.async_response
async def ws_save_settings(hass: HomeAssistant, connection, msg: dict[str, Any]) -> None:
    entry = _entry(hass)
    if entry is None:
        connection.send_error(msg["id"], "not_loaded", "Sentient is not set up")
        return
    if msg["group"] not in {g[0] for g in GROUPS}:
        connection.send_error(msg["id"], "unknown_group", "No such settings group")
        return
    schema = group_schema(msg["group"])
    submitted = {k: v for k, v in msg["data"].items() if v is not None and v != ""}
    try:
        clean = schema(submitted)
    except vol.Invalid as err:
        connection.send_error(msg["id"], "invalid", str(err))
        return
    if CONF_PERSONA in clean:
        clean[CONF_PERSONA] = str(clean[CONF_PERSONA]).strip() or DEFAULT_PERSONA
    options = _current(entry)
    for marker in schema.schema:
        options.pop(str(marker), None)
    options.update(clean)
    hass.config_entries.async_update_entry(entry, options=options)
    connection.send_result(msg["id"], {"saved": msg["group"], "data": _with_defaults(options)})


@websocket_api.websocket_command(
    {
        vol.Required("type"): "sentient/memory/update",
        vol.Required("memory_id"): str,
        vol.Required("text"): str,
        vol.Optional("category"): vol.In(MEMORY_CATEGORIES),
    }
)
@websocket_api.require_admin
@callback
def ws_update_memory(hass: HomeAssistant, connection, msg: dict[str, Any]) -> None:
    entry = _entry(hass)
    if entry is None:
        connection.send_error(msg["id"], "not_loaded", "Sentient is not set up")
        return
    text = msg["text"].strip()
    if not text:
        connection.send_error(msg["id"], "invalid", "A memory can't be empty")
        return
    item = entry.runtime_data.memory.update(msg["memory_id"], text, msg.get("category"))
    if item is None:
        connection.send_error(msg["id"], "not_found", "No such memory")
        return
    connection.send_result(msg["id"], {"id": item.id, "text": item.text, "category": item.category})


@websocket_api.websocket_command({vol.Required("type"): "sentient/memories"})
@callback
def ws_list_memories(hass: HomeAssistant, connection, msg: dict[str, Any]) -> None:
    """Everything remembered, newest first (the sensor only carries the latest 50)."""
    entry = _entry(hass)
    if entry is None:
        connection.send_error(msg["id"], "not_loaded", "Sentient is not set up")
        return
    items = sorted(entry.runtime_data.memory.items, key=lambda m: m.created, reverse=True)
    connection.send_result(
        msg["id"],
        {
            "items": [m.as_dict() for m in items],
            "categories": MEMORY_CATEGORIES,
            "admin": connection.user.is_admin,
        },
    )


# ----- camera spots --------------------------------------------------------------
def _spots_payload(entry) -> dict[str, Any]:
    spots = entry.runtime_data.spots
    return {"spots": [dict(s) for s in spots.spots.values()]}


@websocket_api.websocket_command({vol.Required("type"): "sentient/spots"})
@callback
def ws_list_spots(hass: HomeAssistant, connection, msg: dict[str, Any]) -> None:
    entry = _entry(hass)
    if entry is None:
        connection.send_error(msg["id"], "not_loaded", "Sentient is not set up")
        return
    connection.send_result(msg["id"], _spots_payload(entry))


async def _spot_call(hass: HomeAssistant, connection, msg: dict[str, Any], job) -> None:
    entry = _entry(hass)
    if entry is None:
        connection.send_error(msg["id"], "not_loaded", "Sentient is not set up")
        return
    try:
        result = await job(entry.runtime_data)
    except HomeAssistantError as err:
        connection.send_error(msg["id"], "failed", str(err))
        return
    connection.send_result(msg["id"], {**_spots_payload(entry), "result": result})


@websocket_api.websocket_command(
    {vol.Required("type"): "sentient/spots/learn", vol.Required("camera"): cv.entity_id, vol.Required("name"): str}
)
@websocket_api.require_admin
@websocket_api.async_response
async def ws_learn_spot(hass: HomeAssistant, connection, msg: dict[str, Any]) -> None:
    await _spot_call(hass, connection, msg, lambda rt: rt.spots.async_learn(msg["camera"], msg["name"]))


@websocket_api.websocket_command(
    {
        vol.Required("type"): "sentient/spots/set",
        vol.Required("camera"): cv.entity_id,
        vol.Required("name"): str,
        vol.Required("box"): vol.All([vol.Coerce(float)], vol.Length(min=4, max=4)),
    }
)
@websocket_api.require_admin
@websocket_api.async_response
async def ws_set_spot(hass: HomeAssistant, connection, msg: dict[str, Any]) -> None:
    async def job(rt):
        return rt.spots.set(msg["camera"], msg["name"], msg["box"])

    await _spot_call(hass, connection, msg, job)


@websocket_api.websocket_command({vol.Required("type"): "sentient/spots/forget", vol.Required("name"): str})
@websocket_api.require_admin
@websocket_api.async_response
async def ws_forget_spot(hass: HomeAssistant, connection, msg: dict[str, Any]) -> None:
    async def job(rt):
        return rt.spots.forget(msg["name"])

    await _spot_call(hass, connection, msg, job)


@websocket_api.websocket_command(
    {
        vol.Required("type"): "sentient/look",
        vol.Required("camera"): cv.entity_id,
        vol.Required("question"): str,
        vol.Optional("spot"): str,
    }
)
@websocket_api.async_response
async def ws_look(hass: HomeAssistant, connection, msg: dict[str, Any]) -> None:
    """Ask the AI about a camera (or one spot of it) right now."""
    await _spot_call(
        hass, connection, msg, lambda rt: rt.watches.async_look(msg["camera"], msg["question"], msg.get("spot"))
    )


# ----- motion lights ---------------------------------------------------------------
def _motion(hass: HomeAssistant, connection, msg: dict[str, Any]):
    entry = _entry(hass)
    if entry is None:
        connection.send_error(msg["id"], "not_loaded", "Sentient is not set up")
        return None
    return entry.runtime_data.motion_lights


@websocket_api.websocket_command({vol.Required("type"): "sentient/motion_lights"})
@callback
def ws_motion_list(hass: HomeAssistant, connection, msg: dict[str, Any]) -> None:
    if (ml := _motion(hass, connection, msg)) is not None:
        connection.send_result(msg["id"], ml.listing())


@websocket_api.websocket_command(
    {
        vol.Required("type"): "sentient/motion_lights/save",
        vol.Required("rule"): {
            vol.Optional("id"): str,
            vol.Required("light"): cv.entity_id,
            vol.Required("motion"): vol.All(cv.ensure_list, [cv.entity_id]),
            vol.Optional("enabled"): bool,
            vol.Optional("respect_quiet"): bool,
            vol.Optional("only_dark"): bool,
            vol.Optional("off_after"): vol.Coerce(float),
            vol.Optional("brightness"): vol.Coerce(int),
            vol.Optional("night_brightness"): vol.Coerce(int),
            vol.Optional("lux_sensor"): vol.Any(None, cv.entity_id),
            vol.Optional("lux_below"): vol.Coerce(float),
        },
    }
)
@websocket_api.require_admin
@callback
def ws_motion_save(hass: HomeAssistant, connection, msg: dict[str, Any]) -> None:
    if (ml := _motion(hass, connection, msg)) is None:
        return
    try:
        ml.upsert(dict(msg["rule"]))
    except ValueError as err:
        connection.send_error(msg["id"], "invalid", str(err))
        return
    connection.send_result(msg["id"], ml.listing())


@websocket_api.websocket_command({vol.Required("type"): "sentient/motion_lights/delete", vol.Required("rule_id"): str})
@websocket_api.require_admin
@callback
def ws_motion_delete(hass: HomeAssistant, connection, msg: dict[str, Any]) -> None:
    if (ml := _motion(hass, connection, msg)) is None:
        return
    ml.remove(msg["rule_id"])
    connection.send_result(msg["id"], ml.listing())


@websocket_api.websocket_command(
    {vol.Required("type"): "sentient/motion_lights/night", vol.Required("start"): str, vol.Required("end"): str}
)
@websocket_api.require_admin
@callback
def ws_motion_night(hass: HomeAssistant, connection, msg: dict[str, Any]) -> None:
    if (ml := _motion(hass, connection, msg)) is None:
        return
    ml.set_night(msg["start"], msg["end"])
    connection.send_result(msg["id"], ml.listing())


@websocket_api.websocket_command(
    {vol.Required("type"): "sentient/timeline", vol.Optional("hours", default=48): vol.Coerce(float),
     vol.Optional("kinds"): [str], vol.Optional("limit", default=300): int}
)
@callback
def ws_timeline(hass: HomeAssistant, connection, msg: dict[str, Any]) -> None:
    entry = _entry(hass)
    if entry is None:
        connection.send_error(msg["id"], "not_loaded", "Sentient is not set up")
        return
    items = entry.runtime_data.timeline.since(msg["hours"], msg.get("kinds"), msg["limit"])
    connection.send_result(msg["id"], {"items": items})


@websocket_api.websocket_command({vol.Required("type"): "sentient/digest", vol.Optional("hours", default=24): vol.Coerce(float)})
@websocket_api.async_response
async def ws_digest(hass: HomeAssistant, connection, msg: dict[str, Any]) -> None:
    entry = _entry(hass)
    if entry is None:
        connection.send_error(msg["id"], "not_loaded", "Sentient is not set up")
        return
    try:
        result = await entry.runtime_data.upkeep.async_digest(msg["hours"])
    except HomeAssistantError as err:
        connection.send_error(msg["id"], "failed", str(err))
        return
    connection.send_result(msg["id"], result)


@websocket_api.websocket_command({vol.Required("type"): "sentient/mutes", vol.Optional("remove"): str, vol.Optional("clear"): bool})
@callback
def ws_mutes(hass: HomeAssistant, connection, msg: dict[str, Any]) -> None:
    entry = _entry(hass)
    if entry is None:
        connection.send_error(msg["id"], "not_loaded", "Sentient is not set up")
        return
    mutes = entry.runtime_data.mutes
    if msg.get("clear"):
        mutes.remove(None)
    elif msg.get("remove"):
        mutes.remove(msg["remove"])
    connection.send_result(msg["id"], {"mutes": mutes.listing()})


# ----- named routines -------------------------------------------------------------
@websocket_api.websocket_command({vol.Required("type"): "sentient/shortcuts"})
@callback
def ws_shortcuts(hass: HomeAssistant, connection, msg: dict[str, Any]) -> None:
    entry = _entry(hass)
    if entry is None:
        connection.send_error(msg["id"], "not_loaded", "Sentient is not set up")
        return
    connection.send_result(msg["id"], {"items": entry.runtime_data.shortcuts.listing()})


@websocket_api.websocket_command({vol.Required("type"): "sentient/shortcuts/save", vol.Required("item"): dict})
@websocket_api.require_admin
@websocket_api.async_response
async def ws_shortcut_save(hass: HomeAssistant, connection, msg: dict[str, Any]) -> None:
    entry = _entry(hass)
    if entry is None:
        connection.send_error(msg["id"], "not_loaded", "Sentient is not set up")
        return
    item = dict(msg["item"])
    try:
        if item.get("actions"):
            from homeassistant.helpers.script import async_validate_actions_config

            await async_validate_actions_config(hass, cv.SCRIPT_SCHEMA(item["actions"]))
        entry.runtime_data.shortcuts.upsert(item)
    except (vol.Invalid, ValueError, HomeAssistantError) as err:
        connection.send_error(msg["id"], "invalid", str(err))
        return
    connection.send_result(msg["id"], {"items": entry.runtime_data.shortcuts.listing()})


@websocket_api.websocket_command({vol.Required("type"): "sentient/shortcuts/delete", vol.Required("item_id"): str})
@websocket_api.require_admin
@callback
def ws_shortcut_delete(hass: HomeAssistant, connection, msg: dict[str, Any]) -> None:
    entry = _entry(hass)
    if entry is None:
        connection.send_error(msg["id"], "not_loaded", "Sentient is not set up")
        return
    entry.runtime_data.shortcuts.remove(msg["item_id"])
    connection.send_result(msg["id"], {"items": entry.runtime_data.shortcuts.listing()})


# ----- backup ------------------------------------------------------------------------
@websocket_api.websocket_command({vol.Required("type"): "sentient/export"})
@websocket_api.require_admin
@callback
def ws_export(hass: HomeAssistant, connection, msg: dict[str, Any]) -> None:
    entry = _entry(hass)
    if entry is None:
        connection.send_error(msg["id"], "not_loaded", "Sentient is not set up")
        return
    from .backup import export_all

    connection.send_result(msg["id"], export_all(hass, entry))


@websocket_api.websocket_command(
    {vol.Required("type"): "sentient/import", vol.Required("data"): dict, vol.Optional("settings", default=False): bool}
)
@websocket_api.require_admin
@callback
def ws_import(hass: HomeAssistant, connection, msg: dict[str, Any]) -> None:
    entry = _entry(hass)
    if entry is None:
        connection.send_error(msg["id"], "not_loaded", "Sentient is not set up")
        return
    from .backup import import_all

    try:
        result = import_all(hass, entry, msg["data"], msg["settings"])
    except (KeyError, TypeError, ValueError) as err:
        connection.send_error(msg["id"], "invalid", f"That doesn't look like a Sentient backup ({err})")
        return
    connection.send_result(msg["id"], result)


@websocket_api.websocket_command({vol.Required("type"): "sentient/energy"})
@websocket_api.async_response
async def ws_energy(hass: HomeAssistant, connection, msg: dict[str, Any]) -> None:
    from .energy import async_report

    try:
        connection.send_result(msg["id"], await async_report(hass))
    except Exception as err:  # noqa: BLE001
        connection.send_error(msg["id"], "failed", str(err))
