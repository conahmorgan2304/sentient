"""Setup wizard for Sentient."""

from __future__ import annotations

from typing import Any

import voluptuous as vol

from homeassistant.config_entries import ConfigEntry, ConfigFlow, ConfigFlowResult, OptionsFlow
from homeassistant.core import callback
from homeassistant.helpers import selector

from .const import (
    CONF_COMFORT_TEMP,
    CONF_CAMSEC,
    CONF_CAMSEC_DESCRIBE,
    CONF_CAMSEC_ENTRIES,
    CONF_CAMSEC_INSIDE,
    CONF_CAMSEC_SENSORS,
    CONF_PARTY_EXCLUDE,
    CONF_PARTY_LIGHTS,
    CONF_PARTY_MINUTES,
    CONF_PARTY_SHOW,
    CONF_PARTY_SOUND,
    CONF_PARTY_SOUND_SWITCH,
    CONF_PARTY_STYLE,
    CONF_PARTY_WHOLE_HOUSE,
    DEFAULT_PARTY_SHOW,
    DEFAULT_PARTY_MINUTES,
    DEFAULT_PARTY_STYLE,
    PARTY_STYLES,
    CONF_PERSONALITY,
    CONF_PERSONALITY_NOTES,
    DEFAULT_PERSONALITY,
    PERSONALITIES,
    CONF_WARM_BY_WEEKDAYS,
    CONF_WARM_BY_WEEKENDS,
    CONF_HEATING_ENTITIES,
    CONF_HEATING_LIMIT,
    DEFAULT_HEATING_LIMIT,
    CONF_INSIDE_SENSORS,
    CONF_NIGHT_WATCH,
    CONF_NIGHT_WATCH_ANNOUNCE,
    CONF_NIGHT_WATCH_ENTITIES,
    CONF_NIGHT_WATCH_SPEAKERS,
    CONF_PREHEAT,
    CONF_WEEKLY_REPORT,
    DEFAULT_NIGHT_WATCH,
    DEFAULT_WEEKLY_REPORT,
    CONF_BATTERY_THRESHOLD,
    CONF_HEALTH_IGNORE,
    DEFAULT_BATTERY_THRESHOLD,
    CONF_CHAT_AGENT,
    CONF_VOICE_AGENT,
    CONF_BACKUP_AGENT,
    DEFAULT_BACKUP_AGENT,
    CONF_ANIMAL_SENSORS,
    CONF_SCREEN,
    CONF_SCREEN_ACTIONS,
    CONF_SCREEN_CLEAR_ACTIONS,
    CONF_ANIMAL_LOW,
    CONF_ANIMAL_HIGH,
    DEFAULT_ANIMAL_LOW,
    DEFAULT_ANIMAL_HIGH,
    CONF_SIDEBAR,
    DEFAULT_SIDEBAR,
    BRIEFING_SECTIONS,
    CONF_BRIEFING_CALENDARS,
    CONF_BRIEFING_SECTIONS,
    CONF_BRIEFING_SYSTEM,
    CONF_BRIEFING_TIME,
    CONF_BRIEFING_TODO,
    CONF_BRIEFING_WEATHER,
    CONF_BRIEFINGS,
    DEFAULT_BRIEFING_SECTIONS,
    CONF_FACE_SENSOR,
    CONF_DOORSTEP_SENSORS,
    CONF_ANNOUNCE_VISITORS,
    CONF_DOOR_CAMERA,
    CONF_DOOR_TRIGGERS,
    CONF_PARCEL_ALERTS,
    DEFAULT_ANNOUNCE_VISITORS,
    DEFAULT_PARCEL_ALERTS,
    CONF_LEARN_ENTITIES,
    CONF_SUGGEST_ROUTINES,
    CONF_LINK_TRIGGERS,
    DEFAULT_SUGGEST_ROUTINES,
    CHATTINESS_LIMITS,
    CONF_CHATTINESS,
    CONF_HOUSE_RULES,
    CONF_NOTICE_ENTITIES,
    DEFAULT_CHATTINESS,
    CONF_DAILY_AI_LIMIT,
    CONF_APPLIANCES,
    CONF_APPLIANCE_ANNOUNCE,
    CONF_APPLIANCE_WATTS,
    DEFAULT_APPLIANCE_WATTS,
    CONF_FALLBACK_SPEAKERS,
    DEFAULT_DAILY_AI_LIMIT,
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
    CONF_MEDIA_IMPORTANT_ONLY,
    CONF_SLEEP,
    CONF_SLEEP_MINUTES,
    DEFAULT_SLEEP_MINUTES,
    CONF_BEDTIME_ROUTINES,
    CONF_SLEEP_ACTIONS,
    CONF_WAKE_ACTIONS,
    CONF_FORESIGHT,
    CONF_FORESIGHT_TIME,
    DEFAULT_FORESIGHT_TIME,
    CONF_FOLLOW_YOU,
    CONF_FOLLOW_MINUTES,
    DEFAULT_FOLLOW_MINUTES,
    CONF_DRIVE_CAMERA,
    CONF_DRIVE_SPOT,
    CONF_DRIVE_TRIGGERS,
    DEFAULT_DRIVE_SPOT,
    CONF_CONVERSATIONS,
    CONF_FOLLOW_UPS,
    CONF_CURIOSITY,
    CONF_REFLECTION,
    CONF_REFLECTION_TIME,
    DEFAULT_REFLECTION_TIME,
    CONF_CRITICAL,
    CONF_ENERGY,
    CONF_LEFT_OPEN_MINUTES,
    CONF_LEFT_OPEN_IGNORE,
    CONF_LEAVING_CHECK,
    CONF_ENERGY_PRICE,
    CONF_CRITICAL_IGNORE,
    CONF_CRITICAL_EXTRA,
    CONF_WELCOME_PEOPLE,
    DEFAULT_CHIME,
    CONF_NOTIFY_WHEN,
    NOTIFY_KINDS,
    DEFAULT_NOTIFY_WHEN,
    CONF_CAMERA_COOLDOWN,
    DEFAULT_CAMERA_COOLDOWN,
    CONF_ALL_OCCUPIED,
    DEFAULT_ALL_OCCUPIED,
    CONF_BANNED_WORDS,
    CONF_DEFAULT_SPEAKER,
    CONF_MOTION_WINDOW,
    CONF_PASS_THROUGH_AREAS,
    CONF_PERSONA,
    CONF_PROMPT_MEMORIES,
    CONF_QUIET_END,
    CONF_QUIET_START,
    CONF_SPEAKERS,
    CONF_SUPPRESS_ENTITIES,
    CONF_SUPPRESS_SECONDS,
    DEFAULT_BANNED_WORDS,
    DEFAULT_MOTION_WINDOW,
    DEFAULT_PERSONA,
    DEFAULT_PROMPT_MEMORIES,
    DEFAULT_QUIET_END,
    DEFAULT_QUIET_START,
    DEFAULT_SUPPRESS_SECONDS,
    DOMAIN,
    MEMORY_CATEGORIES,
)

_SPEAKER = selector.EntitySelector(selector.EntitySelectorConfig(domain=["assist_satellite", "media_player"], multiple=True))
_ONE_SPEAKER = selector.EntitySelector(selector.EntitySelectorConfig(domain=["assist_satellite", "media_player"]))


def _speech_schema(defaults: dict[str, Any]) -> vol.Schema:
    def d(key: str, fallback: Any = None) -> dict[str, Any]:
        value = defaults.get(key, fallback)
        return {} if value is None else {"default": value}

    return vol.Schema(
        {
            vol.Optional(CONF_SPEAKERS, **d(CONF_SPEAKERS, [])): _SPEAKER,
            vol.Optional(CONF_DEFAULT_SPEAKER, **d(CONF_DEFAULT_SPEAKER)): _ONE_SPEAKER,
            vol.Optional(CONF_FALLBACK_SPEAKERS, **d(CONF_FALLBACK_SPEAKERS, [])): _SPEAKER,
            vol.Required(CONF_SIDEBAR, **d(CONF_SIDEBAR, DEFAULT_SIDEBAR)): selector.BooleanSelector(),
            vol.Optional(CONF_VOICE_AGENT, **d(CONF_VOICE_AGENT)): selector.EntitySelector(
                selector.EntitySelectorConfig(domain="conversation")
            ),
            vol.Optional(CONF_BACKUP_AGENT, **d(CONF_BACKUP_AGENT, DEFAULT_BACKUP_AGENT)): selector.EntitySelector(
                selector.EntitySelectorConfig(domain="conversation")
            ),
            vol.Required(CONF_SCREEN, **d(CONF_SCREEN, True)): selector.BooleanSelector(),
            vol.Optional(CONF_SCREEN_ACTIONS, **d(CONF_SCREEN_ACTIONS, [])): selector.ActionSelector(),
            vol.Optional(CONF_SCREEN_CLEAR_ACTIONS, **d(CONF_SCREEN_CLEAR_ACTIONS, [])): selector.ActionSelector(),
            vol.Optional(CONF_ANIMAL_SENSORS, **d(CONF_ANIMAL_SENSORS, [])): selector.EntitySelector(
                selector.EntitySelectorConfig(domain="sensor", device_class="temperature", multiple=True)
            ),
            vol.Required(CONF_ANIMAL_LOW, **d(CONF_ANIMAL_LOW, DEFAULT_ANIMAL_LOW)): selector.NumberSelector(
                selector.NumberSelectorConfig(min=-10, max=20, step=0.5, unit_of_measurement="°C", mode=selector.NumberSelectorMode.BOX)
            ),
            vol.Required(CONF_ANIMAL_HIGH, **d(CONF_ANIMAL_HIGH, DEFAULT_ANIMAL_HIGH)): selector.NumberSelector(
                selector.NumberSelectorConfig(min=15, max=45, step=0.5, unit_of_measurement="°C", mode=selector.NumberSelectorMode.BOX)
            ),
            vol.Optional(CONF_CHAT_AGENT, **d(CONF_CHAT_AGENT)): selector.EntitySelector(
                selector.EntitySelectorConfig(domain="conversation")
            ),
            vol.Required(CONF_CHIME, **d(CONF_CHIME, DEFAULT_CHIME)): selector.BooleanSelector(),
            vol.Optional(CONF_SPEAK_VOLUME, **d(CONF_SPEAK_VOLUME)): selector.NumberSelector(
                selector.NumberSelectorConfig(min=5, max=100, step=5, unit_of_measurement="%", mode=selector.NumberSelectorMode.SLIDER)
            ),
            vol.Optional(CONF_NIGHT_VOLUME, **d(CONF_NIGHT_VOLUME)): selector.NumberSelector(
                selector.NumberSelectorConfig(min=5, max=100, step=5, unit_of_measurement="%", mode=selector.NumberSelectorMode.SLIDER)
            ),
            vol.Required(CONF_DUCK_MEDIA, **d(CONF_DUCK_MEDIA, True)): selector.BooleanSelector(),
            vol.Required(CONF_ROOM_VOLUME, **d(CONF_ROOM_VOLUME, True)): selector.BooleanSelector(),
            vol.Optional(CONF_FULL_VOLUME_SPEAKERS, **d(CONF_FULL_VOLUME_SPEAKERS, [])): _SPEAKER,
            vol.Required(CONF_MEDIA_SPEAKERS, **d(CONF_MEDIA_SPEAKERS, True)): selector.BooleanSelector(),
            vol.Required(CONF_MEDIA_IMPORTANT_ONLY, **d(CONF_MEDIA_IMPORTANT_ONLY, False)): selector.BooleanSelector(),
            vol.Optional(CONF_TTS_ENTITY, **d(CONF_TTS_ENTITY)): selector.EntitySelector(
                selector.EntitySelectorConfig(domain="tts")
            ),
            vol.Required(CONF_FOLLOW_YOU, **d(CONF_FOLLOW_YOU, True)): selector.BooleanSelector(),
            vol.Required(CONF_FOLLOW_MINUTES, **d(CONF_FOLLOW_MINUTES, DEFAULT_FOLLOW_MINUTES)): selector.NumberSelector(
                selector.NumberSelectorConfig(min=5, max=720, step=5, unit_of_measurement="min", mode=selector.NumberSelectorMode.BOX)
            ),
            vol.Required(CONF_CONVERSATIONS, **d(CONF_CONVERSATIONS, True)): selector.BooleanSelector(),
            vol.Required(CONF_FOLLOW_UPS, **d(CONF_FOLLOW_UPS, True)): selector.BooleanSelector(),
            vol.Required(CONF_CURIOSITY, **d(CONF_CURIOSITY, True)): selector.BooleanSelector(),
            vol.Required(CONF_REFLECTION, **d(CONF_REFLECTION, True)): selector.BooleanSelector(),
            vol.Required(CONF_REFLECTION_TIME, **d(CONF_REFLECTION_TIME, DEFAULT_REFLECTION_TIME)): selector.TimeSelector(),
            vol.Required(CONF_SLEEP, **d(CONF_SLEEP, True)): selector.BooleanSelector(),
            vol.Required(CONF_SLEEP_MINUTES, **d(CONF_SLEEP_MINUTES, DEFAULT_SLEEP_MINUTES)): selector.NumberSelector(
                selector.NumberSelectorConfig(min=5, max=120, step=5, unit_of_measurement="min", mode=selector.NumberSelectorMode.BOX)
            ),
            vol.Optional(CONF_BEDTIME_ROUTINES, **d(CONF_BEDTIME_ROUTINES, "")): selector.TextSelector(),
            vol.Optional(CONF_SLEEP_ACTIONS, **d(CONF_SLEEP_ACTIONS, [])): selector.ActionSelector(),
            vol.Optional(CONF_WAKE_ACTIONS, **d(CONF_WAKE_ACTIONS, [])): selector.ActionSelector(),
            vol.Required(CONF_FORESIGHT, **d(CONF_FORESIGHT, True)): selector.BooleanSelector(),
            vol.Required(CONF_FORESIGHT_TIME, **d(CONF_FORESIGHT_TIME, DEFAULT_FORESIGHT_TIME)): selector.TimeSelector(),
            vol.Optional(CONF_APPLIANCES, **d(CONF_APPLIANCES, [])): selector.EntitySelector(
                selector.EntitySelectorConfig(domain=["sensor", "vacuum", "switch", "binary_sensor", "select", "climate", "water_heater", "media_player", "fan"], multiple=True)
            ),
            vol.Required(CONF_APPLIANCE_ANNOUNCE, **d(CONF_APPLIANCE_ANNOUNCE, True)): selector.BooleanSelector(),
            vol.Required(CONF_APPLIANCE_WATTS, **d(CONF_APPLIANCE_WATTS, DEFAULT_APPLIANCE_WATTS)): selector.NumberSelector(
                selector.NumberSelectorConfig(min=1, max=200, step=1, unit_of_measurement="W", mode=selector.NumberSelectorMode.BOX)
            ),
            vol.Optional(CONF_DRIVE_CAMERA, **d(CONF_DRIVE_CAMERA)): selector.EntitySelector(
                selector.EntitySelectorConfig(domain="camera")
            ),
            vol.Required(CONF_DRIVE_SPOT, **d(CONF_DRIVE_SPOT, DEFAULT_DRIVE_SPOT)): selector.TextSelector(),
            vol.Optional(CONF_DRIVE_TRIGGERS, **d(CONF_DRIVE_TRIGGERS, [])): selector.EntitySelector(
                selector.EntitySelectorConfig(domain="binary_sensor", multiple=True)
            ),
            vol.Required(CONF_CRITICAL, **d(CONF_CRITICAL, True)): selector.BooleanSelector(),
            vol.Required(CONF_LEFT_OPEN_MINUTES, **d(CONF_LEFT_OPEN_MINUTES, 0)): selector.NumberSelector(
                selector.NumberSelectorConfig(min=0, max=240, step=5, unit_of_measurement="min", mode=selector.NumberSelectorMode.BOX)
            ),
            vol.Optional(CONF_LEFT_OPEN_IGNORE, **d(CONF_LEFT_OPEN_IGNORE, [])): selector.EntitySelector(
                selector.EntitySelectorConfig(domain="binary_sensor", multiple=True)
            ),
            vol.Required(CONF_LEAVING_CHECK, **d(CONF_LEAVING_CHECK, True)): selector.BooleanSelector(),
            vol.Optional(CONF_HEATING_ENTITIES, **d(CONF_HEATING_ENTITIES, [])): selector.EntitySelector(
                selector.EntitySelectorConfig(domain="climate", multiple=True)
            ),
            vol.Required(CONF_PREHEAT, **d(CONF_PREHEAT, False)): selector.BooleanSelector(),
            vol.Required(CONF_HEATING_LIMIT, **d(CONF_HEATING_LIMIT, DEFAULT_HEATING_LIMIT)): selector.NumberSelector(
                selector.NumberSelectorConfig(min=0, max=24, step=0.5, unit_of_measurement="h", mode=selector.NumberSelectorMode.BOX)
            ),
            vol.Optional(CONF_COMFORT_TEMP, **d(CONF_COMFORT_TEMP)): selector.NumberSelector(
                selector.NumberSelectorConfig(min=10, max=26, step=0.5, unit_of_measurement="°C", mode=selector.NumberSelectorMode.BOX)
            ),
            vol.Optional(CONF_WARM_BY_WEEKDAYS, **d(CONF_WARM_BY_WEEKDAYS)): selector.TimeSelector(),
            vol.Optional(CONF_WARM_BY_WEEKENDS, **d(CONF_WARM_BY_WEEKENDS)): selector.TimeSelector(),
            vol.Optional(CONF_INSIDE_SENSORS, **d(CONF_INSIDE_SENSORS, [])): selector.EntitySelector(
                selector.EntitySelectorConfig(domain="binary_sensor", multiple=True)
            ),
            vol.Required(CONF_NIGHT_WATCH, **d(CONF_NIGHT_WATCH, DEFAULT_NIGHT_WATCH)): selector.BooleanSelector(),
            vol.Optional(CONF_NIGHT_WATCH_ENTITIES, **d(CONF_NIGHT_WATCH_ENTITIES, [])): selector.EntitySelector(
                selector.EntitySelectorConfig(domain="binary_sensor", multiple=True)
            ),
            vol.Required(CONF_NIGHT_WATCH_ANNOUNCE, **d(CONF_NIGHT_WATCH_ANNOUNCE, False)): selector.BooleanSelector(),
            vol.Optional(CONF_NIGHT_WATCH_SPEAKERS, **d(CONF_NIGHT_WATCH_SPEAKERS, [])): selector.EntitySelector(
                selector.EntitySelectorConfig(domain=["assist_satellite", "media_player"], multiple=True)
            ),
            vol.Required(CONF_CAMSEC, **d(CONF_CAMSEC, False)): selector.BooleanSelector(),
            vol.Optional(CONF_CAMSEC_SENSORS, **d(CONF_CAMSEC_SENSORS, [])): selector.EntitySelector(
                selector.EntitySelectorConfig(domain="binary_sensor", multiple=True)
            ),
            vol.Optional(CONF_CAMSEC_INSIDE, **d(CONF_CAMSEC_INSIDE, [])): selector.EntitySelector(
                selector.EntitySelectorConfig(domain="binary_sensor", multiple=True)
            ),
            vol.Optional(CONF_CAMSEC_ENTRIES, **d(CONF_CAMSEC_ENTRIES, [])): selector.EntitySelector(
                selector.EntitySelectorConfig(domain=["binary_sensor", "cover", "lock"], multiple=True)
            ),
            vol.Required(CONF_CAMSEC_DESCRIBE, **d(CONF_CAMSEC_DESCRIBE, True)): selector.BooleanSelector(),
            vol.Required(CONF_PARTY_WHOLE_HOUSE, **d(CONF_PARTY_WHOLE_HOUSE, False)): selector.BooleanSelector(),
            vol.Optional(CONF_PARTY_LIGHTS, **d(CONF_PARTY_LIGHTS, [])): selector.EntitySelector(
                selector.EntitySelectorConfig(domain="light", multiple=True)
            ),
            vol.Optional(CONF_PARTY_EXCLUDE, **d(CONF_PARTY_EXCLUDE, [])): selector.EntitySelector(
                selector.EntitySelectorConfig(domain="light", multiple=True)
            ),
            vol.Required(CONF_PARTY_STYLE, **d(CONF_PARTY_STYLE, DEFAULT_PARTY_STYLE)): selector.SelectSelector(
                selector.SelectSelectorConfig(options=PARTY_STYLES, translation_key="party_style")
            ),
            vol.Required(CONF_PARTY_MINUTES, **d(CONF_PARTY_MINUTES, DEFAULT_PARTY_MINUTES)): selector.NumberSelector(
                selector.NumberSelectorConfig(min=0, max=600, step=5, unit_of_measurement="min", mode=selector.NumberSelectorMode.BOX)
            ),
            vol.Optional(CONF_PARTY_SOUND, **d(CONF_PARTY_SOUND)): selector.EntitySelector(
                selector.EntitySelectorConfig(domain="sensor")
            ),
            vol.Optional(CONF_PARTY_SOUND_SWITCH, **d(CONF_PARTY_SOUND_SWITCH)): selector.EntitySelector(
                selector.EntitySelectorConfig(domain="switch")
            ),
            vol.Optional(CONF_PARTY_SHOW, **d(CONF_PARTY_SHOW, DEFAULT_PARTY_SHOW)): selector.SelectSelector(
                selector.SelectSelectorConfig(options=PARTY_STYLES, multiple=True, mode=selector.SelectSelectorMode.LIST, translation_key="party_style")
            ),
            vol.Required(CONF_WEEKLY_REPORT, **d(CONF_WEEKLY_REPORT, DEFAULT_WEEKLY_REPORT)): selector.BooleanSelector(),
            vol.Required(CONF_ENERGY, **d(CONF_ENERGY, True)): selector.BooleanSelector(),
            vol.Optional(CONF_ENERGY_PRICE, **d(CONF_ENERGY_PRICE)): selector.NumberSelector(
                selector.NumberSelectorConfig(min=0, max=200, step=0.01, unit_of_measurement="p/kWh", mode=selector.NumberSelectorMode.BOX)
            ),
            vol.Optional(CONF_CRITICAL_EXTRA, **d(CONF_CRITICAL_EXTRA, [])): selector.EntitySelector(
                selector.EntitySelectorConfig(domain=["binary_sensor", "input_boolean"], multiple=True)
            ),
            vol.Optional(CONF_CRITICAL_IGNORE, **d(CONF_CRITICAL_IGNORE, [])): selector.EntitySelector(
                selector.EntitySelectorConfig(domain=["binary_sensor", "alarm_control_panel"], multiple=True)
            ),
            vol.Required(CONF_ALL_OCCUPIED, **d(CONF_ALL_OCCUPIED, DEFAULT_ALL_OCCUPIED)): selector.BooleanSelector(),
            vol.Required(CONF_QUIET_START, **d(CONF_QUIET_START, DEFAULT_QUIET_START)): selector.TimeSelector(),
            vol.Required(CONF_QUIET_END, **d(CONF_QUIET_END, DEFAULT_QUIET_END)): selector.TimeSelector(),
            vol.Required(CONF_MOTION_WINDOW, **d(CONF_MOTION_WINDOW, DEFAULT_MOTION_WINDOW)): selector.NumberSelector(
                selector.NumberSelectorConfig(min=1, max=60, step=1, unit_of_measurement="min", mode=selector.NumberSelectorMode.BOX)
            ),
            vol.Optional(CONF_PASS_THROUGH_AREAS, **d(CONF_PASS_THROUGH_AREAS, [])): selector.AreaSelector(
                selector.AreaSelectorConfig(multiple=True)
            ),
            vol.Optional(CONF_BANNED_WORDS, **d(CONF_BANNED_WORDS, DEFAULT_BANNED_WORDS)): selector.TextSelector(),
            vol.Optional(CONF_SUPPRESS_ENTITIES, **d(CONF_SUPPRESS_ENTITIES, [])): selector.EntitySelector(
                selector.EntitySelectorConfig(domain=["automation", "script"], multiple=True)
            ),
            vol.Required(CONF_SUPPRESS_SECONDS, **d(CONF_SUPPRESS_SECONDS, DEFAULT_SUPPRESS_SECONDS)): selector.NumberSelector(
                selector.NumberSelectorConfig(min=0, max=600, step=5, unit_of_measurement="s", mode=selector.NumberSelectorMode.BOX)
            ),
            vol.Optional(CONF_NOTICE_ENTITIES, **d(CONF_NOTICE_ENTITIES, [])): selector.EntitySelector(
                selector.EntitySelectorConfig(multiple=True)
            ),
            vol.Required(CONF_PERSONALITY, **d(CONF_PERSONALITY, DEFAULT_PERSONALITY)): selector.SelectSelector(
                selector.SelectSelectorConfig(options=list(PERSONALITIES), translation_key="personality")
            ),
            vol.Optional(CONF_PERSONALITY_NOTES, **d(CONF_PERSONALITY_NOTES, "")): selector.TextSelector(
                selector.TextSelectorConfig(multiline=True)
            ),
            vol.Required(CONF_CHATTINESS, **d(CONF_CHATTINESS, DEFAULT_CHATTINESS)): selector.SelectSelector(
                selector.SelectSelectorConfig(options=list(CHATTINESS_LIMITS), translation_key="chattiness")
            ),
            vol.Optional(CONF_HOUSE_RULES, **d(CONF_HOUSE_RULES, "")): selector.TextSelector(
                selector.TextSelectorConfig(multiline=True)
            ),
            vol.Optional(CONF_LEARN_ENTITIES, **d(CONF_LEARN_ENTITIES, [])): selector.EntitySelector(
                selector.EntitySelectorConfig(domain=["light", "switch", "fan", "input_boolean", "cover"], multiple=True)
            ),
            vol.Required(CONF_WELCOME, **d(CONF_WELCOME, False)): selector.BooleanSelector(),
            vol.Optional(CONF_WELCOME_PEOPLE, **d(CONF_WELCOME_PEOPLE, [])): selector.EntitySelector(
                selector.EntitySelectorConfig(domain="person", multiple=True)
            ),
            vol.Optional(CONF_LINK_TRIGGERS, **d(CONF_LINK_TRIGGERS, [])): selector.EntitySelector(
                selector.EntitySelectorConfig(domain=["binary_sensor", "media_player", "input_boolean", "switch", "light"], multiple=True)
            ),
            vol.Required(CONF_SUGGEST_ROUTINES, **d(CONF_SUGGEST_ROUTINES, DEFAULT_SUGGEST_ROUTINES)): selector.BooleanSelector(),
            vol.Optional(CONF_DOOR_CAMERA, **d(CONF_DOOR_CAMERA)): selector.EntitySelector(
                selector.EntitySelectorConfig(domain="camera")
            ),
            vol.Optional(CONF_DOOR_TRIGGERS, **d(CONF_DOOR_TRIGGERS, [])): selector.EntitySelector(
                selector.EntitySelectorConfig(domain=["binary_sensor", "event"], multiple=True)
            ),
            vol.Optional(CONF_FACE_SENSOR, **d(CONF_FACE_SENSOR)): selector.EntitySelector(
                selector.EntitySelectorConfig(domain=["sensor", "input_text", "text", "image"])
            ),
            vol.Required(CONF_ANNOUNCE_VISITORS, **d(CONF_ANNOUNCE_VISITORS, DEFAULT_ANNOUNCE_VISITORS)): selector.BooleanSelector(),
            vol.Required(CONF_PARCEL_ALERTS, **d(CONF_PARCEL_ALERTS, DEFAULT_PARCEL_ALERTS)): selector.BooleanSelector(),
            vol.Optional(CONF_DOORSTEP_SENSORS, **d(CONF_DOORSTEP_SENSORS, [])): selector.EntitySelector(
                selector.EntitySelectorConfig(domain="binary_sensor", multiple=True)
            ),
            vol.Required(CONF_BATTERY_THRESHOLD, **d(CONF_BATTERY_THRESHOLD, DEFAULT_BATTERY_THRESHOLD)): selector.NumberSelector(
                selector.NumberSelectorConfig(min=0, max=100, step=5, unit_of_measurement="%", mode=selector.NumberSelectorMode.BOX)
            ),
            vol.Optional(CONF_HEALTH_IGNORE, **d(CONF_HEALTH_IGNORE, [])): selector.EntitySelector(
                selector.EntitySelectorConfig(multiple=True)
            ),
            vol.Optional(CONF_NOTIFY_TARGETS, **d(CONF_NOTIFY_TARGETS, [])): selector.EntitySelector(
                selector.EntitySelectorConfig(domain="notify", multiple=True)
            ),
            vol.Optional(CONF_NOTIFY_WHEN, **d(CONF_NOTIFY_WHEN, DEFAULT_NOTIFY_WHEN)): selector.SelectSelector(
                selector.SelectSelectorConfig(
                    options=NOTIFY_KINDS, multiple=True, mode=selector.SelectSelectorMode.LIST, translation_key="notify_when"
                )
            ),
            vol.Optional(CONF_AI_TASK_ENTITY, **d(CONF_AI_TASK_ENTITY)): selector.EntitySelector(
                selector.EntitySelectorConfig(domain="ai_task")
            ),
            vol.Optional(CONF_SECOND_OPINION, **d(CONF_SECOND_OPINION)): selector.EntitySelector(
                selector.EntitySelectorConfig(domain="ai_task")
            ),
            vol.Required(CONF_CAMERA_COOLDOWN, **d(CONF_CAMERA_COOLDOWN, DEFAULT_CAMERA_COOLDOWN)): selector.NumberSelector(
                selector.NumberSelectorConfig(min=10, max=3600, step=10, unit_of_measurement="s", mode=selector.NumberSelectorMode.BOX)
            ),
            vol.Required(CONF_DAILY_AI_LIMIT, **d(CONF_DAILY_AI_LIMIT, DEFAULT_DAILY_AI_LIMIT)): selector.NumberSelector(
                selector.NumberSelectorConfig(min=0, max=5000, step=10, mode=selector.NumberSelectorMode.BOX)
            ),
            vol.Required(CONF_PROMPT_MEMORIES, **d(CONF_PROMPT_MEMORIES, DEFAULT_PROMPT_MEMORIES)): selector.NumberSelector(
                selector.NumberSelectorConfig(min=0, max=100, step=1, mode=selector.NumberSelectorMode.BOX)
            ),
        }
    )


def _briefing_schema(current: dict[str, Any]) -> vol.Schema:
    def d(key: str, fallback: Any = None) -> dict[str, Any]:
        value = current.get(key, fallback)
        return {} if value is None else {"default": value}

    return vol.Schema(
        {
            vol.Required(CONF_BRIEFINGS, **d(CONF_BRIEFINGS, True)): selector.BooleanSelector(),
            vol.Required(CONF_BRIEFING_SECTIONS, **d(CONF_BRIEFING_SECTIONS, DEFAULT_BRIEFING_SECTIONS)): selector.SelectSelector(
                selector.SelectSelectorConfig(
                    options=BRIEFING_SECTIONS, multiple=True, mode=selector.SelectSelectorMode.LIST,
                    translation_key="briefing_section",
                )
            ),
            vol.Optional(CONF_BRIEFING_WEATHER, **d(CONF_BRIEFING_WEATHER)): selector.EntitySelector(
                selector.EntitySelectorConfig(domain="weather")
            ),
            vol.Optional(CONF_BRIEFING_TODO, **d(CONF_BRIEFING_TODO, [])): selector.EntitySelector(
                selector.EntitySelectorConfig(domain="todo", multiple=True)
            ),
            vol.Optional(CONF_BRIEFING_CALENDARS, **d(CONF_BRIEFING_CALENDARS, [])): selector.EntitySelector(
                selector.EntitySelectorConfig(domain="calendar", multiple=True)
            ),
            vol.Optional(CONF_BRIEFING_SYSTEM, **d(CONF_BRIEFING_SYSTEM, [])): selector.EntitySelector(
                selector.EntitySelectorConfig(domain=["sensor", "binary_sensor"], multiple=True)
            ),
            vol.Optional(CONF_BRIEFING_TIME, **d(CONF_BRIEFING_TIME)): selector.TimeSelector(),
            vol.Required(CONF_MORNING_BRIEFING, **d(CONF_MORNING_BRIEFING, False)): selector.BooleanSelector(),
            vol.Optional(CONF_DIGEST_TIME, **d(CONF_DIGEST_TIME)): selector.TimeSelector(),
        }
    )


class SentientConfigFlow(ConfigFlow, domain=DOMAIN):
    """First-time setup: a name, then how the house should speak."""

    VERSION = 1

    def __init__(self) -> None:
        self._data: dict[str, Any] = {}

    async def async_step_user(self, user_input: dict[str, Any] | None = None) -> ConfigFlowResult:
        await self.async_set_unique_id(DOMAIN)
        self._abort_if_unique_id_configured()
        if user_input is not None:
            self._data[CONF_PERSONA] = user_input[CONF_PERSONA].strip() or DEFAULT_PERSONA
            return await self.async_step_speech()
        return self.async_show_form(
            step_id="user",
            data_schema=vol.Schema({vol.Required(CONF_PERSONA, default=DEFAULT_PERSONA): selector.TextSelector()}),
        )

    async def async_step_speech(self, user_input: dict[str, Any] | None = None) -> ConfigFlowResult:
        if user_input is not None:
            return self.async_create_entry(title=self._data[CONF_PERSONA], data={**self._data, **user_input})
        return self.async_show_form(step_id="speech", data_schema=_speech_schema({}))

    @staticmethod
    @callback
    def async_get_options_flow(config_entry: ConfigEntry) -> OptionsFlow:
        return SentientOptionsFlow()


def _memory_label(memory) -> str:
    text = memory.text if len(memory.text) <= 90 else memory.text[:87] + "..."
    return f"{text}  ({memory.category})"


_CATEGORY = selector.SelectSelector(
    selector.SelectSelectorConfig(options=MEMORY_CATEGORIES, translation_key="category")
)


class SentientOptionsFlow(OptionsFlow):
    """Settings, plus a simple manager for what the house remembers."""

    def __init__(self) -> None:
        self._editing: str | None = None

    async def async_step_init(self, user_input: dict[str, Any] | None = None) -> ConfigFlowResult:
        return self.async_show_menu(step_id="init", menu_options=["settings", "briefing", "memories"])

    def _save_page(self, schema: vol.Schema, user_input: dict[str, Any]) -> ConfigFlowResult:
        """Update only this page's settings; a field left empty is cleared."""
        options = {**self.config_entry.data, **self.config_entry.options}
        for key in schema.schema:
            options.pop(str(key), None)
        options.update(user_input)
        return self.async_create_entry(data=options)

    async def async_step_briefing(self, user_input: dict[str, Any] | None = None) -> ConfigFlowResult:
        current = {**self.config_entry.data, **self.config_entry.options}
        schema = _briefing_schema(current)
        if user_input is not None:
            return self._save_page(schema, user_input)
        return self.async_show_form(step_id="briefing", data_schema=schema)

    async def async_step_settings(self, user_input: dict[str, Any] | None = None) -> ConfigFlowResult:
        current = {**self.config_entry.data, **self.config_entry.options}
        schema = vol.Schema(
            {vol.Required(CONF_PERSONA, default=current.get(CONF_PERSONA, DEFAULT_PERSONA)): selector.TextSelector()}
        ).extend(_speech_schema(current).schema)
        if user_input is not None:
            return self._save_page(schema, user_input)
        return self.async_show_form(step_id="settings", data_schema=schema)

    def _keep_options(self) -> ConfigFlowResult:
        return self.async_create_entry(data=dict(self.config_entry.options))

    async def async_step_memories(self, user_input: dict[str, Any] | None = None) -> ConfigFlowResult:
        memory = self.config_entry.runtime_data.memory
        if user_input is not None:
            for memory_id in user_input.get("forget") or []:
                memory.forget(memory_id)
            if (text := (user_input.get("add") or "").strip()):
                memory.add(text, user_input.get("category", "fact"), source="settings")
            if (edit := user_input.get("edit")) and memory.get(edit):
                self._editing = edit
                return await self.async_step_edit_memory()
            return self._keep_options()

        items = sorted(memory.items, key=lambda m: m.created, reverse=True)
        choices = [selector.SelectOptionDict(value=m.id, label=_memory_label(m)) for m in items]
        fields: dict[Any, Any] = {}
        if choices:
            fields[vol.Optional("edit")] = selector.SelectSelector(
                selector.SelectSelectorConfig(options=choices, mode=selector.SelectSelectorMode.DROPDOWN)
            )
            fields[vol.Optional("forget", default=[])] = selector.SelectSelector(
                selector.SelectSelectorConfig(options=choices, multiple=True, mode=selector.SelectSelectorMode.LIST)
            )
        fields[vol.Optional("add")] = selector.TextSelector()
        fields[vol.Optional("category", default="fact")] = _CATEGORY
        return self.async_show_form(
            step_id="memories",
            data_schema=vol.Schema(fields),
            description_placeholders={"count": str(len(items))},
        )

    async def async_step_edit_memory(self, user_input: dict[str, Any] | None = None) -> ConfigFlowResult:
        memory = self.config_entry.runtime_data.memory
        item = memory.get(self._editing or "")
        if item is None:
            return self._keep_options()
        if user_input is not None:
            memory.update(item.id, user_input["text"], user_input.get("category"))
            return self._keep_options()
        return self.async_show_form(
            step_id="edit_memory",
            data_schema=vol.Schema(
                {
                    vol.Required("text", default=item.text): selector.TextSelector(
                        selector.TextSelectorConfig(multiline=True)
                    ),
                    vol.Required("category", default=item.category): _CATEGORY,
                }
            ),
        )
