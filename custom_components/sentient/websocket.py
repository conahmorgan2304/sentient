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
    CONF_COMFORT_TEMP,
    CONF_CAMSEC,
    CONF_CAMSEC_DESCRIBE,
    CONF_CAMSEC_ENTRIES,
    CONF_CAMSEC_INSIDE,
    CONF_CAMSEC_SENSORS,
    CONF_PERSONALITY,
    CONF_PERSONALITY_NOTES,
    CONF_WARM_BY_WEEKDAYS,
    CONF_WARM_BY_WEEKENDS,
    CONF_HEATING_ENTITIES,
    CONF_HEATING_LIMIT,
    CONF_PARTY_EXCLUDE,
    CONF_PARTY_LIGHTS,
    CONF_PARTY_MINUTES,
    CONF_PARTY_SHOW,
    CONF_PARTY_SOUND,
    CONF_PARTY_SOUND_SWITCH,
    CONF_PARTY_STYLE,
    CONF_PARTY_WHOLE_HOUSE,
    CONF_INSIDE_SENSORS,
    CONF_NIGHT_WATCH,
    CONF_NIGHT_WATCH_ANNOUNCE,
    CONF_NIGHT_WATCH_ENTITIES,
    CONF_NIGHT_WATCH_SPEAKERS,
    CONF_PREHEAT,
    CONF_WEEKLY_REPORT,
    CONF_AI_TASK_ENTITY,
    CONF_SECOND_OPINION,
    CONF_NOTIFY_TARGETS,
    CONF_CHIME,
    CONF_DIGEST_TIME,
    CONF_MORNING_BRIEFING,
    CONF_WELCOME,
    CONF_SPEAK_VOLUME,
    CONF_NIGHT_VOLUME,
    CONF_DUCK_MEDIA,
    CONF_ROOM_VOLUME,
    CONF_FULL_VOLUME_SPEAKERS,
    CONF_TTS_ENTITY,
    CONF_MEDIA_SPEAKERS,
    CONF_SLEEP,
    CONF_SLEEP_MINUTES,
    CONF_BEDTIME_ROUTINES,
    CONF_SLEEP_ACTIONS,
    CONF_WAKE_ACTIONS,
    CONF_FORESIGHT,
    CONF_FORESIGHT_TIME,
    CONF_FOLLOW_YOU,
    CONF_FOLLOW_MINUTES,
    CONF_DRIVE_CAMERA,
    CONF_DRIVE_SPOT,
    CONF_DRIVE_TRIGGERS,
    CONF_CONVERSATIONS,
    CONF_FOLLOW_UPS,
    CONF_CURIOSITY,
    CONF_REFLECTION,
    CONF_REFLECTION_TIME,
    CONF_CRITICAL,
    CONF_ENERGY,
    CONF_LEFT_OPEN_MINUTES,
    CONF_LEFT_OPEN_IGNORE,
    CONF_LEAVING_CHECK,
    CONF_ENERGY_PRICE,
    CONF_CRITICAL_IGNORE,
    CONF_CRITICAL_EXTRA,
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
    CONF_VOICE_AGENT,
    CONF_BACKUP_AGENT,
    CONF_ANIMAL_SENSORS,
    CONF_SCREEN,
    CONF_SCREEN_ACTIONS,
    CONF_SCREEN_CLEAR_ACTIONS,
    CONF_ANIMAL_LOW,
    CONF_ANIMAL_HIGH,
    CONF_CHATTINESS,
    CONF_DAILY_AI_LIMIT,
    CONF_APPLIANCES,
    CONF_APPLIANCE_ANNOUNCE,
    CONF_APPLIANCE_WATTS,
    CONF_DEFAULT_SPEAKER,
    CONF_DOOR_CAMERA,
    CONF_DOOR_TRIGGERS,
    CONF_FACE_SENSOR,
    CONF_DOORSTEP_SENSORS,
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
    CONF_LINK_TRIGGERS,
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
        CONF_NIGHT_VOLUME, CONF_DUCK_MEDIA, CONF_ROOM_VOLUME, CONF_FULL_VOLUME_SPEAKERS, CONF_MEDIA_SPEAKERS, CONF_TTS_ENTITY, CONF_FOLLOW_YOU, CONF_FOLLOW_MINUTES, CONF_ALL_OCCUPIED,
        CONF_PERSONALITY, CONF_PERSONALITY_NOTES, CONF_CHATTINESS, CONF_BANNED_WORDS, CONF_HOUSE_RULES,
    ]),
    ("quiet", "Quiet & rooms", "mdi:weather-night", "When to keep it down, knowing when the house has gone to bed and woken up, and how rooms are worked out.", [
        CONF_QUIET_START, CONF_QUIET_END, CONF_SLEEP, CONF_SLEEP_MINUTES, CONF_BEDTIME_ROUTINES, CONF_SLEEP_ACTIONS,
        CONF_WAKE_ACTIONS, CONF_MOTION_WINDOW, CONF_PASS_THROUGH_AREAS,
        CONF_SUPPRESS_ENTITIES, CONF_SUPPRESS_SECONDS,
    ]),
    ("briefing", "Briefings", "mdi:text-box-outline", "What a briefing covers and when.", [
        CONF_BRIEFINGS, CONF_BRIEFING_SECTIONS, CONF_BRIEFING_WEATHER, CONF_BRIEFING_TODO,
        CONF_BRIEFING_CALENDARS, CONF_BRIEFING_SYSTEM, CONF_BRIEFING_TIME, CONF_MORNING_BRIEFING, CONF_DIGEST_TIME,
    ]),
    ("noticing", "Noticing & learning", "mdi:radar", "What it watches for and learns from.", [
        CONF_NOTICE_ENTITIES, CONF_LEARN_ENTITIES, CONF_LINK_TRIGGERS, CONF_SUGGEST_ROUTINES, CONF_WELCOME, CONF_WELCOME_PEOPLE,
        CONF_INSIDE_SENSORS, CONF_WEEKLY_REPORT,
    ]),
    ("nightwatch", "Night watch", "mdi:shield-moon-outline", "Doors and gates opening at times they never normally do, with nobody up.", [
        CONF_NIGHT_WATCH, CONF_NIGHT_WATCH_ENTITIES, CONF_NIGHT_WATCH_ANNOUNCE, CONF_NIGHT_WATCH_SPEAKERS,
    ]),
    ("camsec", "Camera security", "mdi:cctv", "People on the cameras: their route from one to the next, someone in the garden without the gate opening, anyone about at night.", [
        CONF_CAMSEC, CONF_CAMSEC_SENSORS, CONF_CAMSEC_INSIDE, CONF_CAMSEC_ENTRIES, CONF_CAMSEC_DESCRIBE,
    ]),
    ("heating", "Heating", "mdi:radiator", "What it learns about the heating, and warming up before people are up.", [
        CONF_HEATING_ENTITIES, CONF_PREHEAT, CONF_HEATING_LIMIT, CONF_COMFORT_TEMP, CONF_WARM_BY_WEEKDAYS, CONF_WARM_BY_WEEKENDS,
    ]),
    ("door", "Doorbell & AI looks", "mdi:doorbell-video", "Doorbell log, parcels, faces, and which AI looks at pictures.", [
        CONF_DOOR_CAMERA, CONF_DOOR_TRIGGERS, CONF_FACE_SENSOR, CONF_ANNOUNCE_VISITORS, CONF_PARCEL_ALERTS, CONF_DOORSTEP_SENSORS,
        CONF_AI_TASK_ENTITY, CONF_SECOND_OPINION, CONF_CAMERA_COOLDOWN,
    ]),
    ("phone", "Phone notifications", "mdi:cellphone-message", "What also goes to people's phones.", [
        CONF_NOTIFY_TARGETS, CONF_NOTIFY_WHEN,
    ]),
    ("safety", "Safety & security", "mdi:shield-home", "Smoke, CO, gas and leaks; doors left open; a check when everyone goes out.", [
        CONF_CRITICAL, CONF_CRITICAL_EXTRA, CONF_CRITICAL_IGNORE, CONF_LEFT_OPEN_MINUTES, CONF_LEFT_OPEN_IGNORE, CONF_LEAVING_CHECK,
    ]),
    ("party", "Light shows", "mdi:party-popper", "Disco, Halloween, Christmas and more: lights put on a show, can follow the music, then go back exactly as they were.", [
        CONF_PARTY_WHOLE_HOUSE, CONF_PARTY_LIGHTS, CONF_PARTY_EXCLUDE, CONF_PARTY_STYLE, CONF_PARTY_MINUTES,
        CONF_PARTY_SOUND, CONF_PARTY_SOUND_SWITCH, CONF_PARTY_SHOW,
    ]),
    ("mind", "Mind", "mdi:head-lightbulb-outline", "Remembering conversations, checking back on things, asking about what it can't explain, thinking over the day each night, a look at tomorrow each evening — and how many AI calls it may make a day.", [
        CONF_CONVERSATIONS, CONF_FOLLOW_UPS, CONF_CURIOSITY, CONF_REFLECTION, CONF_REFLECTION_TIME, CONF_FORESIGHT, CONF_FORESIGHT_TIME,
        CONF_DAILY_AI_LIMIT,
    ]),
    ("voicebackup", "Voice backup", "mdi:lifebuoy", "If the AI voice agent fails or times out, the same request goes to a backup agent so simple things still work. Point your Assist pipeline at the “with backup” agent to use it.", [
        CONF_VOICE_AGENT, CONF_BACKUP_AGENT,
    ]),
    ("screen", "Tablet screen", "mdi:tablet-dashboard", "What the Sentient screen card shows: alerts with the camera, notices, a morning card and the house at a glance. Actions can wake a tablet and switch it to that page.", [
        CONF_SCREEN, CONF_SCREEN_ACTIONS, CONF_SCREEN_CLEAR_ACTIONS,
    ]),
    ("animals", "Animals", "mdi:rabbit", "Temperatures where animals live outside — a hutch, a kennel, a coop: a word when it's too cold or too hot, and frost warnings the evening before.", [
        CONF_ANIMAL_SENSORS, CONF_ANIMAL_LOW, CONF_ANIMAL_HIGH,
    ]),
    ("appliances", "Appliances", "mdi:washing-machine", "How long things normally run: says when one's finished, or seems stuck — in plain words, no AI.", [
        CONF_APPLIANCES, CONF_APPLIANCE_ANNOUNCE, CONF_APPLIANCE_WATTS,
    ]),
    ("drive", "The drive", "mdi:car", "Which cars are on the drive, from a camera spot — one AI look when activity settles.", [
        CONF_DRIVE_SPOT, CONF_DRIVE_CAMERA, CONF_DRIVE_TRIGGERS,
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
    websocket_api.async_register_command(hass, ws_thumb)
    from .wizard import async_register as _wizard

    _wizard(hass)
    websocket_api.async_register_command(hass, ws_save_settings)
    websocket_api.async_register_command(hass, ws_update_memory)
    websocket_api.async_register_command(hass, ws_list_memories)
    websocket_api.async_register_command(hass, ws_list_spots)
    websocket_api.async_register_command(hass, ws_learn_spot)
    websocket_api.async_register_command(hass, ws_set_spot)
    websocket_api.async_register_command(hass, ws_spot_aliases)
    websocket_api.async_register_command(hass, ws_forget_spot)
    websocket_api.async_register_command(hass, ws_look)
    websocket_api.async_register_command(hass, ws_motion_list)
    websocket_api.async_register_command(hass, ws_timeline)
    websocket_api.async_register_command(hass, ws_digest)
    websocket_api.async_register_command(hass, ws_mutes)
    websocket_api.async_register_command(hass, ws_insights)
    websocket_api.async_register_command(hass, ws_items)
    websocket_api.async_register_command(hass, ws_items_save)
    websocket_api.async_register_command(hass, ws_items_delete)
    websocket_api.async_register_command(hass, ws_items_test)
    websocket_api.async_register_command(hass, ws_weekly)
    websocket_api.async_register_command(hass, ws_shortcuts)
    websocket_api.async_register_command(hass, ws_shortcut_save)
    websocket_api.async_register_command(hass, ws_shortcut_delete)
    websocket_api.async_register_command(hass, ws_export)
    websocket_api.async_register_command(hass, ws_energy)
    websocket_api.async_register_command(hass, ws_security)
    websocket_api.async_register_command(hass, ws_critical_test)
    websocket_api.async_register_command(hass, ws_import)
    websocket_api.async_register_command(hass, ws_motion_save)
    websocket_api.async_register_command(hass, ws_motion_delete)
    websocket_api.async_register_command(hass, ws_motion_night)


async def _labels(hass: HomeAssistant, lang: str) -> tuple[dict[str, str], dict[str, str], dict[str, str]]:
    """Field labels, helper lines and dropdown texts from the translations."""
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
    # "chattiness.options.quiet" -> "Quiet", for the form's dropdowns
    texts = {k.removeprefix(f"component.{DOMAIN}.selector."): v for k, v in selectors.items()}
    return labels, helpers, texts


@websocket_api.websocket_command({vol.Required("type"): "sentient/settings", vol.Optional("language"): str})
@websocket_api.require_admin
@websocket_api.async_response
async def ws_get_settings(hass: HomeAssistant, connection, msg: dict[str, Any]) -> None:
    entry = _entry(hass)
    if entry is None:
        connection.send_error(msg["id"], "not_loaded", "Sentient is not set up")
        return
    labels, helpers, selector_texts = await _labels(hass, msg.get("language") or hass.config.language)
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
            "selector": selector_texts,
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
        vol.Optional("points"): [vol.All([vol.Coerce(float)], vol.Length(min=2, max=2))],
        vol.Optional("aliases"): str,
    }
)
@websocket_api.require_admin
@websocket_api.async_response
async def ws_set_spot(hass: HomeAssistant, connection, msg: dict[str, Any]) -> None:
    async def job(rt):
        return rt.spots.set(msg["camera"], msg["name"], msg["box"], msg.get("points"), msg.get("aliases"))

    await _spot_call(hass, connection, msg, job)


@websocket_api.websocket_command(
    {vol.Required("type"): "sentient/spots/aliases", vol.Required("name"): str, vol.Required("aliases"): str}
)
@websocket_api.require_admin
@websocket_api.async_response
async def ws_spot_aliases(hass: HomeAssistant, connection, msg: dict[str, Any]) -> None:
    async def job(rt):
        return rt.spots.set_aliases(msg["name"], msg["aliases"])

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


@websocket_api.websocket_command({
    vol.Required("type"): "sentient/mutes", vol.Optional("remove"): str, vol.Optional("clear"): bool,
    vol.Optional("unlearn"): str, vol.Optional("unlearn_all"): bool,
})
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
    feedback = entry.runtime_data.feedback
    if msg.get("unlearn_all"):
        feedback.reset(None)
    elif msg.get("unlearn"):
        feedback.reset(msg["unlearn"])
    connection.send_result(msg["id"], {"mutes": mutes.listing(), "learned": feedback.listing()})


@websocket_api.websocket_command({
    vol.Required("type"): "sentient/feedback/thumb", vol.Required("text"): str, vol.Optional("source"): vol.Any(str, None),
    vol.Required("up"): bool,
})
@callback
def ws_thumb(hass: HomeAssistant, connection, msg: dict[str, Any]) -> None:
    """A thumbs up or down on something it said."""
    entry = _entry(hass)
    if entry is None:
        connection.send_error(msg["id"], "not_loaded", "Sentient is not set up")
        return
    result = entry.runtime_data.feedback.thumb(msg["text"], msg.get("source"), msg["up"],
                                               sorted(entry.runtime_data.noticer.entities))
    entry.runtime_data.timeline.add(
        "learned", f"{'Thumbs up' if msg['up'] else 'Thumbs down'}: {msg['text'][:120]}",
        ("about " + ", ".join(result["about"])) if result["about"] else None,
    )
    connection.send_result(msg["id"], result)


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


@websocket_api.websocket_command({vol.Required("type"): "sentient/security"})
@callback
def ws_security(hass: HomeAssistant, connection, msg: dict[str, Any]) -> None:
    from .security import check

    connection.send_result(msg["id"], check(hass))


@websocket_api.websocket_command({vol.Required("type"): "sentient/critical/test"})
@websocket_api.require_admin
@websocket_api.async_response
async def ws_critical_test(hass: HomeAssistant, connection, msg: dict[str, Any]) -> None:
    entry = _entry(hass)
    if entry is None:
        connection.send_error(msg["id"], "not_loaded", "Sentient is not set up")
        return
    connection.send_result(msg["id"], await entry.runtime_data.critical.async_test())



@websocket_api.websocket_command({vol.Required("type"): "sentient/insights"})
@callback
def ws_insights(hass: HomeAssistant, connection, msg: dict[str, Any]) -> None:
    """What the house has learned about heating and its doors at night."""
    entry = _entry(hass)
    if entry is None:
        connection.send_error(msg["id"], "not_loaded", "Sentient is not loaded")
        return
    rt = entry.runtime_data
    connection.send_result(msg["id"], {"heating": rt.heating.report(), "night_watch": rt.night_watch.describe(),
                                       "camera_security": rt.camsec.describe()})


@websocket_api.websocket_command({vol.Required("type"): "sentient/weekly"})
@callback
def ws_weekly(hass: HomeAssistant, connection, msg: dict[str, Any]) -> None:
    """Make the weekly 'what I learned' report now (also kept on the timeline)."""
    entry = _entry(hass)
    if entry is None:
        connection.send_error(msg["id"], "not_loaded", "Sentient is not loaded")
        return
    connection.send_result(msg["id"], entry.runtime_data.upkeep.weekly_report())



# ----- offers, announcements, alarms, camera checks ---------------------------------------
def _items_payload(hass: HomeAssistant, entry, kind: str) -> dict[str, Any]:
    from .kinds import KINDS, summary

    meta = KINDS[kind]
    items = entry.runtime_data.lists[kind].listing()
    for item in items:
        item["_summary"] = summary(kind, item, hass)
    if kind == "alarms":
        nexts = {a["id"]: a["next"] for a in entry.runtime_data.alarms.listing()}
        for item in items:
            item["_next"] = nexts.get(item["id"])
    return {
        "items": items,
        "meta": {k: meta[k] for k in ("title", "icon", "blurb", "add", "schema", "labels", "defaults")},
    }


_KIND = vol.In(["offers", "announce", "alarms", "checks", "autooff"])


@websocket_api.websocket_command({vol.Required("type"): "sentient/items", vol.Required("kind"): _KIND})
@callback
def ws_items(hass: HomeAssistant, connection, msg: dict[str, Any]) -> None:
    entry = _entry(hass)
    if entry is None:
        connection.send_error(msg["id"], "not_loaded", "Sentient is not set up")
        return
    connection.send_result(msg["id"], _items_payload(hass, entry, msg["kind"]))


@websocket_api.websocket_command(
    {vol.Required("type"): "sentient/items/save", vol.Required("kind"): _KIND, vol.Required("item"): dict}
)
@websocket_api.require_admin
@websocket_api.async_response
async def ws_items_save(hass: HomeAssistant, connection, msg: dict[str, Any]) -> None:
    entry = _entry(hass)
    if entry is None:
        connection.send_error(msg["id"], "not_loaded", "Sentient is not set up")
        return
    item = {k: v for k, v in dict(msg["item"]).items() if not k.startswith("_")}
    try:
        from homeassistant.helpers.script import async_validate_actions_config

        for key in ("actions", "room_actions"):
            if item.get(key):
                await async_validate_actions_config(hass, cv.SCRIPT_SCHEMA(item[key]))
        entry.runtime_data.lists[msg["kind"]].upsert(item)
    except (vol.Invalid, ValueError, HomeAssistantError) as err:
        connection.send_error(msg["id"], "invalid", str(err))
        return
    connection.send_result(msg["id"], _items_payload(hass, entry, msg["kind"]))


@websocket_api.websocket_command(
    {vol.Required("type"): "sentient/items/delete", vol.Required("kind"): _KIND, vol.Required("item_id"): str}
)
@websocket_api.require_admin
@callback
def ws_items_delete(hass: HomeAssistant, connection, msg: dict[str, Any]) -> None:
    entry = _entry(hass)
    if entry is None:
        connection.send_error(msg["id"], "not_loaded", "Sentient is not set up")
        return
    entry.runtime_data.lists[msg["kind"]].remove(msg["item_id"])
    connection.send_result(msg["id"], _items_payload(hass, entry, msg["kind"]))


@websocket_api.websocket_command(
    {vol.Required("type"): "sentient/items/test", vol.Required("kind"): _KIND, vol.Required("item_id"): str}
)
@websocket_api.require_admin
@websocket_api.async_response
async def ws_items_test(hass: HomeAssistant, connection, msg: dict[str, Any]) -> None:
    """Try one now: ask the offer, say the announcement, ring the alarm, run the camera check."""
    entry = _entry(hass)
    if entry is None:
        connection.send_error(msg["id"], "not_loaded", "Sentient is not set up")
        return
    rt = entry.runtime_data
    item = rt.lists[msg["kind"]].items.get(msg["item_id"])
    if item is None:
        connection.send_error(msg["id"], "not_found", "It's gone")
        return
    kind = msg["kind"]
    try:
        if kind == "offers":
            result = await rt.offers.async_ask(item, (item.get("areas") or [None])[0])
        elif kind == "announce":
            result = await rt.announcements.async_announce(item)
        elif kind == "alarms":
            result = await rt.speech.async_say(
                (item.get("message") or "").strip() or f"{item['name']}." + (f" {item['description']}." if item.get("description") else ""),
                "urgent", [s for s in item.get("speakers") or [] if hass.states.get(s)] or None, source="alarm test",
            )
            result = result.as_dict()
        elif kind == "autooff":
            on = [e for e in rt.autooff.targets(item) if hass.states.get(e) and hass.states.get(e).state == "on"]
            await rt.autooff.async_switch_off(item, on)
            result = {"switched_off": on}
        else:
            result = await rt.checks.async_check(item, force=True)
    except Exception as err:  # noqa: BLE001
        connection.send_error(msg["id"], "failed", str(err))
        return
    connection.send_result(msg["id"], {**_items_payload(hass, entry, kind), "result": result})
