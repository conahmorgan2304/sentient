"""Setup wizard for Sentient."""

from __future__ import annotations

from typing import Any

import voluptuous as vol

from homeassistant.config_entries import ConfigEntry, ConfigFlow, ConfigFlowResult, OptionsFlow
from homeassistant.core import callback
from homeassistant.helpers import selector

from .const import CONF_UPDATE_CHECKER
from .const import CONF_ALEXA_TTS_ONLY
from .const import CONF_LIVE_VOICE_CHECK, CONF_SPEECH_DELIVERY_HISTORY
from .followups import CONF_CONTEXTUAL_FOLLOWUPS
from .const import CONF_RECOVERY
from .household_tasks import CONF_BEDTIME, CONF_BEDTIME_LIGHTS, CONF_BEDTIME_CONTACTS
from .incidents import CONF_COVERAGE
from .frigate_health import CONF_FRIGATE_HEALTH
from .topics import CONF_ROOM_HANDOFF
from .sessions import CONF_SESSIONS, CONF_SESSION_SECONDS, CONF_GUARDED, CONF_UNDO, CONF_EVIDENCE, CONF_INCIDENTS
from .security_events import CONF_EVENT_EVIDENCE, CONF_EVENT_CONTEXT, CONF_EVENT_FEEDBACK
from .clip_analysis import CONF_SHARED_CLIPS

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
    CONF_STYLE_FEEDBACK,
    CONF_SUGGESTIONS,
    CONF_WHATS_NEW,
    CONF_SIMPLE,
    CONF_EVENT_CLIPS,
    CONF_KNOWN_CARS,
    CONF_RELEVANT_MEMORIES,
    CONF_RULES,
    CONF_FACES,
    CONF_FACE_PEOPLE,
    CONF_FACE_TEACH,
    CONF_FACE_CAMERAS,
    CONF_FACE_FORGET_HOURS,
    CONF_CLIP_WATCH,
    CONF_CLIP_CAMERAS,
    CONF_CLIP_SCOPE,
    CONF_CLIP_WHEN,
    CONF_CLIP_NOTIFY,
    CONF_CLIP_SOURCE,
    CONF_CLIP_WHOSE,
    CONF_CLIP_LOOKS,
    CONF_CLIP_DAYS,
    CONF_CLIP_KEEP,
    CONF_BUDGET_CLIPS,
    CLIP_NOTIFY_MODES,
    CLIP_SCOPES,
    CLIP_SOURCES,
    CLIP_WHENS,
    DEFAULT_BUDGET_CLIPS,
    CONF_FACE_TEACH_CAMERAS,
    CONF_SMART_AI,
    CONF_PHONE_BUTTONS,
    CONF_PHONE_APPS,
    PHONE_BUTTON_KINDS,
    CONF_BINS_CHECK,
    CONF_BINS_SPOT,
    DEFAULT_BINS_SPOT,
    CONF_BINS_CALENDAR,
    CONF_BINS_TIME,
    DEFAULT_BINS_TIME,
    CONF_BATTERY_FORECAST,
    CONF_AUTO_BACKUP,
    CONF_BACKUP_KEEP,
    DEFAULT_BACKUP_KEEP,
    CONF_SELF_TEST,
    SELF_TEST_MODES,
    CONF_RAIN,
    CONF_RAIN_ENTITIES,
    CONF_HUTCH_CHECK,
    CONF_HUTCH_SPOT,
    DEFAULT_HUTCH_SPOT,
    CONF_CAT_WATCH,
    CONF_CAT_SENSORS,
    CONF_CAT_LOUD,
    CONF_CONFIRM,
    CONFIRM_KINDS,
    CONF_STYLE_NOTES,
    DEFAULT_PERSONALITY,
    PERSONALITIES,
    CONF_WARM_BY_WEEKDAYS,
    CONF_WARM_BY_WEEKENDS,
    CONF_HEATING_ENTITIES,
    CONF_HEATING_LIMIT,
    CONF_HEATING_NIGHT_TEMP,
    CONF_HEATING_REVERT,
    CONF_HEATING_REVERT_DAY,
    CONF_HEATING_REVERT_NIGHT,
    DEFAULT_HEATING_LIMIT,
    DEFAULT_HEATING_NIGHT_TEMP,
    DEFAULT_HEATING_REVERT_DAY,
    DEFAULT_HEATING_REVERT_NIGHT,
    CONF_INSIDE_SENSORS,
    CONF_NIGHT_WATCH,
    CONF_NIGHT_WATCH_ANNOUNCE,
    CONF_NIGHT_WATCH_ENTITIES,
    CONF_NIGHT_WATCH_SPEAKERS,
    CONF_HEATING_SCHEDULE,
    CONF_HEATING_GRAPH,
    CONF_ROOMS_VIEW,
    CONF_OUTDOOR_AREAS,
    CONF_KNOWLEDGE,
    CONF_DAY_CHART,
    CONF_PREHEAT,
    CONF_WEEKLY_REPORT,
    DEFAULT_NIGHT_WATCH,
    DEFAULT_WEEKLY_REPORT,
    CONF_BATTERY_THRESHOLD,
    CONF_HEALTH_IGNORE,
    CONF_HIDE_SUBSTREAMS,
    CONF_IGNORE_CAMERAS,
    CONF_NEVER_ENTITIES,
    CONF_SELF_CHECK,
    CONF_LEARN_CORRECTIONS,
    CONF_NORMAL_DAY,
    CONF_THOUGHTS,
    CONF_SHARE_LEARNING,
    CONF_SELF_ADJUST,
    CONF_PROPOSALS,
    CONF_PRIVACY_LOG,
    CONF_MANUALS,
    CONF_MANUALS_FOLDER,
    CONF_MANUALS_LOCAL,
    DEFAULT_MANUALS_FOLDER,
    CONF_GOALS,
    CONF_BOOKS,
    CONF_BOOKS_FOLDER,
    CONF_GAMES,
    CONF_VOICE_ID,
    CONF_VOICE_ID_UNKNOWN,
    CONF_READING_TTS,
    CONF_READING_VOICE,
    CONF_READ_AHEAD,
    DEFAULT_BOOKS_FOLDER,
    CONF_RECIPES,
    CONF_RECIPES_FOLDER,
    DEFAULT_RECIPES_FOLDER,
    CONF_RECIPES_LIST,
    CONF_TANDOOR_URL,
    CONF_TANDOOR_TOKEN,
    DEFAULT_BATTERY_THRESHOLD,
    CONF_CHAT_AGENT,
    CONF_VOICE_AGENT,
    CONF_BACKUP_AGENT,
    DEFAULT_BACKUP_AGENT,
    CONF_ANIMAL_SENSORS,
    CONF_AWAY_LIGHTS_ON,
    CONF_AWAY_PEOPLE,
    CONF_HOLIDAY,
    CONF_HOLIDAY_END_ON_RETURN,
    CONF_HOLIDAY_FROST,
    CONF_HOLIDAY_THERMOSTATS,
    DEFAULT_HOLIDAY_FROST,
    CONF_AWAY_LIGHTS,
    CONF_AWAY_AFTER,
    DEFAULT_AWAY_AFTER,
    CONF_SCREEN,
    CONF_SCREEN_ACTIONS,
    CONF_SCREEN_CLEAR_ACTIONS,
    CONF_ANIMAL_LOW,
    CONF_ANIMAL_HIGH,
    DEFAULT_ANIMAL_LOW,
    DEFAULT_ANIMAL_HIGH,
    CONF_PAGE_ADMIN_ONLY,
    CONF_SIDEBAR,
    CONF_VOICE_UNLOCK,
    DEFAULT_SIDEBAR,
    BRIEFING_SECTIONS,
    CONF_BRIEFING_CALENDARS,
    CONF_BRIEFING_SECTIONS,
    CONF_BRIEFING_SYSTEM,
    CONF_OUTSIDE_INFO,
    CONF_PARCEL_TRACKERS,
    CONF_BRIEFING_TIME,
    CONF_BRIEFING_TODO,
    CONF_BRIEFING_WEATHER,
    CONF_BRIEFINGS,
    DEFAULT_BRIEFING_SECTIONS,
    CONF_FACE_SENSOR,
    CONF_DOORSTEP_SENSORS,
    CONF_ZONE_DOOR_SENSORS,
    CONF_ZONE_VISITS,
    CONF_ANNOUNCE_VISITORS,
    CONF_DOOR_CAMERA,
    CONF_DOOR_TRIGGERS,
    CONF_PARCEL_ALERTS,
    DEFAULT_ANNOUNCE_VISITORS,
    DEFAULT_PARCEL_ALERTS,
    CONF_LEARN_ENTITIES,
    CONF_LEARN_ANCHORS,
    CONF_TRUST_COVERS,
    CONF_TRUST_HEATING,
    CONF_TRUST_LIGHTS,
    CONF_TRUST_SWITCHES,
    TRUST_LEVELS,
    CONF_OFFER_TOGETHER,
    CONF_SUGGEST_ROUTINES,
    CONF_LINK_TRIGGERS,
    DEFAULT_SUGGEST_ROUTINES,
    CHATTINESS_LIMITS,
    CONF_CHATTINESS,
    CONF_TALK_STYLE,
    TALK_STYLES,
    DEFAULT_TALK_STYLE,
    CONF_CARRY_ON_MINUTES,
    DEFAULT_CARRY_ON_MINUTES,
    MAX_CARRY_ON_MINUTES,
    CONF_FOCUSED_TOOLS,
    CONF_ASK_LATER,
    CONF_PUT_RIGHT,
    CONF_MUSIC,
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
    CONF_MORNING_BRIEFING_AREAS,
    CONF_WELCOME,
    CONF_SPEAK_VOLUME,
    CONF_NIGHT_VOLUME,
    CONF_DUCK_MEDIA,
    CONF_ROOM_VOLUME,
    CONF_FULL_VOLUME_SPEAKERS,
    CONF_TTS_ENTITY,
    CONF_REMEMBER_LANGUAGE_PHRASES,
    CONF_ACTION_RECOVERY,
    CONF_SPECIFIC_CLARIFICATION,
    CONF_STREAM_VOICE,
    CONF_VOICE_TIMING_DIAGNOSTICS,
    CONF_AUTOMATIC_LANGUAGE,
    CONF_VOICE_LANGUAGE,
    CONF_SPEAK_HOUSE_LANGUAGE,
    CONF_MEDIA_SPEAKERS,
    CONF_MEDIA_IMPORTANT_ONLY,
    CONF_SLEEP,
    CONF_SLEEP_MINUTES,
    CONF_SLEEP_IGNORE,
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
    CONF_CAMERA_WAKE,
    CONF_CAMERA_WAKE_CAMERAS,
    CONF_CAMERA_WAKE_SECONDS,
    DEFAULT_CAMERA_WAKE_SECONDS,
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
    CONF_ENERGY_CURRENCY,
    CONF_PRICE_ENTITY,
    CONF_SEARCH_HINTS,
    CONF_REMARK_REPEAT_HOURS,
    DEFAULT_REMARK_REPEAT_HOURS,
    CONF_WEB_SEARCH,
    CONF_SEARXNG_URL,
    CONF_ALEXA_SKILL,
    CONF_ALEXA_WAIT,
    CONF_ALEXA_LATE,
    DEFAULT_ALEXA_WAIT,
    CURRENCIES,
    CONF_CRITICAL_IGNORE,
    CONF_CRITICAL_EXTRA,
    CONF_WELCOME_MIN_AWAY,
    CONF_WELCOME_PEOPLE,
    CONF_WELCOME_UNTIL,
    DEFAULT_WELCOME_MIN_AWAY,
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
    CONF_PROMPT_RECENT,
    CONF_LOOK_CACHE,
    DEFAULT_LOOK_CACHE,
    CONF_CAMERA_PREFILTER,
    CONF_BUDGET_DRIVE,
    CONF_BUDGET_NOTICING,
    CONF_BUDGET_CAMERAS,
    CONF_ROUTINE_EVIDENCE,
    CONF_QUIET_END,
    CONF_BUSY_ENTITIES,
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


def _warning_countries() -> list[selector.SelectOptionDict]:
    from .warnings_feed import countries

    return [selector.SelectOptionDict(value=c["value"], label=c["label"]) for c in countries()]


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
            vol.Required(CONF_PAGE_ADMIN_ONLY, **d(CONF_PAGE_ADMIN_ONLY, False)): selector.BooleanSelector(),
            vol.Required(CONF_SUGGESTIONS, **d(CONF_SUGGESTIONS, True)): selector.BooleanSelector(),
            vol.Required(CONF_WHATS_NEW, **d(CONF_WHATS_NEW, True)): selector.BooleanSelector(),
            vol.Required(CONF_SIMPLE, **d(CONF_SIMPLE, False)): selector.BooleanSelector(),
            vol.Required(CONF_AUTO_BACKUP, **d(CONF_AUTO_BACKUP, False)): selector.BooleanSelector(),
            vol.Required(CONF_BACKUP_KEEP, **d(CONF_BACKUP_KEEP, DEFAULT_BACKUP_KEEP)): selector.NumberSelector(
                selector.NumberSelectorConfig(min=1, max=20, step=1, mode=selector.NumberSelectorMode.BOX)
            ),
            vol.Optional(CONF_VOICE_AGENT, **d(CONF_VOICE_AGENT)): selector.EntitySelector(
                selector.EntitySelectorConfig(domain="conversation")
            ),
            vol.Optional(CONF_BACKUP_AGENT, **d(CONF_BACKUP_AGENT, DEFAULT_BACKUP_AGENT)): selector.EntitySelector(
                selector.EntitySelectorConfig(domain="conversation")
            ),
            vol.Required("offline_answers", **d("offline_answers", True)): selector.BooleanSelector(),
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
            vol.Required(CONF_HUTCH_CHECK, **d(CONF_HUTCH_CHECK, False)): selector.BooleanSelector(),
            vol.Optional(CONF_HUTCH_SPOT, **d(CONF_HUTCH_SPOT, DEFAULT_HUTCH_SPOT)): selector.TextSelector(),
            vol.Required(CONF_CAT_WATCH, **d(CONF_CAT_WATCH, False)): selector.BooleanSelector(),
            vol.Optional(CONF_CAT_SENSORS, **d(CONF_CAT_SENSORS, [])): selector.EntitySelector(
                selector.EntitySelectorConfig(domain="binary_sensor", multiple=True)
            ),
            vol.Required(CONF_CAT_LOUD, **d(CONF_CAT_LOUD, False)): selector.BooleanSelector(),
            vol.Required(CONF_RAIN, **d(CONF_RAIN, False)): selector.BooleanSelector(),
            vol.Optional(CONF_RAIN_ENTITIES, **d(CONF_RAIN_ENTITIES, [])): selector.EntitySelector(
                selector.EntitySelectorConfig(domain="binary_sensor", multiple=True)
            ),
            vol.Required(CONF_UPDATE_CHECKER, **d(CONF_UPDATE_CHECKER, False)): selector.BooleanSelector(),
            vol.Required(CONF_SELF_TEST, **d(CONF_SELF_TEST, "off")): selector.SelectSelector(
                selector.SelectSelectorConfig(options=SELF_TEST_MODES, translation_key="self_test")
            ),
            vol.Required(CONF_AWAY_LIGHTS_ON, **d(CONF_AWAY_LIGHTS_ON, False)): selector.BooleanSelector(),
            vol.Optional(CONF_AWAY_PEOPLE, **d(CONF_AWAY_PEOPLE, [])): selector.EntitySelector(
                selector.EntitySelectorConfig(domain=["person", "device_tracker"], multiple=True)
            ),
            vol.Optional(CONF_AWAY_LIGHTS, **d(CONF_AWAY_LIGHTS, [])): selector.EntitySelector(
                selector.EntitySelectorConfig(domain="light", multiple=True)
            ),
            vol.Required(CONF_AWAY_AFTER, **d(CONF_AWAY_AFTER, DEFAULT_AWAY_AFTER)): selector.NumberSelector(
                selector.NumberSelectorConfig(min=0, max=240, step=5, unit_of_measurement="min", mode=selector.NumberSelectorMode.BOX)
            ),
            vol.Required(CONF_HOLIDAY, **d(CONF_HOLIDAY, True)): selector.BooleanSelector(),
            vol.Optional(CONF_HOLIDAY_THERMOSTATS, **d(CONF_HOLIDAY_THERMOSTATS, [])): selector.EntitySelector(
                selector.EntitySelectorConfig(domain="climate", multiple=True)
            ),
            vol.Required(CONF_HOLIDAY_FROST, **d(CONF_HOLIDAY_FROST, DEFAULT_HOLIDAY_FROST)): selector.NumberSelector(
                selector.NumberSelectorConfig(min=5, max=15, step=0.5, unit_of_measurement="°", mode=selector.NumberSelectorMode.BOX)
            ),
            vol.Required(CONF_HOLIDAY_END_ON_RETURN, **d(CONF_HOLIDAY_END_ON_RETURN, True)): selector.BooleanSelector(),
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
            vol.Required(CONF_ALEXA_TTS_ONLY, **d(CONF_ALEXA_TTS_ONLY, False)): selector.BooleanSelector(),
            vol.Required(CONF_MEDIA_SPEAKERS, **d(CONF_MEDIA_SPEAKERS, True)): selector.BooleanSelector(),
            vol.Required(CONF_MEDIA_IMPORTANT_ONLY, **d(CONF_MEDIA_IMPORTANT_ONLY, False)): selector.BooleanSelector(),
            vol.Optional(CONF_TTS_ENTITY, **d(CONF_TTS_ENTITY)): selector.EntitySelector(
                selector.EntitySelectorConfig(domain="tts")
            ),
            vol.Required(CONF_SPEAK_HOUSE_LANGUAGE, **d(CONF_SPEAK_HOUSE_LANGUAGE, False)): selector.BooleanSelector(),
            vol.Required(CONF_AUTOMATIC_LANGUAGE, **d(CONF_AUTOMATIC_LANGUAGE, False)): selector.BooleanSelector(),
            vol.Required(CONF_REMEMBER_LANGUAGE_PHRASES, **d(CONF_REMEMBER_LANGUAGE_PHRASES, False)): selector.BooleanSelector(),
            vol.Required(CONF_STREAM_VOICE, **d(CONF_STREAM_VOICE, False)): selector.BooleanSelector(),
            vol.Required(CONF_VOICE_TIMING_DIAGNOSTICS, **d(CONF_VOICE_TIMING_DIAGNOSTICS, False)): selector.BooleanSelector(),
            vol.Required(CONF_SPECIFIC_CLARIFICATION, **d(CONF_SPECIFIC_CLARIFICATION, False)): selector.BooleanSelector(),
            vol.Required(CONF_ACTION_RECOVERY, **d(CONF_ACTION_RECOVERY, False)): selector.BooleanSelector(),
            vol.Required("recheck_unconfirmed", **d("recheck_unconfirmed", True)): selector.BooleanSelector(),
            vol.Optional(CONF_VOICE_LANGUAGE, **d(CONF_VOICE_LANGUAGE, "auto")): selector.SelectSelector(
                selector.SelectSelectorConfig(options=["auto", "en", "es", "fr", "de", "pt", "it", "nl", "pl", "fi", "sv", "da", "nb", "tr", "ru", "uk", "ar", "hi", "bn", "zh", "ja", "ko", "id", "vi", "th", "he", "el", "cs", "ro", "hu"], custom_value=True)
            ),
            vol.Required(CONF_FOLLOW_YOU, **d(CONF_FOLLOW_YOU, True)): selector.BooleanSelector(),
            vol.Required(CONF_FOLLOW_MINUTES, **d(CONF_FOLLOW_MINUTES, DEFAULT_FOLLOW_MINUTES)): selector.NumberSelector(
                selector.NumberSelectorConfig(min=5, max=720, step=5, unit_of_measurement="min", mode=selector.NumberSelectorMode.BOX)
            ),
            vol.Required(CONF_LIVE_VOICE_CHECK, **d(CONF_LIVE_VOICE_CHECK, True)): selector.BooleanSelector(),
            vol.Required(CONF_SPEECH_DELIVERY_HISTORY, **d(CONF_SPEECH_DELIVERY_HISTORY, True)): selector.BooleanSelector(),
            vol.Required("speech_failure_notification", **d("speech_failure_notification", False)): selector.BooleanSelector(),
            vol.Required(CONF_SESSIONS, **d(CONF_SESSIONS, False)): selector.BooleanSelector(),
            vol.Required(CONF_ROOM_HANDOFF, **d(CONF_ROOM_HANDOFF, False)): selector.BooleanSelector(),
            vol.Required(CONF_BEDTIME, **d(CONF_BEDTIME, False)): selector.BooleanSelector(),
            vol.Optional(CONF_BEDTIME_LIGHTS, **d(CONF_BEDTIME_LIGHTS, [])): selector.EntitySelector(
                selector.EntitySelectorConfig(domain="light", multiple=True)
            ),
            vol.Optional(CONF_BEDTIME_CONTACTS, **d(CONF_BEDTIME_CONTACTS, [])): selector.EntitySelector(
                selector.EntitySelectorConfig(domain="binary_sensor", device_class=["door", "window", "garage_door", "opening"], multiple=True)
            ),
            vol.Required(CONF_CONTEXTUAL_FOLLOWUPS, **d(CONF_CONTEXTUAL_FOLLOWUPS, False)): selector.BooleanSelector(),
            vol.Required("recent_followup_context", **d("recent_followup_context", False)): selector.BooleanSelector(),
            vol.Required("conversation_cues", **d("conversation_cues", True)): selector.BooleanSelector(),
            vol.Required("behaviour_explanations", **d("behaviour_explanations", False)): selector.BooleanSelector(),
            vol.Required("current_house_context", **d("current_house_context", False)): selector.BooleanSelector(),
            vol.Optional("house_context_entities", **d("house_context_entities", [])): selector.EntitySelector(selector.EntitySelectorConfig(multiple=True)),
            vol.Required("house_context_updates", **d("house_context_updates", False)): selector.BooleanSelector(),
            vol.Required(CONF_RECOVERY, **d(CONF_RECOVERY, False)): selector.BooleanSelector(),
            vol.Required(CONF_GUARDED, **d(CONF_GUARDED, False)): selector.BooleanSelector(),
            vol.Required(CONF_VOICE_UNLOCK, **d(CONF_VOICE_UNLOCK, False)): selector.BooleanSelector(),
            vol.Required(CONF_UNDO, **d(CONF_UNDO, False)): selector.BooleanSelector(),
            vol.Required(CONF_EVIDENCE, **d(CONF_EVIDENCE, False)): selector.BooleanSelector(),
            vol.Required(CONF_INCIDENTS, **d(CONF_INCIDENTS, False)): selector.BooleanSelector(),
            vol.Required(CONF_EVENT_EVIDENCE, **d(CONF_EVENT_EVIDENCE, False)): selector.BooleanSelector(),
            vol.Required(CONF_COVERAGE, **d(CONF_COVERAGE, False)): selector.BooleanSelector(),
            vol.Required(CONF_FRIGATE_HEALTH, **d(CONF_FRIGATE_HEALTH, False)): selector.BooleanSelector(),
            vol.Required(CONF_EVENT_CONTEXT, **d(CONF_EVENT_CONTEXT, False)): selector.BooleanSelector(),
            vol.Required(CONF_EVENT_FEEDBACK, **d(CONF_EVENT_FEEDBACK, False)): selector.BooleanSelector(),
            vol.Required(CONF_SHARED_CLIPS, **d(CONF_SHARED_CLIPS, False)): selector.BooleanSelector(),
            vol.Required(CONF_SESSION_SECONDS, **d(CONF_SESSION_SECONDS, 60)): selector.NumberSelector(
                selector.NumberSelectorConfig(min=15, max=300, step=15, unit_of_measurement="s", mode=selector.NumberSelectorMode.BOX)
            ),
            vol.Required(CONF_CONVERSATIONS, **d(CONF_CONVERSATIONS, True)): selector.BooleanSelector(),
            vol.Required(CONF_FOLLOW_UPS, **d(CONF_FOLLOW_UPS, True)): selector.BooleanSelector(),
            vol.Required(CONF_CURIOSITY, **d(CONF_CURIOSITY, True)): selector.BooleanSelector(),
            vol.Required(CONF_REFLECTION, **d(CONF_REFLECTION, True)): selector.BooleanSelector(),
            vol.Required(CONF_KNOWLEDGE, **d(CONF_KNOWLEDGE, True)): selector.BooleanSelector(),
            vol.Required(CONF_DAY_CHART, **d(CONF_DAY_CHART, True)): selector.BooleanSelector(),
            vol.Required(CONF_REFLECTION_TIME, **d(CONF_REFLECTION_TIME, DEFAULT_REFLECTION_TIME)): selector.TimeSelector(),
            vol.Required(CONF_SLEEP, **d(CONF_SLEEP, True)): selector.BooleanSelector(),
            vol.Required(CONF_SLEEP_MINUTES, **d(CONF_SLEEP_MINUTES, DEFAULT_SLEEP_MINUTES)): selector.NumberSelector(
                selector.NumberSelectorConfig(min=5, max=120, step=5, unit_of_measurement="min", mode=selector.NumberSelectorMode.BOX)
            ),
            vol.Optional(CONF_SLEEP_IGNORE, **d(CONF_SLEEP_IGNORE, [])): selector.EntitySelector(
                selector.EntitySelectorConfig(domain="binary_sensor", multiple=True)
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
            vol.Optional(CONF_KNOWN_CARS, **d(CONF_KNOWN_CARS, "")): selector.TextSelector(
                selector.TextSelectorConfig(multiline=True)
            ),
            vol.Optional(CONF_CAMERA_WAKE, **d(CONF_CAMERA_WAKE, [])): selector.ActionSelector(),
            vol.Optional(CONF_CAMERA_WAKE_CAMERAS, **d(CONF_CAMERA_WAKE_CAMERAS, [])): selector.EntitySelector(
                selector.EntitySelectorConfig(domain="camera", multiple=True)
            ),
            vol.Required(CONF_CAMERA_WAKE_SECONDS, **d(CONF_CAMERA_WAKE_SECONDS, DEFAULT_CAMERA_WAKE_SECONDS)): selector.NumberSelector(
                selector.NumberSelectorConfig(min=0, max=30, step=1, unit_of_measurement="s", mode=selector.NumberSelectorMode.BOX)
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
            vol.Required(CONF_HEATING_SCHEDULE, **d(CONF_HEATING_SCHEDULE, False)): selector.BooleanSelector(),
            vol.Required(CONF_HEATING_GRAPH, **d(CONF_HEATING_GRAPH, True)): selector.BooleanSelector(),
            vol.Required(CONF_HEATING_NIGHT_TEMP, **d(CONF_HEATING_NIGHT_TEMP, DEFAULT_HEATING_NIGHT_TEMP)): selector.NumberSelector(
                selector.NumberSelectorConfig(min=5, max=28, step=0.5, unit_of_measurement="°C", mode=selector.NumberSelectorMode.BOX)
            ),
            vol.Required(CONF_HEATING_REVERT, **d(CONF_HEATING_REVERT, True)): selector.BooleanSelector(),
            vol.Required(CONF_HEATING_REVERT_DAY, **d(CONF_HEATING_REVERT_DAY, DEFAULT_HEATING_REVERT_DAY)): selector.NumberSelector(
                selector.NumberSelectorConfig(min=0.5, max=12, step=0.5, unit_of_measurement="h", mode=selector.NumberSelectorMode.BOX)
            ),
            vol.Required(CONF_HEATING_REVERT_NIGHT, **d(CONF_HEATING_REVERT_NIGHT, DEFAULT_HEATING_REVERT_NIGHT)): selector.NumberSelector(
                selector.NumberSelectorConfig(min=0.5, max=12, step=0.5, unit_of_measurement="h", mode=selector.NumberSelectorMode.BOX)
            ),
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
            vol.Required("security_reasoning", **d("security_reasoning", False)): selector.BooleanSelector(),
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
                selector.NumberSelectorConfig(min=0, max=200, step=0.01, unit_of_measurement="per kWh", mode=selector.NumberSelectorMode.BOX)
            ),
            vol.Optional(CONF_ENERGY_CURRENCY, **d(CONF_ENERGY_CURRENCY)): selector.SelectSelector(
                selector.SelectSelectorConfig(options=CURRENCIES, custom_value=True, mode=selector.SelectSelectorMode.DROPDOWN)
            ),
            vol.Optional(CONF_PRICE_ENTITY, **d(CONF_PRICE_ENTITY)): selector.EntitySelector(
                selector.EntitySelectorConfig(domain=["sensor", "event", "input_number"])
            ),
            vol.Required(CONF_SEARCH_HINTS, **d(CONF_SEARCH_HINTS, True)): selector.BooleanSelector(),
            vol.Required(CONF_WEB_SEARCH, **d(CONF_WEB_SEARCH, False)): selector.BooleanSelector(),
            vol.Optional(CONF_SEARXNG_URL, **d(CONF_SEARXNG_URL)): selector.TextSelector(
                selector.TextSelectorConfig(type=selector.TextSelectorType.URL)
            ),
            vol.Required(CONF_ALEXA_SKILL, **d(CONF_ALEXA_SKILL, False)): selector.BooleanSelector(),
            vol.Required(CONF_ALEXA_WAIT, **d(CONF_ALEXA_WAIT, DEFAULT_ALEXA_WAIT)): selector.NumberSelector(
                selector.NumberSelectorConfig(min=2, max=7, step=0.5, unit_of_measurement="s", mode=selector.NumberSelectorMode.BOX)
            ),
            vol.Required(CONF_ALEXA_LATE, **d(CONF_ALEXA_LATE, True)): selector.BooleanSelector(),
            vol.Optional(CONF_CRITICAL_EXTRA, **d(CONF_CRITICAL_EXTRA, [])): selector.EntitySelector(
                selector.EntitySelectorConfig(domain=["binary_sensor", "input_boolean"], multiple=True)
            ),
            vol.Optional(CONF_CRITICAL_IGNORE, **d(CONF_CRITICAL_IGNORE, [])): selector.EntitySelector(
                selector.EntitySelectorConfig(domain=["binary_sensor", "alarm_control_panel"], multiple=True)
            ),
            vol.Required(CONF_ALL_OCCUPIED, **d(CONF_ALL_OCCUPIED, DEFAULT_ALL_OCCUPIED)): selector.BooleanSelector(),
            vol.Required(CONF_QUIET_START, **d(CONF_QUIET_START, DEFAULT_QUIET_START)): selector.TimeSelector(),
            vol.Required(CONF_QUIET_END, **d(CONF_QUIET_END, DEFAULT_QUIET_END)): selector.TimeSelector(),
            vol.Optional(CONF_BUSY_ENTITIES, **d(CONF_BUSY_ENTITIES, [])): selector.EntitySelector(
                selector.EntitySelectorConfig(domain=["binary_sensor", "input_boolean", "switch", "calendar", "sensor"], multiple=True)
            ),
            vol.Required(CONF_MOTION_WINDOW, **d(CONF_MOTION_WINDOW, DEFAULT_MOTION_WINDOW)): selector.NumberSelector(
                selector.NumberSelectorConfig(min=1, max=60, step=1, unit_of_measurement="min", mode=selector.NumberSelectorMode.BOX)
            ),
            vol.Required(CONF_ROOMS_VIEW, **d(CONF_ROOMS_VIEW, True)): selector.BooleanSelector(),
            vol.Optional(CONF_OUTDOOR_AREAS, **d(CONF_OUTDOOR_AREAS, [])): selector.AreaSelector(
                selector.AreaSelectorConfig(multiple=True)
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
            vol.Required(CONF_REMARK_REPEAT_HOURS, **d(CONF_REMARK_REPEAT_HOURS, DEFAULT_REMARK_REPEAT_HOURS)): selector.NumberSelector(
                selector.NumberSelectorConfig(min=0, max=24, step=0.5, unit_of_measurement="h", mode=selector.NumberSelectorMode.BOX)
            ),
            vol.Required(CONF_PERSONALITY, **d(CONF_PERSONALITY, DEFAULT_PERSONALITY)): selector.SelectSelector(
                selector.SelectSelectorConfig(options=list(PERSONALITIES), translation_key="personality")
            ),
            vol.Optional(CONF_PERSONALITY_NOTES, **d(CONF_PERSONALITY_NOTES, "")): selector.TextSelector(
                selector.TextSelectorConfig(multiline=True)
            ),
            vol.Required(CONF_STYLE_FEEDBACK, **d(CONF_STYLE_FEEDBACK, True)): selector.BooleanSelector(),
            vol.Optional(CONF_STYLE_NOTES, **d(CONF_STYLE_NOTES, "")): selector.TextSelector(
                selector.TextSelectorConfig(multiline=True)
            ),
            vol.Required(CONF_CHATTINESS, **d(CONF_CHATTINESS, DEFAULT_CHATTINESS)): selector.SelectSelector(
                selector.SelectSelectorConfig(options=list(CHATTINESS_LIMITS), translation_key="chattiness")
            ),
            vol.Required(CONF_TALK_STYLE, **d(CONF_TALK_STYLE, DEFAULT_TALK_STYLE)): selector.SelectSelector(
                selector.SelectSelectorConfig(options=TALK_STYLES, translation_key="conversation_style")
            ),
            vol.Required(CONF_CARRY_ON_MINUTES, **d(CONF_CARRY_ON_MINUTES, DEFAULT_CARRY_ON_MINUTES)): selector.NumberSelector(
                selector.NumberSelectorConfig(min=0, max=MAX_CARRY_ON_MINUTES, step=1, unit_of_measurement="min", mode=selector.NumberSelectorMode.SLIDER)
            ),
            vol.Required(CONF_FOCUSED_TOOLS, **d(CONF_FOCUSED_TOOLS, False)): selector.BooleanSelector(),
            vol.Required(CONF_ASK_LATER, **d(CONF_ASK_LATER, False)): selector.BooleanSelector(),
            vol.Required(CONF_PUT_RIGHT, **d(CONF_PUT_RIGHT, True)): selector.BooleanSelector(),
            vol.Required(CONF_MUSIC, **d(CONF_MUSIC, True)): selector.BooleanSelector(),
            vol.Optional(CONF_HOUSE_RULES, **d(CONF_HOUSE_RULES, "")): selector.TextSelector(
                selector.TextSelectorConfig(multiline=True)
            ),
            vol.Optional(CONF_LEARN_ENTITIES, **d(CONF_LEARN_ENTITIES, [])): selector.EntitySelector(
                selector.EntitySelectorConfig(domain=["light", "switch", "fan", "input_boolean", "cover"], multiple=True)
            ),
            vol.Required(CONF_LEARN_ANCHORS, **d(CONF_LEARN_ANCHORS, True)): selector.BooleanSelector(),
            vol.Required(CONF_OFFER_TOGETHER, **d(CONF_OFFER_TOGETHER, True)): selector.BooleanSelector(),
            **{vol.Required(key, **d(key, "do")): selector.SelectSelector(
                selector.SelectSelectorConfig(options=TRUST_LEVELS, translation_key="trust_level"))
               for key in (CONF_TRUST_LIGHTS, CONF_TRUST_SWITCHES, CONF_TRUST_COVERS, CONF_TRUST_HEATING)},
            vol.Required(CONF_WELCOME, **d(CONF_WELCOME, False)): selector.BooleanSelector(),
            vol.Optional(CONF_WELCOME_PEOPLE, **d(CONF_WELCOME_PEOPLE, [])): selector.EntitySelector(
                selector.EntitySelectorConfig(domain=["person", "device_tracker"], multiple=True)
            ),
            vol.Required(CONF_WELCOME_MIN_AWAY, **d(CONF_WELCOME_MIN_AWAY, DEFAULT_WELCOME_MIN_AWAY)): selector.NumberSelector(
                selector.NumberSelectorConfig(min=5, max=480, step=5, unit_of_measurement="minutes", mode=selector.NumberSelectorMode.BOX)
            ),
            vol.Optional(CONF_WELCOME_UNTIL, **d(CONF_WELCOME_UNTIL)): selector.TimeSelector(),
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
            vol.Required(CONF_FACES, **d(CONF_FACES, False)): selector.BooleanSelector(),
            vol.Optional(CONF_FACE_PEOPLE, **d(CONF_FACE_PEOPLE, [])): selector.TextSelector(
                selector.TextSelectorConfig(multiple=True)
            ),
            vol.Required(CONF_FACE_TEACH, **d(CONF_FACE_TEACH, False)): selector.BooleanSelector(),
            vol.Optional(CONF_FACE_TEACH_CAMERAS, **d(CONF_FACE_TEACH_CAMERAS, [])): selector.EntitySelector(
                selector.EntitySelectorConfig(domain="camera", multiple=True)
            ),
            vol.Optional(CONF_FACE_CAMERAS, **d(CONF_FACE_CAMERAS, [])): selector.EntitySelector(
                selector.EntitySelectorConfig(domain="camera", multiple=True)
            ),
            vol.Required(CONF_FACE_FORGET_HOURS, **d(CONF_FACE_FORGET_HOURS, 0)): selector.NumberSelector(
                selector.NumberSelectorConfig(min=0, max=720, step=1, unit_of_measurement="h", mode=selector.NumberSelectorMode.BOX)
            ),
            vol.Required(CONF_CLIP_WATCH, **d(CONF_CLIP_WATCH, False)): selector.BooleanSelector(),
            vol.Optional(CONF_CLIP_CAMERAS, **d(CONF_CLIP_CAMERAS, [])): selector.EntitySelector(
                selector.EntitySelectorConfig(domain="camera", multiple=True)
            ),
            vol.Required(CONF_CLIP_SCOPE, **d(CONF_CLIP_SCOPE, CLIP_SCOPES[0])): selector.SelectSelector(
                selector.SelectSelectorConfig(options=CLIP_SCOPES, translation_key="clip_scope")
            ),
            vol.Required(CONF_CLIP_WHEN, **d(CONF_CLIP_WHEN, CLIP_WHENS[0])): selector.SelectSelector(
                selector.SelectSelectorConfig(options=CLIP_WHENS, translation_key="clip_when")
            ),
            vol.Required(CONF_CLIP_NOTIFY, **d(CONF_CLIP_NOTIFY, CLIP_NOTIFY_MODES[0])): selector.SelectSelector(
                selector.SelectSelectorConfig(options=CLIP_NOTIFY_MODES, translation_key="clip_notify")
            ),
            vol.Required(CONF_CLIP_SOURCE, **d(CONF_CLIP_SOURCE, CLIP_SOURCES[0])): selector.SelectSelector(
                selector.SelectSelectorConfig(options=CLIP_SOURCES, translation_key="clip_source")
            ),
            vol.Required(CONF_CLIP_WHOSE, **d(CONF_CLIP_WHOSE, False)): selector.BooleanSelector(),
            vol.Optional(CONF_CLIP_LOOKS, **d(CONF_CLIP_LOOKS, "")): selector.TextSelector(
                selector.TextSelectorConfig(multiline=True)
            ),
            vol.Required(CONF_BUDGET_CLIPS, **d(CONF_BUDGET_CLIPS, DEFAULT_BUDGET_CLIPS)): selector.NumberSelector(
                selector.NumberSelectorConfig(min=0, max=500, step=1, mode=selector.NumberSelectorMode.BOX)
            ),
            vol.Required(CONF_CLIP_DAYS, **d(CONF_CLIP_DAYS, 7)): selector.NumberSelector(
                selector.NumberSelectorConfig(min=1, max=60, step=1, unit_of_measurement="days", mode=selector.NumberSelectorMode.BOX)
            ),
            vol.Required(CONF_CLIP_KEEP, **d(CONF_CLIP_KEEP, False)): selector.BooleanSelector(),
            vol.Required(CONF_PARCEL_ALERTS, **d(CONF_PARCEL_ALERTS, DEFAULT_PARCEL_ALERTS)): selector.BooleanSelector(),
            vol.Optional(CONF_DOORSTEP_SENSORS, **d(CONF_DOORSTEP_SENSORS, [])): selector.EntitySelector(
                selector.EntitySelectorConfig(domain="binary_sensor", multiple=True)
            ),
            vol.Required(CONF_ZONE_VISITS, **d(CONF_ZONE_VISITS, False)): selector.BooleanSelector(),
            vol.Optional(CONF_ZONE_DOOR_SENSORS, **d(CONF_ZONE_DOOR_SENSORS, [])): selector.EntitySelector(
                selector.EntitySelectorConfig(domain="binary_sensor", multiple=True)
            ),
            vol.Required(CONF_BATTERY_THRESHOLD, **d(CONF_BATTERY_THRESHOLD, DEFAULT_BATTERY_THRESHOLD)): selector.NumberSelector(
                selector.NumberSelectorConfig(min=0, max=100, step=5, unit_of_measurement="%", mode=selector.NumberSelectorMode.BOX)
            ),
            vol.Required(CONF_BATTERY_FORECAST, **d(CONF_BATTERY_FORECAST, True)): selector.BooleanSelector(),
            vol.Optional(CONF_HEALTH_IGNORE, **d(CONF_HEALTH_IGNORE, [])): selector.EntitySelector(
                selector.EntitySelectorConfig(multiple=True)
            ),
            vol.Required(CONF_HIDE_SUBSTREAMS, **d(CONF_HIDE_SUBSTREAMS, True)): selector.BooleanSelector(),
            vol.Optional(CONF_IGNORE_CAMERAS, **d(CONF_IGNORE_CAMERAS, [])): selector.EntitySelector(
                selector.EntitySelectorConfig(domain="camera", multiple=True)
            ),
            vol.Optional(CONF_NEVER_ENTITIES, **d(CONF_NEVER_ENTITIES, [])): selector.EntitySelector(
                selector.EntitySelectorConfig(multiple=True)
            ),
            vol.Required(CONF_SELF_CHECK, **d(CONF_SELF_CHECK, True)): selector.BooleanSelector(),
            vol.Required(CONF_LEARN_CORRECTIONS, **d(CONF_LEARN_CORRECTIONS, True)): selector.BooleanSelector(),
            vol.Required(CONF_NORMAL_DAY, **d(CONF_NORMAL_DAY, True)): selector.BooleanSelector(),
            vol.Required(CONF_THOUGHTS, **d(CONF_THOUGHTS, True)): selector.BooleanSelector(),
            vol.Required(CONF_SHARE_LEARNING, **d(CONF_SHARE_LEARNING, True)): selector.BooleanSelector(),
            vol.Required(CONF_SELF_ADJUST, **d(CONF_SELF_ADJUST, False)): selector.BooleanSelector(),
            vol.Required(CONF_PROPOSALS, **d(CONF_PROPOSALS, True)): selector.BooleanSelector(),
            vol.Required(CONF_PRIVACY_LOG, **d(CONF_PRIVACY_LOG, True)): selector.BooleanSelector(),
            vol.Required(CONF_GOALS, **d(CONF_GOALS, False)): selector.BooleanSelector(),
            vol.Required(CONF_MANUALS, **d(CONF_MANUALS, False)): selector.BooleanSelector(),
            vol.Optional(CONF_MANUALS_FOLDER, **d(CONF_MANUALS_FOLDER, DEFAULT_MANUALS_FOLDER)): selector.TextSelector(),
            vol.Required(CONF_MANUALS_LOCAL, **d(CONF_MANUALS_LOCAL, True)): selector.BooleanSelector(),
            vol.Required(CONF_GAMES, **d(CONF_GAMES, False)): selector.BooleanSelector(),
            vol.Required(CONF_VOICE_ID, **d(CONF_VOICE_ID, False)): selector.BooleanSelector(),
            vol.Required(CONF_VOICE_ID_UNKNOWN, **d(CONF_VOICE_ID_UNKNOWN, False)): selector.BooleanSelector(),
            vol.Required("speaker_memories", **d("speaker_memories", False)): selector.BooleanSelector(),
            vol.Required(CONF_BOOKS, **d(CONF_BOOKS, False)): selector.BooleanSelector(),
            vol.Optional(CONF_BOOKS_FOLDER, **d(CONF_BOOKS_FOLDER, DEFAULT_BOOKS_FOLDER)): selector.TextSelector(),
            vol.Optional(CONF_READING_TTS, **d(CONF_READING_TTS, None)): selector.EntitySelector(selector.EntitySelectorConfig(domain="tts")),
            vol.Optional(CONF_READING_VOICE, **d(CONF_READING_VOICE, "")): selector.TextSelector(),
            vol.Required(CONF_READ_AHEAD, **d(CONF_READ_AHEAD, True)): selector.BooleanSelector(),
            vol.Required(CONF_RECIPES, **d(CONF_RECIPES, False)): selector.BooleanSelector(),
            vol.Optional(CONF_RECIPES_FOLDER, **d(CONF_RECIPES_FOLDER, DEFAULT_RECIPES_FOLDER)): selector.TextSelector(),
            vol.Optional(CONF_RECIPES_LIST, **d(CONF_RECIPES_LIST, None)): selector.EntitySelector(selector.EntitySelectorConfig(domain="todo")),
            vol.Optional(CONF_TANDOOR_URL, **d(CONF_TANDOOR_URL, "")): selector.TextSelector(selector.TextSelectorConfig(type=selector.TextSelectorType.URL)),
            vol.Optional(CONF_TANDOOR_TOKEN, **d(CONF_TANDOOR_TOKEN, "")): selector.TextSelector(selector.TextSelectorConfig(type=selector.TextSelectorType.PASSWORD)),
            vol.Optional(CONF_NOTIFY_TARGETS, **d(CONF_NOTIFY_TARGETS, [])): selector.EntitySelector(
                selector.EntitySelectorConfig(domain="notify", multiple=True)
            ),
            vol.Optional(CONF_NOTIFY_WHEN, **d(CONF_NOTIFY_WHEN, DEFAULT_NOTIFY_WHEN)): selector.SelectSelector(
                selector.SelectSelectorConfig(
                    options=NOTIFY_KINDS, multiple=True, mode=selector.SelectSelectorMode.LIST, translation_key="notify_when"
                )
            ),
            vol.Optional(CONF_PHONE_BUTTONS, **d(CONF_PHONE_BUTTONS, [])): selector.SelectSelector(
                selector.SelectSelectorConfig(
                    options=PHONE_BUTTON_KINDS, multiple=True, mode=selector.SelectSelectorMode.LIST, translation_key="phone_buttons"
                )
            ),
            vol.Optional(CONF_PHONE_APPS, **d(CONF_PHONE_APPS, [])): selector.TextSelector(
                selector.TextSelectorConfig(multiple=True)
            ),
            vol.Required(CONF_BINS_CHECK, **d(CONF_BINS_CHECK, False)): selector.BooleanSelector(),
            vol.Optional(CONF_BINS_CALENDAR, **d(CONF_BINS_CALENDAR)): selector.EntitySelector(
                selector.EntitySelectorConfig(domain="calendar")
            ),
            vol.Optional(CONF_BINS_SPOT, **d(CONF_BINS_SPOT, DEFAULT_BINS_SPOT)): selector.TextSelector(),
            vol.Required(CONF_BINS_TIME, **d(CONF_BINS_TIME, DEFAULT_BINS_TIME)): selector.TimeSelector(),
            vol.Optional(CONF_AI_TASK_ENTITY, **d(CONF_AI_TASK_ENTITY)): selector.EntitySelector(
                selector.EntitySelectorConfig(domain="ai_task")
            ),
            vol.Optional(CONF_SECOND_OPINION, **d(CONF_SECOND_OPINION)): selector.EntitySelector(
                selector.EntitySelectorConfig(domain="ai_task")
            ),
            vol.Optional(CONF_SMART_AI, **d(CONF_SMART_AI)): selector.EntitySelector(
                selector.EntitySelectorConfig(domain="ai_task")
            ),
            vol.Required(CONF_EVENT_CLIPS, **d(CONF_EVENT_CLIPS, False)): selector.BooleanSelector(),
            vol.Required(CONF_CAMERA_COOLDOWN, **d(CONF_CAMERA_COOLDOWN, DEFAULT_CAMERA_COOLDOWN)): selector.NumberSelector(
                selector.NumberSelectorConfig(min=10, max=3600, step=10, unit_of_measurement="s", mode=selector.NumberSelectorMode.BOX)
            ),
            vol.Required(CONF_DAILY_AI_LIMIT, **d(CONF_DAILY_AI_LIMIT, DEFAULT_DAILY_AI_LIMIT)): selector.NumberSelector(
                selector.NumberSelectorConfig(min=0, max=5000, step=10, mode=selector.NumberSelectorMode.BOX)
            ),
            vol.Required(CONF_PROMPT_MEMORIES, **d(CONF_PROMPT_MEMORIES, DEFAULT_PROMPT_MEMORIES)): selector.NumberSelector(
                selector.NumberSelectorConfig(min=0, max=100, step=1, mode=selector.NumberSelectorMode.BOX)
            ),
            vol.Required(CONF_PROMPT_RECENT, **d(CONF_PROMPT_RECENT, True)): selector.BooleanSelector(),
            vol.Required(CONF_RULES, **d(CONF_RULES, False)): selector.BooleanSelector(),
            vol.Required(CONF_RELEVANT_MEMORIES, **d(CONF_RELEVANT_MEMORIES, True)): selector.BooleanSelector(),
            vol.Optional(CONF_CONFIRM, **d(CONF_CONFIRM, [])): selector.SelectSelector(
                selector.SelectSelectorConfig(options=list(CONFIRM_KINDS), multiple=True,
                                              mode=selector.SelectSelectorMode.LIST, translation_key="confirm_kinds")
            ),
            vol.Required(CONF_LOOK_CACHE, **d(CONF_LOOK_CACHE, DEFAULT_LOOK_CACHE)): selector.NumberSelector(
                selector.NumberSelectorConfig(min=0, max=30, step=1, unit_of_measurement="min", mode=selector.NumberSelectorMode.BOX)
            ),
            vol.Required(CONF_CAMERA_PREFILTER, **d(CONF_CAMERA_PREFILTER, True)): selector.BooleanSelector(),
            vol.Required(CONF_BUDGET_DRIVE, **d(CONF_BUDGET_DRIVE, 0)): selector.NumberSelector(
                selector.NumberSelectorConfig(min=0, max=500, step=1, mode=selector.NumberSelectorMode.BOX)
            ),
            vol.Required(CONF_BUDGET_NOTICING, **d(CONF_BUDGET_NOTICING, 0)): selector.NumberSelector(
                selector.NumberSelectorConfig(min=0, max=500, step=1, mode=selector.NumberSelectorMode.BOX)
            ),
            vol.Required(CONF_BUDGET_CAMERAS, **d(CONF_BUDGET_CAMERAS, 0)): selector.NumberSelector(
                selector.NumberSelectorConfig(min=0, max=500, step=1, mode=selector.NumberSelectorMode.BOX)
            ),
            vol.Required(CONF_ROUTINE_EVIDENCE, **d(CONF_ROUTINE_EVIDENCE, True)): selector.BooleanSelector(),
            vol.Required("pronounce_names", **d("pronounce_names", True)): selector.BooleanSelector(),
            vol.Optional("pronunciations", **d("pronunciations", "")): selector.TextSelector(
                selector.TextSelectorConfig(multiline=True)
            ),
            vol.Required("health_duplicates", **d("health_duplicates", True)): selector.BooleanSelector(),
            # memory and learning (brain.py)
            vol.Required("house_ledger", **d("house_ledger", True)): selector.BooleanSelector(),
            vol.Required("unexplained_changes", **d("unexplained_changes", True)): selector.BooleanSelector(),
            vol.Optional("unexplained_entities", **d("unexplained_entities", [])): selector.EntitySelector(
                selector.EntitySelectorConfig(multiple=True)
            ),
            vol.Required("expectations", **d("expectations", True)): selector.BooleanSelector(),
            vol.Required("surprise_gating", **d("surprise_gating", "watch")): selector.SelectSelector(
                selector.SelectSelectorConfig(options=["off", "watch", "on"], translation_key="surprise_gating")
            ),
            vol.Required("situations", **d("situations", True)): selector.BooleanSelector(),
            vol.Required("beliefs", **d("beliefs", True)): selector.BooleanSelector(),
            vol.Required("learn_reactions", **d("learn_reactions", True)): selector.BooleanSelector(),
            vol.Required("ai_case_memory", **d("ai_case_memory", True)): selector.BooleanSelector(),
            vol.Required("voice_speed_watchdog", **d("voice_speed_watchdog", True)): selector.BooleanSelector(),
            vol.Required("voice_speed_restart", **d("voice_speed_restart", False)): selector.BooleanSelector(),
            vol.Required("incident_stages", **d("incident_stages", True)): selector.BooleanSelector(),
            # weather and flood warnings (warnings_feed.py)
            vol.Required("weather_warnings", **d("weather_warnings", False)): selector.BooleanSelector(),
            vol.Optional("warnings_country", **d("warnings_country")): selector.SelectSelector(
                selector.SelectSelectorConfig(options=_warning_countries(), mode=selector.SelectSelectorMode.DROPDOWN)
            ),
            vol.Optional("warnings_region", **d("warnings_region", "")): selector.TextSelector(),
            vol.Required("warnings_speak", **d("warnings_speak", True)): selector.BooleanSelector(),
            # extra skills (skills.py)
            vol.Required("email_read", **d("email_read", False)): selector.BooleanSelector(),
            vol.Required("calendar_clashes", **d("calendar_clashes", False)): selector.BooleanSelector(),
            vol.Required("security_audit", **d("security_audit", True)): selector.BooleanSelector(),
            vol.Required("automation_builder", **d("automation_builder", False)): selector.BooleanSelector(),
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
            vol.Optional(CONF_OUTSIDE_INFO, **d(CONF_OUTSIDE_INFO, [])): selector.EntitySelector(
                selector.EntitySelectorConfig(domain=["sensor", "binary_sensor", "event"], multiple=True)
            ),
            vol.Optional(CONF_PARCEL_TRACKERS, **d(CONF_PARCEL_TRACKERS, [])): selector.EntitySelector(
                selector.EntitySelectorConfig(domain=["sensor", "binary_sensor"], multiple=True)
            ),
            vol.Optional(CONF_BRIEFING_TIME, **d(CONF_BRIEFING_TIME)): selector.TimeSelector(),
            vol.Required(CONF_MORNING_BRIEFING, **d(CONF_MORNING_BRIEFING, False)): selector.BooleanSelector(),
            vol.Optional(CONF_MORNING_BRIEFING_AREAS, **d(CONF_MORNING_BRIEFING_AREAS, [])): selector.AreaSelector(selector.AreaSelectorConfig(multiple=True)),
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
            # a new house starts with the simpler settings page (essentials first; everything one tap away)
            return self.async_create_entry(title=self._data[CONF_PERSONA], data={CONF_SIMPLE: True, **self._data, **user_input})
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
