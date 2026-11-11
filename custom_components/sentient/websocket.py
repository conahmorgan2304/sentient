"""Websocket commands behind the sidebar page's Settings tab.

The page draws Home Assistant's own form (the same pickers as the options
flow) from the schema sent here, so every setting has one definition: the
schemas in config_flow.py.
"""

from __future__ import annotations

import logging
import re
import functools
import inspect
from typing import Any

import voluptuous as vol

from homeassistant.components import websocket_api
from homeassistant.util import dt as dt_util
from homeassistant.core import HomeAssistant, callback
from homeassistant.exceptions import HomeAssistantError
from homeassistant.helpers import config_validation as cv
from homeassistant.helpers import selector
from homeassistant.helpers.translation import async_get_translations

from .games import KINDS
from .const import CONF_UPDATE_CHECKER
from .const import CONF_ALEXA_TTS_ONLY
from .const import CONF_LIVE_VOICE_CHECK, CONF_SPEECH_DELIVERY_HISTORY
from .config_flow import _briefing_schema, _speech_schema
from .followups import CONF_CONTEXTUAL_FOLLOWUPS
from .const import CONF_RECOVERY
from .household_tasks import CONF_BEDTIME, CONF_BEDTIME_LIGHTS, CONF_BEDTIME_CONTACTS
from .incidents import CONF_COVERAGE
from .frigate_health import CONF_FRIGATE_HEALTH
from .topics import CONF_ROOM_HANDOFF
from .sessions import CONF_SESSIONS, CONF_SESSION_SECONDS, CONF_GUARDED, CONF_UNDO, CONF_EVIDENCE, CONF_INCIDENTS
from .security_events import CONF_EVENT_EVIDENCE, CONF_EVENT_CONTEXT, CONF_EVENT_FEEDBACK, FEEDBACK
from .clip_analysis import CONF_SHARED_CLIPS

from .const import (
    CONF_COMFORT_TEMP,
    CONF_CAMSEC,
    CONF_CAMSEC_DESCRIBE,
    CONF_CAMSEC_ENTRIES,
    CONF_CAMSEC_INSIDE,
    CONF_CAMSEC_SENSORS,
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
    CONF_FACE_TEACH_CAMERAS,
    CONF_SMART_AI,
    CONF_PHONE_BUTTONS,
    CONF_PHONE_APPS,
    CONF_BINS_CHECK,
    CONF_BINS_SPOT,
    CONF_BINS_CALENDAR,
    CONF_BINS_TIME,
    CONF_BATTERY_FORECAST,
    CONF_AUTO_BACKUP,
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
    CONF_GOALS,
    CONF_BOOKS,
    CONF_BOOKS_FOLDER,
    CONF_GAMES,
    CONF_VOICE_ID,
    CONF_VOICE_ID_UNKNOWN,
    CONF_READING_TTS,
    CONF_READING_VOICE,
    CONF_READ_AHEAD,
    CONF_RECIPES,
    CONF_RECIPES_FOLDER,
    CONF_RECIPES_LIST,
    CONF_TANDOOR_URL,
    CONF_TANDOOR_TOKEN,
    CONF_BACKUP_KEEP,
    CONF_SELF_TEST,
    CONF_RAIN,
    CONF_RAIN_ENTITIES,
    CONF_HUTCH_CHECK,
    CONF_HUTCH_SPOT,
    CONF_CAT_WATCH,
    CONF_CAT_SENSORS,
    CONF_CAT_LOUD,
    CONF_CONFIRM,
    CONF_STYLE_NOTES,
    MAX_STYLE_NOTES,
    CONF_WARM_BY_WEEKDAYS,
    CONF_WARM_BY_WEEKENDS,
    CONF_HEATING_ENTITIES,
    CONF_HEATING_LIMIT,
    CONF_HEATING_NIGHT_TEMP,
    CONF_HEATING_REVERT,
    CONF_HEATING_REVERT_DAY,
    CONF_HEATING_REVERT_NIGHT,
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
    CONF_HEATING_SCHEDULE,
    CONF_HEATING_GRAPH,
    CONF_ROOMS_VIEW,
    CONF_KNOWLEDGE,
    CONF_DAY_CHART,
    CONF_PREHEAT,
    CONF_WEEKLY_REPORT,
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
    CONF_CAMERA_WAKE,
    CONF_CAMERA_WAKE_CAMERAS,
    CONF_CAMERA_WAKE_SECONDS,
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
    CONF_ENERGY_CURRENCY,
    CONF_PRICE_ENTITY,
    CONF_SEARCH_HINTS,
    CONF_REMARK_REPEAT_HOURS,
    CONF_WEB_SEARCH,
    CONF_SEARXNG_URL,
    CONF_ALEXA_SKILL,
    CONF_ALEXA_WAIT,
    CONF_ALEXA_LATE,
    CONF_CRITICAL_IGNORE,
    CONF_CRITICAL_EXTRA,
    CONF_WELCOME_MIN_AWAY,
    CONF_WELCOME_PEOPLE,
    CONF_WELCOME_UNTIL,
    CONF_NOTIFY_WHEN,
    CONF_ALL_OCCUPIED,
    CONF_ANNOUNCE_VISITORS,
    CONF_BANNED_WORDS,
    CONF_BATTERY_THRESHOLD,
    CONF_BRIEFING_CALENDARS,
    CONF_BRIEFING_SECTIONS,
    CONF_BRIEFING_SYSTEM,
    CONF_OUTSIDE_INFO,
    CONF_PARCEL_TRACKERS,
    CONF_BRIEFING_TIME,
    CONF_BRIEFING_TODO,
    CONF_BRIEFING_WEATHER,
    CONF_BRIEFINGS,
    CONF_CAMERA_COOLDOWN,
    CONF_CHAT_AGENT,
    CONF_VOICE_AGENT,
    CONF_BACKUP_AGENT,
    CONF_ANIMAL_SENSORS,
    CONF_AWAY_LIGHTS_ON,
    CONF_AWAY_PEOPLE,
    CONF_HOLIDAY,
    CONF_HOLIDAY_END_ON_RETURN,
    CONF_HOLIDAY_FROST,
    CONF_HOLIDAY_THERMOSTATS,
    CONF_AWAY_LIGHTS,
    CONF_AWAY_AFTER,
    CONF_SCREEN,
    CONF_SCREEN_ACTIONS,
    CONF_SCREEN_CLEAR_ACTIONS,
    CONF_ANIMAL_LOW,
    CONF_ANIMAL_HIGH,
    CONF_CHATTINESS,
    CONF_TALK_STYLE,
    CONF_CARRY_ON_MINUTES,
    CONF_FOCUSED_TOOLS,
    CONF_ASK_LATER,
    CONF_PUT_RIGHT,
    CONF_MUSIC,
    CONF_DAILY_AI_LIMIT,
    CONF_APPLIANCES,
    CONF_APPLIANCE_ANNOUNCE,
    CONF_APPLIANCE_WATTS,
    CONF_DEFAULT_SPEAKER,
    CONF_DOOR_CAMERA,
    CONF_DOOR_TRIGGERS,
    CONF_FACE_SENSOR,
    CONF_DOORSTEP_SENSORS,
    CONF_ZONE_DOOR_SENSORS,
    CONF_ZONE_VISITS,
    CONF_FALLBACK_SPEAKERS,
    CONF_HEALTH_IGNORE,
    CONF_HIDE_SUBSTREAMS,
    CONF_IGNORE_CAMERAS,
    CONF_HOUSE_RULES,
    CONF_LEARN_ENTITIES,
    CONF_LEARN_ANCHORS,
    CONF_TRUST_COVERS,
    CONF_TRUST_HEATING,
    CONF_TRUST_LIGHTS,
    CONF_TRUST_SWITCHES,
    CONF_OFFER_TOGETHER,
    CONF_MOTION_WINDOW,
    CONF_NOTICE_ENTITIES,
    CONF_PARCEL_ALERTS,
    CONF_PASS_THROUGH_AREAS,
    CONF_PERSONA,
    CONF_PROMPT_MEMORIES,
    CONF_PROMPT_RECENT,
    CONF_LOOK_CACHE,
    CONF_CAMERA_PREFILTER,
    CONF_BUDGET_DRIVE,
    CONF_BUDGET_NOTICING,
    CONF_BUDGET_CAMERAS,
    CONF_ROUTINE_EVIDENCE,
    CONF_QUIET_END,
    CONF_BUSY_ENTITIES,
    CONF_QUIET_START,
    CONF_PAGE_ADMIN_ONLY,
    CONF_SIDEBAR,
    CONF_VOICE_UNLOCK,
    CONF_SPEAKERS,
    CONF_SUGGEST_ROUTINES,
    CONF_LINK_TRIGGERS,
    CONF_SUPPRESS_ENTITIES,
    CONF_SUPPRESS_SECONDS,
    DEFAULT_PERSONA,
    DOMAIN,
    MEMORY_CATEGORIES,
)

_LOGGER = logging.getLogger(__name__)

# (id, title, icon, blurb, keys) — the order the Settings tab shows them in
GROUPS: list[tuple[str, str, str, str, list[str]]] = [
    ("voice", "Voice", "mdi:account-voice", "Name, speakers and how it talks.", [
        CONF_PERSONA, CONF_SPEAKERS, CONF_DEFAULT_SPEAKER, CONF_FALLBACK_SPEAKERS, CONF_CHIME, CONF_SPEAK_VOLUME,
        CONF_NIGHT_VOLUME, CONF_DUCK_MEDIA, CONF_ALEXA_TTS_ONLY, CONF_ROOM_VOLUME, CONF_FULL_VOLUME_SPEAKERS, CONF_MEDIA_SPEAKERS, CONF_MEDIA_IMPORTANT_ONLY, CONF_TTS_ENTITY, CONF_SPEAK_HOUSE_LANGUAGE, CONF_AUTOMATIC_LANGUAGE, CONF_REMEMBER_LANGUAGE_PHRASES, CONF_STREAM_VOICE, CONF_VOICE_TIMING_DIAGNOSTICS, CONF_SPECIFIC_CLARIFICATION, CONF_ACTION_RECOVERY, CONF_VOICE_LANGUAGE, CONF_FOLLOW_YOU, CONF_FOLLOW_MINUTES, CONF_ALL_OCCUPIED,
        CONF_PERSONALITY, CONF_PERSONALITY_NOTES, CONF_STYLE_FEEDBACK, CONF_STYLE_NOTES, CONF_CHATTINESS, CONF_TALK_STYLE, CONF_CARRY_ON_MINUTES, CONF_FOCUSED_TOOLS, CONF_ASK_LATER, CONF_PUT_RIGHT, CONF_MUSIC, CONF_BANNED_WORDS, CONF_HOUSE_RULES, CONF_CONFIRM,
        CONF_LIVE_VOICE_CHECK, CONF_SPEECH_DELIVERY_HISTORY, "speech_failure_notification", CONF_SESSIONS, CONF_ROOM_HANDOFF, CONF_CONTEXTUAL_FOLLOWUPS, "recent_followup_context", "conversation_cues", "behaviour_explanations", "current_house_context", "house_context_entities", "house_context_updates", CONF_RECOVERY, CONF_SESSION_SECONDS, CONF_GUARDED, CONF_VOICE_UNLOCK, CONF_UNDO, CONF_EVIDENCE,
    ]),
    ("quiet", "Quiet & rooms", "mdi:weather-night", "When to keep it down, knowing when the house has gone to bed and woken up, and how rooms are worked out.", [
        CONF_QUIET_START, CONF_QUIET_END, CONF_BUSY_ENTITIES, CONF_SLEEP, CONF_SLEEP_MINUTES, CONF_SLEEP_IGNORE, CONF_BEDTIME_ROUTINES, CONF_SLEEP_ACTIONS,
        CONF_WAKE_ACTIONS, CONF_MOTION_WINDOW, CONF_PASS_THROUGH_AREAS, CONF_ROOMS_VIEW,
        CONF_SUPPRESS_ENTITIES, CONF_SUPPRESS_SECONDS,
        CONF_BEDTIME, CONF_BEDTIME_LIGHTS, CONF_BEDTIME_CONTACTS,
    ]),
    ("never", "Never touch", "mdi:hand-back-left-off-outline", "Devices Sentient must never switch, dim or change by itself — not in rules, routines, offers, auto-off, away or motion lights, light shows, the heating or by voice. It can still read them and talk about them.", [
        CONF_NEVER_ENTITIES,
    ]),
    ("briefing", "Briefings", "mdi:text-box-outline", "What a briefing covers and when.", [
        CONF_BRIEFINGS, CONF_BRIEFING_SECTIONS, CONF_BRIEFING_WEATHER, CONF_BRIEFING_TODO,
        CONF_BRIEFING_CALENDARS, CONF_BRIEFING_SYSTEM, CONF_OUTSIDE_INFO, CONF_PARCEL_TRACKERS, CONF_BRIEFING_TIME, CONF_MORNING_BRIEFING, CONF_MORNING_BRIEFING_AREAS, CONF_DIGEST_TIME,
    ]),
    ("noticing", "Noticing & learning", "mdi:radar", "What it watches for and learns from.", [
        CONF_NOTICE_ENTITIES, CONF_REMARK_REPEAT_HOURS, CONF_LEARN_ENTITIES, CONF_LEARN_ANCHORS, CONF_OFFER_TOGETHER, CONF_LINK_TRIGGERS, CONF_SUGGEST_ROUTINES, CONF_ROUTINE_EVIDENCE, CONF_WELCOME, CONF_WELCOME_PEOPLE, CONF_WELCOME_MIN_AWAY, CONF_WELCOME_UNTIL,
        CONF_INSIDE_SENSORS, CONF_WEEKLY_REPORT, CONF_DAY_CHART,
    ]),
    ("nightwatch", "Night watch", "mdi:shield-moon-outline", "Doors and gates opening at times they never normally do, with nobody up.", [
        CONF_NIGHT_WATCH, CONF_NIGHT_WATCH_ENTITIES, CONF_NIGHT_WATCH_ANNOUNCE, CONF_NIGHT_WATCH_SPEAKERS,
    ]),
    ("camsec", "Camera security", "mdi:cctv", "People on the cameras: their route from one to the next, someone in the garden without the gate opening, anyone about at night.", [
        CONF_CAMSEC, CONF_CAMSEC_SENSORS, CONF_CAMSEC_INSIDE, CONF_CAMSEC_ENTRIES, CONF_CAMSEC_DESCRIBE, "security_reasoning",
        CONF_EVENT_EVIDENCE, CONF_EVENT_CONTEXT, CONF_EVENT_FEEDBACK, CONF_COVERAGE, CONF_FRIGATE_HEALTH,
    ]),
    ("heating", "Heating", "mdi:radiator", "Switches for the heating features. The thermostats, night temperature, comfort temperature, time limits and warm-by times, and the heating schedule, are on the House tab.", [
        CONF_PREHEAT, CONF_HEATING_SCHEDULE, CONF_HEATING_GRAPH, CONF_HEATING_REVERT,
    ]),
    ("heating_zones", "Heating zones", "mdi:radiator", "Which thermostats Sentient looks after, the comfort temperature, the time limit and when the house should be warm by. Shown on the House tab.", [
        CONF_HEATING_ENTITIES, CONF_HEATING_NIGHT_TEMP, CONF_HEATING_REVERT_DAY, CONF_HEATING_REVERT_NIGHT, CONF_HEATING_LIMIT, CONF_COMFORT_TEMP, CONF_WARM_BY_WEEKDAYS, CONF_WARM_BY_WEEKENDS,
    ]),
    ("door", "Doorbell & AI looks", "mdi:doorbell-video", "Doorbell log, parcels, faces, and which AI looks at pictures.", [
        CONF_DOOR_CAMERA, CONF_DOOR_TRIGGERS, CONF_FACE_SENSOR, CONF_FACES, CONF_FACE_PEOPLE, CONF_FACE_TEACH, CONF_FACE_TEACH_CAMERAS, CONF_FACE_CAMERAS, CONF_FACE_FORGET_HOURS, CONF_ANNOUNCE_VISITORS, CONF_PARCEL_ALERTS, CONF_DOORSTEP_SENSORS, CONF_ZONE_VISITS, CONF_ZONE_DOOR_SENSORS,
        CONF_AI_TASK_ENTITY, CONF_SECOND_OPINION, CONF_SMART_AI, CONF_EVENT_CLIPS, CONF_CAMERA_COOLDOWN, CONF_LOOK_CACHE, CONF_CAMERA_PREFILTER,
    ]),
    ("clips", "Event clips", "mdi:filmstrip-box-multiple", "Watches Frigate's recorded clips from chosen cameras and writes down what happened — on the timeline, the Sentient page and for the voice. Never spoken. The clip is deleted once watched.", [
        CONF_CLIP_WATCH, CONF_CLIP_CAMERAS, CONF_CLIP_SCOPE, CONF_CLIP_WHEN, CONF_CLIP_NOTIFY, CONF_CLIP_SOURCE, CONF_CLIP_WHOSE, CONF_CLIP_LOOKS,
        CONF_BUDGET_CLIPS, CONF_CLIP_DAYS, CONF_CLIP_KEEP, CONF_SHARED_CLIPS,
    ]),
    ("phone", "Phone notifications", "mdi:cellphone-message", "What also goes to people's phones.", [
        CONF_NOTIFY_TARGETS, CONF_NOTIFY_WHEN, CONF_PHONE_BUTTONS, CONF_PHONE_APPS,
    ]),
    ("safety", "Safety & security", "mdi:shield-home", "Smoke, CO, gas and leaks; doors left open; a check when everyone goes out.", [
        CONF_INCIDENTS, CONF_CRITICAL, CONF_CRITICAL_EXTRA, CONF_CRITICAL_IGNORE, CONF_LEFT_OPEN_MINUTES, CONF_LEFT_OPEN_IGNORE, CONF_LEAVING_CHECK,
    ]),
    ("party", "Light shows", "mdi:party-popper", "Disco, Halloween, Christmas and more: lights put on a show, can follow the music, then go back exactly as they were.", [
        CONF_PARTY_WHOLE_HOUSE, CONF_PARTY_LIGHTS, CONF_PARTY_EXCLUDE, CONF_PARTY_STYLE, CONF_PARTY_MINUTES,
        CONF_PARTY_SOUND, CONF_PARTY_SOUND_SWITCH, CONF_PARTY_SHOW,
    ]),
    ("mind", "Mind", "mdi:head-lightbulb-outline", "Remembering conversations, checking back on things, asking about what it can't explain, thinking over the day each night, a look at tomorrow each evening — and how many AI calls it may make a day.", [
        CONF_CONVERSATIONS, CONF_FOLLOW_UPS, CONF_CURIOSITY, CONF_REFLECTION, CONF_REFLECTION_TIME, CONF_FORESIGHT, CONF_FORESIGHT_TIME,
        CONF_SELF_TEST, CONF_DAILY_AI_LIMIT, CONF_BUDGET_DRIVE, CONF_BUDGET_NOTICING, CONF_BUDGET_CAMERAS, CONF_PROMPT_RECENT, CONF_RULES,
        CONF_SELF_CHECK, CONF_SELF_ADJUST, CONF_LEARN_CORRECTIONS, CONF_NORMAL_DAY, CONF_THOUGHTS, CONF_SHARE_LEARNING, CONF_KNOWLEDGE, CONF_PROPOSALS, CONF_PRIVACY_LOG, CONF_GOALS,
    ]),
    ("updates", "Updates", "mdi:update", "Check for Sentient releases; installation stays under your control.", [CONF_UPDATE_CHECKER]),
    ("voicebackup", "Voice backup", "mdi:lifebuoy", "If the AI voice agent fails or times out, the same request goes to a backup agent so simple things still work. Point your Assist pipeline at the “with backup” agent to use it.", [
        CONF_VOICE_AGENT, CONF_BACKUP_AGENT, "offline_answers",
    ]),
    ("screen", "Tablet screen", "mdi:tablet-dashboard", "What the Sentient screen card shows: alerts with the camera, notices, a morning card and the house at a glance. Actions can wake a tablet and switch it to that page.", [
        CONF_SCREEN, CONF_SCREEN_ACTIONS, CONF_SCREEN_CLEAR_ACTIONS,
    ]),
    ("animals", "Animals", "mdi:rabbit", "Animals that live outside: a word when their hutch, kennel or coop gets too cold or too hot, frost warnings the evening before, a look at the hutch at dusk, and a watch for cats by it at night.", [
        CONF_ANIMAL_SENSORS, CONF_ANIMAL_LOW, CONF_ANIMAL_HIGH, CONF_HUTCH_CHECK, CONF_HUTCH_SPOT, CONF_CAT_WATCH, CONF_CAT_SENSORS, CONF_CAT_LOUD,
    ]),
    ("appliances", "Appliances", "mdi:washing-machine", "How long things normally run: says when one's finished, or seems stuck — in plain words, no AI.", [
        CONF_APPLIANCES, CONF_APPLIANCE_ANNOUNCE, CONF_APPLIANCE_WATTS,
    ]),
    ("drive", "The drive", "mdi:car", "Which cars are on the drive, from a camera spot — one AI look when activity settles.", [
        CONF_DRIVE_SPOT, CONF_DRIVE_CAMERA, CONF_DRIVE_TRIGGERS, CONF_KNOWN_CARS,
    ]),
    ("camwake", "Sleepy cameras", "mdi:camera-timer", "Battery cameras and others that go into standby show an old or black picture until they wake, so the AI would answer from it (\u201cthe drive is empty\u201d). Add actions that wake them \u2014 pressing a wake or refresh button, turning on a stream, running a script; {{ camera }} is the camera being looked at \u2014 and Sentient runs them first and waits for a fresh picture. Each camera is woken at most once a minute. No actions: off.", [
        CONF_CAMERA_WAKE, CONF_CAMERA_WAKE_CAMERAS, CONF_CAMERA_WAKE_SECONDS,
    ]),
    ("trust", "What the house does by itself", "mdi:hand-okay", "For things the house decides to do on its own — routines you've let it take over and warming the heating before you're up — whether it just does them, says so first, or asks. Garage doors and gates always ask; locks and the alarm are never done by the house.", [
        CONF_TRUST_LIGHTS, CONF_TRUST_SWITCHES, CONF_TRUST_COVERS, CONF_TRUST_HEATING,
    ]),
    ("away", "Away lights & holiday", "mdi:home-export-outline", "When everyone's out after dark, the lights copy how they were used at the same time last week, a few minutes either way, so the house looks lived in. Holiday mode adds: nothing said out loud but safety alerts (it goes to the phones, with visitors), chosen thermostats down to frost protection, and everything back to normal when you're home.", [
        CONF_AWAY_LIGHTS_ON, CONF_AWAY_PEOPLE, CONF_AWAY_LIGHTS, CONF_AWAY_AFTER,
        CONF_HOLIDAY, CONF_HOLIDAY_THERMOSTATS, CONF_HOLIDAY_FROST, CONF_HOLIDAY_END_ON_RETURN,
    ]),
    ("bins", "Bins check", "mdi:delete-variant", "The evening before collection (from the bin calendar), one camera look at where the bins go out — said only if they aren't out yet.", [
        CONF_BINS_CHECK, CONF_BINS_CALENDAR, CONF_BINS_SPOT, CONF_BINS_TIME,
    ]),
    ("rain", "Rain warnings", "mdi:weather-pouring", "A word when rain's due within the hour and a window or door is left open. Reads the hourly forecast — no AI.", [
        CONF_RAIN, CONF_RAIN_ENTITIES,
    ]),
    ("search", "Web search", "mdi:web", "Questions about the world outside the house. Best with the Voice Satellite LLM Tools companion (web, images, YouTube on the tablets); or Sentient's own voice-only search through your SearXNG.", [
        CONF_SEARCH_HINTS, CONF_WEB_SEARCH, CONF_SEARXNG_URL,
    ]),
    ("alexa", "Ask through Alexa", "mdi:amazon-alexa", "Ask the house brain from any Echo with an Alexa skill: point the skill at the \u201cFor Alexa\u201d agent. It knows which Echo was spoken to, keeps answers short, and if the AI needs longer than Alexa waits, says the answer on that Echo when it's ready.", [
        CONF_ALEXA_SKILL, CONF_ALEXA_WAIT, CONF_ALEXA_LATE,
    ]),
    ("manuals", "Manuals & documents", "mdi:book-open-page-variant-outline", "Answers from your own documents: put appliance manuals, warranties and receipts (PDFs or photos) in a folder of Home Assistant's media, named after what they're for, and ask \u201cwhat filter does the air purifier need?\u201d. One AI call per question.", [
        CONF_MANUALS, CONF_MANUALS_FOLDER, CONF_MANUALS_LOCAL,
    ]),
    ("games", "Story games", "mdi:dice-multiple-outline", "Voice-led story games the whole house can play, such as an escape-the-house adventure with the assistants as game master; start from a ready-made template and fill in your own names and rooms, or write your own. Each game keeps its own saved memory (where the players are, what they carry, what they found and chose) that carries on between sessions and between assistants, and is shown to a voice only while that game is being played. The game master can also read the house's ordinary memory, never change it. Switching this off hides this page and the game tools; your saved games are kept, not cleared, and are there again when you switch it back on.", [
        CONF_GAMES,
    ]),
    ("voiceid", "Voice recognition", "mdi:account-voice", "Know who is speaking. With the Sentient Voice Recognition add-on, the assistants can tell which enrolled person spoke and greet them by name, and treat \u201cremember that I\u2026\u201d as being about them. It is a good guess from the voice, never proof, so it never allows anything that asks for confirmation. Without the add-on, this page shows where to get it.", [
        CONF_VOICE_ID, CONF_VOICE_ID_UNKNOWN,
    ]),
    ("reading", "Books & recipes", "mdi:book-open-variant", "Ebooks and your own stories read aloud straight through until stopped or paused (the wake word pauses), audiobooks played, and each speaker keeps its own place. Pick a reading voice to give stories a different text-to-speech voice from the usual one, and use TRY THE VOICE to hear it first. Recipes come from a folder, Mealie or Tandoor, with a step-by-step cooking mode. Works on any speaker \u2014 but Echos (Alexa) cut long speech short, can't play the house's own audio files and only have Alexa's voice, so on an Echo a book comes one passage per request through the Alexa skill and audiobooks need another speaker. Recipes work fine on Echos.", [
        CONF_BOOKS, CONF_BOOKS_FOLDER, CONF_READING_TTS, CONF_READING_VOICE, CONF_READ_AHEAD, CONF_RECIPES, CONF_RECIPES_FOLDER, CONF_RECIPES_LIST, CONF_TANDOOR_URL, CONF_TANDOOR_TOKEN,
    ]),
    ("energy", "Energy", "mdi:lightning-bolt", "Electricity use from the Energy dashboard's devices — and, with a time-of-use tariff, a nudge to run the washing machine or dishwasher when it's cheapest.", [
        CONF_ENERGY, CONF_ENERGY_PRICE, CONF_ENERGY_CURRENCY, CONF_PRICE_ENTITY,
    ]),
    ("health", "House health", "mdi:home-heart", "What counts as a problem.", [
        CONF_BATTERY_THRESHOLD, CONF_BATTERY_FORECAST, CONF_HEALTH_IGNORE, CONF_HIDE_SUBSTREAMS, CONF_IGNORE_CAMERAS, CONF_HIDE_SUBSTREAMS, CONF_IGNORE_CAMERAS,
    ]),
    ("autobackup", "Weekly backup", "mdi:calendar-sync-outline", "Every Sunday night, the same backup as the download button, saved in Home Assistant's config folder (so Home Assistant's own backups have it too). The newest few are kept; restore one from Backup.", [
        CONF_AUTO_BACKUP, CONF_BACKUP_KEEP,
    ]),
    ("page", "This page", "mdi:monitor-dashboard", "The sidebar page and its Ask box.", [
        CONF_SIDEBAR, CONF_PAGE_ADMIN_ONLY, CONF_SUGGESTIONS, CONF_WHATS_NEW, CONF_SIMPLE, CONF_CHAT_AGENT, CONF_PROMPT_MEMORIES, CONF_RELEVANT_MEMORIES,
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


def page_access(func):
    """Read-only page commands: refuse non-administrators when the page is set to administrators only."""
    def _refused(hass, connection, msg) -> bool:
        entry = _entry(hass)
        if entry is not None and _current(entry).get(CONF_PAGE_ADMIN_ONLY) and not connection.user.is_admin:
            connection.send_error(msg["id"], websocket_api.ERR_UNAUTHORIZED, "Only administrators can use the Sentient page")
            return True
        return False

    if inspect.iscoroutinefunction(func):
        @functools.wraps(func)
        async def wrapper(hass, connection, msg):
            if not _refused(hass, connection, msg):
                await func(hass, connection, msg)
    else:
        @functools.wraps(func)
        def wrapper(hass, connection, msg):
            if not _refused(hass, connection, msg):
                func(hass, connection, msg)
    return wrapper


@websocket_api.websocket_command({
    vol.Required("type"): "sentient/heating/programme/save", vol.Optional("programme_id"): str,
    vol.Required("programme"): dict,
})
@websocket_api.require_admin
@callback
def ws_heating_programme_save(hass: HomeAssistant, connection, msg: dict[str, Any]) -> None:
    """Add or change a heating programme: heaters, days and on/off periods."""
    from .heating_schedule import clean_programme, programme_conflict

    entry = _entry(hass)
    if entry is None:
        connection.send_error(msg["id"], "not_loaded", "Sentient is not set up")
        return
    rt = entry.runtime_data
    try:
        data = clean_programme(rt.options, msg["programme"])
    except ValueError as err:
        connection.send_error(msg["id"], "invalid", str(err))
        return
    if pid := msg.get("programme_id"):
        if pid not in rt.heat_prog.items.items:
            connection.send_error(msg["id"], "not_found", "That heating programme no longer exists")
            return
        data["id"] = pid
    if why := programme_conflict(data, rt.heat_prog.programmes()):
        connection.send_error(msg["id"], "conflict", why)
        return
    connection.send_result(msg["id"], rt.heat_prog.items.upsert(data))


@websocket_api.websocket_command({vol.Required("type"): "sentient/heating/programme/delete", vol.Required("programme_id"): str})
@websocket_api.require_admin
@callback
def ws_heating_programme_delete(hass: HomeAssistant, connection, msg: dict[str, Any]) -> None:
    entry = _entry(hass)
    ok = bool(entry and entry.runtime_data.heat_prog.items.remove(msg["programme_id"]))
    connection.send_result(msg["id"], {"removed": ok})


@websocket_api.websocket_command({vol.Required("type"): "sentient/today"})
@page_access
@callback
def ws_today(hass: HomeAssistant, connection, msg: dict[str, Any]) -> None:
    """The overview's TODAY card: weather, who's home, what's next (alarms, reminders, bins), heating."""
    from .heating_schedule import enabled as sched_on, next_change, night_temp

    entry = _entry(hass)
    if entry is None:
        connection.send_result(msg["id"], {})
        return
    rt = entry.runtime_data
    opts = rt.options
    out: dict[str, Any] = {}
    if (w := hass.states.get(str(opts.get("briefing_weather") or ""))) is not None and w.state not in ("unavailable", "unknown"):
        out["weather"] = {"condition": w.state, "temp": w.attributes.get("temperature"), "unit": w.attributes.get("temperature_unit"),
                          "humidity": w.attributes.get("humidity"), "wind": w.attributes.get("wind_speed")}
    people = []
    trackers = list(opts.get("welcome_people") or [])  # a phone chosen for welcome home is better evidence than a person who never leaves
    for st in hass.states.async_all("person"):
        mine = [t for t in st.attributes.get("device_trackers") or [] if t in trackers]
        where = hass.states.get(mine[0]).state if mine and hass.states.get(mine[0]) is not None else st.state
        people.append({"name": st.name, "home": where == "home", "where": where})
    if not people:
        for eid in opts.get("welcome_people") or []:
            if (st := hass.states.get(eid)) is not None:
                people.append({"name": st.name, "home": st.state == "home", "where": st.state})
    out["people"] = people
    now = dt_util.utcnow()
    soon = []
    for a in rt.alarms.listing():
        if a["on"] and a["next"] and (dt_util.parse_datetime(a["next"]) - now).total_seconds() < 36 * 3600:
            soon.append({"when": a["next"], "what": a["name"], "kind": "routine" if a["silent"] else "alarm"})
    if (cal := hass.states.get(str(opts.get("bins_calendar") or ""))) is not None and cal.attributes.get("start_time"):
        start = dt_util.parse_datetime(str(cal.attributes["start_time"])) or None
        if start is not None:
            start = dt_util.as_utc(start if start.tzinfo else start.replace(tzinfo=dt_util.get_default_time_zone()))
            if (start - now).total_seconds() < 3 * 86400:
                soon.append({"when": start.isoformat(), "what": str(cal.attributes.get("message") or "Bins"), "kind": "bins"})
    for w in list(getattr(rt.watches, "_watches", {}).values()):
        try:
            soon.append({"when": w.until_dt.isoformat(), "what": w.what, "kind": "watch"})
        except Exception:  # noqa: BLE001 - a malformed watch shouldn't hide the rest
            continue
    out["soon"] = sorted(soon, key=lambda x: x["when"])[:8]
    zones = []
    for eid in rt.heating.entities:
        if (st := hass.states.get(eid)) is not None:
            zones.append({"name": st.name, "now": st.attributes.get("current_temperature"), "target": st.attributes.get("temperature"),
                          "state": st.state, "action": st.attributes.get("hvac_action")})
    progs = rt.heat_prog.programmes() if getattr(rt, "heat_prog", None) is not None else []
    nxt = next_change(progs, dt_util.now()) if sched_on(opts) else None
    out["heating"] = {"zones": zones, "next": {**nxt, "night": night_temp(opts)} if nxt else None}
    connection.send_result(msg["id"], out)


@websocket_api.websocket_command({vol.Required("type"): "sentient/glance"})
@page_access
@callback
def ws_glance(hass: HomeAssistant, connection, msg: dict[str, Any]) -> None:
    """Rooms at a glance for the House tab, and the overview's around-the-house numbers."""
    from .glance import rooms, summary

    entry = _entry(hass)
    if entry is None or not entry.runtime_data.options.get(CONF_ROOMS_VIEW, True):
        connection.send_result(msg["id"], {"on": False, "rooms": [], "summary": None})
        return
    rt = entry.runtime_data
    try:
        likely = rt.tracker.best_area()
    except Exception:  # noqa: BLE001 - the tiles still show without the best guess
        likely = None
    sleep = getattr(rt, "sleep", None)
    restless = {r["entity_id"] for r in sleep.never_rests()} if sleep is not None else set()
    rows = rooms(hass, likely, restless)
    connection.send_result(msg["id"], {"on": True, "rooms": rows, "summary": summary(rows)})


@websocket_api.websocket_command({vol.Required("type"): "sentient/day"})
@page_access
@callback
def ws_day(hass: HomeAssistant, connection, msg: dict[str, Any]) -> None:
    """The overview's THE DAY SO FAR chart: today's timeline counted by the hour."""
    from .glance import day

    entry = _entry(hass)
    if entry is None or not entry.runtime_data.options.get(CONF_DAY_CHART, True):
        connection.send_result(msg["id"], {"on": False})
        return
    now = dt_util.utcnow()
    hours_today = dt_util.as_local(now).hour + 1
    items = entry.runtime_data.timeline.since(hours_today, None, 1000)
    connection.send_result(msg["id"], {"on": True, **day(items, now)})


@websocket_api.websocket_command({vol.Required("type"): "sentient/memory/review"})
@page_access
@callback
def ws_memory_review(hass: HomeAssistant, connection, msg: dict[str, Any]) -> None:
    """Memories to look at: ones that contradict each other, or say the same thing twice."""
    entry = _entry(hass)
    connection.send_result(msg["id"], {"pairs": entry.runtime_data.memory.review() if entry is not None else []})


@websocket_api.websocket_command({vol.Required("type"): "sentient/memory/resolve", vol.Required("pair"): str,
                                  vol.Optional("keep"): vol.Any(str, None)})
@websocket_api.require_admin
@callback
def ws_memory_resolve(hass: HomeAssistant, connection, msg: dict[str, Any]) -> None:
    entry = _entry(hass)
    ok = bool(entry is not None and entry.runtime_data.memory.resolve(msg["pair"], msg.get("keep")))
    connection.send_result(msg["id"], {"ok": ok})


@websocket_api.websocket_command({vol.Required("type"): "sentient/notice/unwatch", vol.Required("entity_id"): str})
@websocket_api.require_admin
@callback
def ws_notice_unwatch(hass: HomeAssistant, connection, msg: dict[str, Any]) -> None:
    """Stop noticing one thing (from the Mind page's 'I keep deciding this isn't worth saying')."""
    entry = _entry(hass)
    if entry is None:
        connection.send_error(msg["id"], "not_loaded", "Sentient is not set up")
        return
    options = _current(entry)
    watched = list(options.get(CONF_NOTICE_ENTITIES) or [])
    if msg["entity_id"] in watched:
        watched.remove(msg["entity_id"])
        hass.config_entries.async_update_entry(entry, options={**options, CONF_NOTICE_ENTITIES: watched})
    connection.send_result(msg["id"], {"notice_entities": watched})


@websocket_api.websocket_command({vol.Required("type"): "sentient/inner"})
@page_access
@callback
def ws_inner(hass: HomeAssistant, connection, msg: dict[str, Any]) -> None:
    """What it's thinking: lessons from corrections, an unusual day, and how well it guesses the next room."""
    entry = _entry(hass)
    inner = getattr(entry.runtime_data, "inner", None) if entry is not None else None
    out = dict(inner.summary()) if inner is not None else {"learn": False, "day": False, "thoughts_on": False}
    if entry is not None and entry.runtime_data.options.get(CONF_KNOWLEDGE, True):
        from .glance import knows

        try:
            out["knows"] = knows(entry.runtime_data)
        except Exception:  # noqa: BLE001 - the rest of the Mind tab still loads
            _LOGGER.debug("Sentient couldn't sum up what it knows", exc_info=True)
            out["knows"] = []
    connection.send_result(msg["id"], out)


@websocket_api.websocket_command({vol.Required("type"): "sentient/inner/dismiss", vol.Required("key"): str})
@websocket_api.require_admin
@callback
def ws_inner_dismiss(hass: HomeAssistant, connection, msg: dict[str, Any]) -> None:
    entry = _entry(hass)
    inner = getattr(entry.runtime_data, "inner", None) if entry is not None else None
    connection.send_result(msg["id"], {"ok": bool(inner and inner.dismiss(msg["key"]))})


@websocket_api.websocket_command({vol.Required("type"): "sentient/cameras"})
@page_access
@callback
def ws_cameras(hass: HomeAssistant, connection, msg: dict[str, Any]) -> None:
    """The real cameras (not sub-streams, screenshots or a tablet's own camera), with today's detections."""
    from .cameras import cameras as house_cameras, hidden as hidden_cameras

    entry = _entry(hass)
    if entry is None:
        connection.send_result(msg["id"], {"cameras": [], "hidden": 0})
        return
    options = entry.runtime_data.options
    incidents = getattr(entry.runtime_data, "incidents", None)
    start = dt_util.start_of_local_day()
    seen: dict[str, int] = {}
    for item in getattr(incidents, "items", []) if incidents is not None else []:
        for ev in item.get("evidence", []):
            when = dt_util.parse_datetime(ev.get("time") or "")
            if ev.get("kind") == "event" and when is not None and when >= start:
                seen[ev["source"]] = seen.get(ev["source"], 0) + 1
    rows = [{"entity_id": st.entity_id, "name": st.name, "state": st.state, "today": seen.get(st.entity_id, 0)}
            for st in house_cameras(hass, options)]
    connection.send_result(msg["id"], {"cameras": rows, "hidden": len(hidden_cameras(hass, options))})


@websocket_api.websocket_command({vol.Required("type"): "sentient/heating"})
@page_access
@callback
def ws_heating(hass: HomeAssistant, connection, msg: dict[str, Any]) -> None:
    """The heating zones now, what has been learned about them, and the heating schedule — for the House tab."""
    from .heating_schedule import MAX_TEMP, MIN_TEMP, describe, enabled, night_temp

    entry = _entry(hass)
    if entry is None:
        connection.send_result(msg["id"], {"zones": [], "schedule_on": False, "schedules": []})
        return
    rt = entry.runtime_data
    report = rt.heating.report()
    zones = []
    for eid, plan in zip(rt.heating.entities, report["zones"]):
        st = hass.states.get(eid)
        zones.append({"entity_id": eid, "name": plan["name"], "state": st.state if st else "unavailable",
                      "now": plan.get("now"), "target": st.attributes.get("temperature") if st else None,
                      "action": st.attributes.get("hvac_action") if st else None,
                      "warms_per_hour": plan.get("warms_per_hour"), "comfortable_at": plan.get("comfortable_at")})
    schedules = []
    for item in rt.lists["alarms"].listing():
        if not item.get("heating"):
            continue
        nxt = next((a["next"] for a in rt.alarms.listing() if a["id"] == item["id"]), None)
        schedules.append({"id": item["id"], "name": item["name"], "time": str(item.get("time"))[:5], "days": item.get("days") or [],
                          "enabled": item.get("enabled", True), "entity_id": item["heating"].get("entity_id"),
                          "temperature": item["heating"].get("temperature"), "what": describe(hass, item["heating"]), "next": nxt})
    schedules.sort(key=lambda x: x["time"])
    from .heating_schedule import next_change, programme_conflict, wanted_on

    progs = rt.heat_prog.programmes() if getattr(rt, "heat_prog", None) is not None else []
    now = dt_util.now()
    for z in zones:
        p = wanted_on(progs, z["entity_id"], now) if enabled(rt.options) else None
        z["scheduled"] = {"on": True, "until": p["off"], "temp": p["temp"]} if p else {"on": False}
    from .heating_schedule import suggest

    suggested = [] if progs else suggest(list(rt.heating.entities), dict(rt.heating.wake or {}), dict(rt.heating.zones or {}))
    connection.send_result(msg["id"], {
        "suggested": suggested,
        "programmes": sorted(progs, key=lambda p: p["name"].lower()),
        "conflicts": {p["id"]: why for p in progs if (why := programme_conflict(p, progs))},
        "next": ({**nxt, "night": night_temp(rt.options)} if (nxt := next_change(progs, now)) else None) if enabled(rt.options) else None,
        "zones": zones, "warming_now": report["warming_now"], "usually_up": report["usually_up"], "warm_by": report["warm_by"],
        "schedule_on": enabled(rt.options), "schedules": schedules, "min": MIN_TEMP, "max": MAX_TEMP,
        "night": night_temp(rt.options),
        "graph": bool(rt.options.get(CONF_HEATING_GRAPH, True)),
        "ran": dict(getattr(rt.heat_prog, "ran", {}) or {}) if getattr(rt, "heat_prog", None) is not None else {},
        "ticking": getattr(rt.heat_prog, "last_tick", None) if getattr(rt, "heat_prog", None) is not None else None,
        "admin": connection.user.is_admin,
    })


@websocket_api.websocket_command({
    vol.Required("type"): "sentient/heating/save",
    vol.Optional("item_id"): str,
    vol.Required("entity_id"): str,
    vol.Required("time"): str,
    vol.Optional("days", default=[]): [vol.In(["mon", "tue", "wed", "thu", "fri", "sat", "sun"])],
    vol.Optional("temperature"): vol.Any(None, vol.Coerce(float)),
    vol.Optional("enabled", default=True): bool,
})
@websocket_api.require_admin
@callback
def ws_heating_save(hass: HomeAssistant, connection, msg: dict[str, Any]) -> None:
    from .heating_schedule import describe, enabled, heating_actions

    entry = _entry(hass)
    if entry is None:
        connection.send_error(msg["id"], "not_loaded", "Sentient is not set up")
        return
    rt = entry.runtime_data
    if not enabled(rt.options):
        connection.send_error(msg["id"], "off", "Heating schedules are switched off in Settings → Heating")
        return
    if not re.fullmatch(r"([01]\d|2[0-3]):[0-5]\d(:00)?", msg["time"]):
        connection.send_error(msg["id"], "invalid", "That isn't a time")
        return
    spec = {"entity_id": msg["entity_id"], "temperature": msg.get("temperature")}
    _actions, why = heating_actions(hass, rt.options, spec)
    if why:
        connection.send_error(msg["id"], "invalid", why)
        return
    at = msg["time"][:5]
    days = [d for d in ["mon", "tue", "wed", "thu", "fri", "sat", "sun"] if d in msg["days"]]
    item = {"name": f"Heating: {describe(hass, spec)} at {at}", "time": f"{at}:00", "days": days, "enabled": msg["enabled"],
            "silent": True, "made_by": "page", "heating": spec, "actions": _actions}
    if msg.get("item_id"):
        if msg["item_id"] not in rt.lists["alarms"].items or not rt.lists["alarms"].items[msg["item_id"]].get("heating"):
            connection.send_error(msg["id"], "not_found", "That heating time is gone")
            return
        item["id"] = msg["item_id"]
    rt.lists["alarms"].upsert(item)
    connection.send_result(msg["id"], {"saved": item["name"]})


@websocket_api.websocket_command({vol.Required("type"): "sentient/clips"})
@page_access
@callback
def ws_clips(hass: HomeAssistant, connection, msg: dict[str, Any]) -> None:
    """The clips watched as they happened (newest first) — for the Sentient page."""
    entry = _entry(hass)
    cw = getattr(entry.runtime_data, "clipwatch", None) if entry else None
    if cw is None:
        connection.send_result(msg["id"], {"on": False, "items": []})
        return
    connection.send_result(msg["id"], {"on": cw.enabled, "via": cw.via, "skipped": cw.skipped,
                                       "items": cw.recent(None, 72)[:30]})


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


@websocket_api.websocket_command(
    {vol.Required("type"): "sentient/proposal", vol.Required("proposal"): str, vol.Required("accept"): bool}
)
@websocket_api.require_admin
@callback
def ws_proposal(hass: HomeAssistant, connection, msg: dict[str, Any]) -> None:
    """Yes or no to one of the self-check's suggested settings changes (it changes nothing without a yes)."""
    entry = _entry(hass)
    if entry is None or getattr(entry.runtime_data, "proposals", None) is None:
        connection.send_error(msg["id"], "not_loaded", "Sentient is not set up")
        return
    result = entry.runtime_data.proposals.answer(msg["proposal"], msg["accept"])
    if "error" in result:
        connection.send_error(msg["id"], "gone", result["error"])
        return
    connection.send_result(msg["id"], result)


@websocket_api.websocket_command({vol.Required("type"): "sentient/privacy", vol.Optional("days", default=7): vol.All(vol.Coerce(float), vol.Range(min=1, max=14))})
@websocket_api.require_admin
@callback
def ws_privacy(hass: HomeAssistant, connection, msg: dict[str, Any]) -> None:
    """What has left the house, by feature (metadata only)."""
    entry = _entry(hass)
    if entry is None:
        connection.send_error(msg["id"], "not_loaded", "Sentient is not set up")
        return
    connection.send_result(msg["id"], entry.runtime_data.ai.sent_summary(msg["days"]))


@websocket_api.websocket_command({vol.Required("type"): "sentient/privacy/forget", vol.Optional("hours", default=48): vol.All(vol.Coerce(float), vol.Range(min=1, max=168))})
@websocket_api.require_admin
@callback
def ws_privacy_forget(hass: HomeAssistant, connection, msg: dict[str, Any]) -> None:
    """Forget everything Sentient kept from the last few hours."""
    entry = _entry(hass)
    if entry is None:
        connection.send_error(msg["id"], "not_loaded", "Sentient is not set up")
        return
    from .privacy import forget_recent

    connection.send_result(msg["id"], {"forgot": forget_recent(entry.runtime_data, msg["hours"]), "hours": msg["hours"]})


@websocket_api.websocket_command({vol.Required("type"): "sentient/security/event/feedback",
                                  vol.Required("event_id"): str, vol.Required("value"): vol.In(FEEDBACK)})
@websocket_api.require_admin
@callback
def ws_security_event_feedback(hass: HomeAssistant, connection, msg: dict[str, Any]) -> None:
    entry = _entry(hass)
    if entry is None:
        connection.send_error(msg["id"], "not_loaded", "Sentient is not set up")
        return
    result = entry.runtime_data.security_events.feedback(msg["event_id"], msg["value"])
    if result.get("error"):
        connection.send_error(msg["id"], "not_available", result["error"])
    else:
        connection.send_result(msg["id"], result)


@websocket_api.websocket_command({vol.Required("type"): "sentient/whats_new"})
@page_access
@callback
def ws_whats_new(hass: HomeAssistant, connection, msg: dict[str, Any]) -> None:
    entry = _entry(hass)
    if entry is None or getattr(entry.runtime_data, "whats_new", None) is None:
        connection.send_result(msg["id"], {"releases": []})
        return
    connection.send_result(msg["id"], entry.runtime_data.whats_new.listing())


@websocket_api.websocket_command({vol.Required("type"): "sentient/whats_new/seen"})
@websocket_api.require_admin
@callback
def ws_whats_new_seen(hass: HomeAssistant, connection, msg: dict[str, Any]) -> None:
    entry = _entry(hass)
    if entry is not None and getattr(entry.runtime_data, "whats_new", None) is not None:
        entry.runtime_data.whats_new.mark_seen()
    connection.send_result(msg["id"], {"seen": True})


@websocket_api.websocket_command({vol.Required("type"): "sentient/export/readable"})
@websocket_api.require_admin
@websocket_api.async_response
async def ws_export_readable(hass: HomeAssistant, connection, msg: dict[str, Any]) -> None:
    """Memories, routines, lists and settings as readable text (secrets and addresses left out)."""
    entry = _entry(hass)
    if entry is None:
        connection.send_error(msg["id"], "not_loaded", "Sentient is not set up")
        return
    from .readable import async_labels, build

    text = build(hass, entry.runtime_data, entry.title, await async_labels(hass))
    connection.send_result(msg["id"], {"text": text})


@websocket_api.websocket_command({vol.Required("type"): "sentient/usage"})
@websocket_api.require_admin
@callback
def ws_usage(hass: HomeAssistant, connection, msg: dict[str, Any]) -> None:
    """Which optional features are used this month, on but never used, or off."""
    entry = _entry(hass)
    if entry is None or getattr(entry.runtime_data, "usage", None) is None:
        connection.send_error(msg["id"], "not_loaded", "Sentient is not set up")
        return
    connection.send_result(msg["id"], entry.runtime_data.usage.report())


@callback
def async_register(hass: HomeAssistant) -> None:
    websocket_api.async_register_command(hass, ws_security_event_feedback)
    websocket_api.async_register_command(hass, ws_games)
    websocket_api.async_register_command(hass, ws_games_edit)
    websocket_api.async_register_command(hass, ws_proposal)
    websocket_api.async_register_command(hass, ws_usage)
    websocket_api.async_register_command(hass, ws_export_readable)
    websocket_api.async_register_command(hass, ws_whats_new)
    websocket_api.async_register_command(hass, ws_whats_new_seen)
    websocket_api.async_register_command(hass, ws_privacy)
    websocket_api.async_register_command(hass, ws_privacy_forget)
    websocket_api.async_register_command(hass, ws_voice_check)
    websocket_api.async_register_command(hass, ws_update_check)
    websocket_api.async_register_command(hass, ws_get_settings)
    websocket_api.async_register_command(hass, ws_clips)
    websocket_api.async_register_command(hass, ws_heating)
    websocket_api.async_register_command(hass, ws_heating_save)
    websocket_api.async_register_command(hass, ws_cameras)
    websocket_api.async_register_command(hass, ws_inner)
    websocket_api.async_register_command(hass, ws_notice_unwatch)
    websocket_api.async_register_command(hass, ws_memory_review)
    websocket_api.async_register_command(hass, ws_memory_resolve)
    websocket_api.async_register_command(hass, ws_today)
    websocket_api.async_register_command(hass, ws_glance)
    websocket_api.async_register_command(hass, ws_day)
    websocket_api.async_register_command(hass, ws_heating_programme_save)
    websocket_api.async_register_command(hass, ws_heating_programme_delete)
    websocket_api.async_register_command(hass, ws_inner_dismiss)
    websocket_api.async_register_command(hass, ws_thumb)
    websocket_api.async_register_command(hass, ws_style)
    websocket_api.async_register_command(hass, ws_backups)
    websocket_api.async_register_command(hass, ws_backup_restore)
    websocket_api.async_register_command(hass, ws_backup_save)
    from .wizard import async_register as _wizard

    _wizard(hass)
    from .suggestions import async_register as _suggestions

    _suggestions(hass)
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
    websocket_api.async_register_command(hass, ws_frigate_health)
    websocket_api.async_register_command(hass, ws_critical_test)
    websocket_api.async_register_command(hass, ws_reading_try)
    websocket_api.async_register_command(hass, ws_reading_stop)
    websocket_api.async_register_command(hass, ws_doctor)
    websocket_api.async_register_command(hass, ws_doctor_speakers)
    websocket_api.async_register_command(hass, ws_import)
    websocket_api.async_register_command(hass, ws_motion_save)
    websocket_api.async_register_command(hass, ws_motion_delete)
    websocket_api.async_register_command(hass, ws_motion_night)
    websocket_api.async_register_command(hass, ws_voice_id)
    websocket_api.async_register_command(hass, ws_voice_enrol)
    websocket_api.async_register_command(hass, ws_voice_enrol_cancel)
    websocket_api.async_register_command(hass, ws_voice_details)
    websocket_api.async_register_command(hass, ws_voice_threshold)


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
    _voice_picker(hass, entry, groups)
    from .voice_id import status as voice_id_status

    for group in groups:
        if group["id"] == "voiceid":
            group["voice_id"] = voice_id_status(hass, _current(entry))
            if not group["voice_id"]["installed"]:
                group["schema"] = []  # nothing to set until the add-on is there; the page shows how to get it
    connection.send_result(
        msg["id"],
        {
            "groups": groups,
            "data": _with_defaults(_current(entry)),
            "labels": labels,
            "helpers": helpers,
            "selector": selector_texts,
            "categories": MEMORY_CATEGORIES,
            "depends": DEPENDS,
        },
    )


# settings that only mean something while another one is switched on: the page hides them until it is
# (their values are kept, so switching back on brings them back as they were)
DEPENDS: dict[str, str] = {
    CONF_VOICE_ID_UNKNOWN: CONF_VOICE_ID,
    CONF_ROOM_HANDOFF: CONF_SESSIONS,
    CONF_BEDTIME_LIGHTS: CONF_BEDTIME,
    CONF_BEDTIME_CONTACTS: CONF_BEDTIME,
    **{k: "sleep_detection" for k in ("sleep_minutes", "sleep_ignore_sensors", "bedtime_routines", "sleep_actions", "wake_actions")},
    "zone_door_sensors": "zone_visits",
    **{k: "faces" for k in ("face_people", "face_cameras", "face_forget_hours")},
    **{k: "clip_watch" for k in ("clip_cameras", "clip_scope", "clip_when", "clip_notify", "clip_source", "clip_whose", "clip_looks", "clip_days", "clip_keep", "budget_clips")},
    **{k: "energy_insight" for k in ("energy_price", "energy_currency")},
    "foresight_time": "foresight", "reflection_time": "reflection",
    **{k: "welcome_home" for k in ("welcome_people", "welcome_min_away", "welcome_until")},
    "morning_briefing_areas": "morning_briefing",
    **{k: "night_watch" for k in ("night_watch_entities", "night_watch_announce", "night_watch_speakers")},
    "hutch_spot": "hutch_check", **{k: "cat_watch" for k in ("cat_sensors", "cat_loud")},
    **{k: "screen" for k in ("screen_actions", "screen_clear_actions")},
    **{k: "alexa_skill" for k in ("alexa_wait", "alexa_late_answers")},
    "self_adjust": "self_check",
}


def _voice_picker(hass: HomeAssistant, entry, groups: list[dict[str, Any]]) -> None:
    """The reading voice as Home Assistant's own voice picker, following the service chosen above it."""
    from .speech import tts_language

    engine = entry.options.get(CONF_READING_TTS) or entry.data.get(CONF_READING_TTS)
    language = (tts_language(hass, engine) if engine else None) or hass.config.language
    for group in groups:
        for field in group["schema"]:
            if field["name"] == CONF_READING_VOICE:
                field["selector"] = {"tts_voice": {"language": language}}
                field["context"] = {"engineId": CONF_READING_TTS}


@websocket_api.websocket_command({vol.Required("type"): "sentient/doctor"})
@websocket_api.require_admin
@websocket_api.async_response
async def ws_doctor(hass: HomeAssistant, connection, msg: dict[str, Any]) -> None:
    """The setup doctor: what's set up right and what to fix (read-only, no AI)."""
    entry = _entry(hass)
    if entry is None:
        connection.send_error(msg["id"], "not_loaded", "Sentient is not set up")
        return
    from .doctor import async_check

    connection.send_result(msg["id"], await async_check(hass, entry.runtime_data))


@websocket_api.websocket_command({vol.Required("type"): "sentient/voice_id"})
@websocket_api.require_admin
@callback
def ws_voice_id(hass: HomeAssistant, connection, msg: dict[str, Any]) -> None:
    """Is Sentient Voice Recognition installed, is it in use, and who was heard last."""
    from .voice_id import status

    entry = _entry(hass)
    connection.send_result(msg["id"], status(hass, _current(entry) if entry is not None else {}))


@websocket_api.websocket_command({vol.Required("type"): "sentient/voice_id/enrol", vol.Required("name"): vol.All(str, vol.Length(min=1, max=40)),
                                  vol.Required("satellite_id"): cv.entity_id, vol.Required("consent"): bool})
@websocket_api.require_admin
@websocket_api.async_response
async def ws_voice_enrol(hass: HomeAssistant, connection, msg: dict[str, Any]) -> None:
    """Open the add-on's "record from a speaker" window. Only works when the add-on is running and the setting is on."""
    from .voice_id import enabled, start_enrolment

    entry = _entry(hass)
    if entry is None or not enabled(hass, _current(entry)):
        connection.send_error(msg["id"], "not_available", "Voice recognition isn't running or isn't switched on.")
        return
    if not msg["consent"]:
        connection.send_error(msg["id"], "consent_required", "Confirm that the person agreed to enrol their voice.")
        return
    try:
        connection.send_result(msg["id"], await start_enrolment(hass, msg["name"], msg["satellite_id"]))
    except Exception as err:  # the add-on explains why (a bad name, a full list)
        connection.send_error(msg["id"], "failed", str(err))


@websocket_api.websocket_command({vol.Required("type"): "sentient/voice_id/enrol_cancel"})
@websocket_api.require_admin
@websocket_api.async_response
async def ws_voice_enrol_cancel(hass: HomeAssistant, connection, msg: dict[str, Any]) -> None:
    from .voice_id import cancel_enrolment

    await cancel_enrolment(hass)
    connection.send_result(msg["id"], {})


@websocket_api.websocket_command({vol.Required("type"): "sentient/voice_id/details"})
@websocket_api.require_admin
@websocket_api.async_response
async def ws_voice_details(hass: HomeAssistant, connection, msg: dict[str, Any]) -> None:
    """Saved voices, engine state and any match-level advice. Empty unless the add-on runs and the setting is on."""
    from .voice_id import RECENT_KEY, advice, details, enabled

    entry = _entry(hass)
    if entry is None or not enabled(hass, _current(entry)):
        connection.send_result(msg["id"], {"available": False})
        return
    detail = await details(hass)
    recent = list(hass.data.get(RECENT_KEY) or [])
    connection.send_result(msg["id"], {"available": True, "detail": detail, "advice": advice(detail, recent)})


@websocket_api.websocket_command({vol.Required("type"): "sentient/voice_id/threshold",
                                  vol.Required("value"): vol.All(vol.Coerce(float), vol.Range(min=0.5, max=0.9))})
@websocket_api.require_admin
@websocket_api.async_response
async def ws_voice_threshold(hass: HomeAssistant, connection, msg: dict[str, Any]) -> None:
    from .voice_id import ENROL_DOMAIN, enabled

    entry = _entry(hass)
    if entry is None or not enabled(hass, _current(entry)):
        connection.send_error(msg["id"], "not_available", "Voice recognition isn't running or isn't switched on.")
        return
    await hass.services.async_call(ENROL_DOMAIN, "set_match_threshold", {"value": msg["value"]}, blocking=True)
    connection.send_result(msg["id"], {})


SPEAKER_TEST_TIMEOUT = 30


@websocket_api.websocket_command({vol.Required("type"): "sentient/doctor/speakers"})
@websocket_api.require_admin
@websocket_api.async_response
async def ws_doctor_speakers(hass: HomeAssistant, connection, msg: dict[str, Any]) -> None:
    """Say a short test line on each speaker in turn and report which accepted it."""
    entry = _entry(hass)
    if entry is None:
        connection.send_error(msg["id"], "not_loaded", "Sentient is not set up")
        return
    rt = entry.runtime_data
    results = []
    import asyncio

    for sp in dict.fromkeys(rt.options.get("speakers") or []):
        st = hass.states.get(sp)
        row = {"speaker": st.name if st else sp, "entity_id": sp, "ok": False}
        if st is None:
            row["error"] = "Speaker no longer exists"
        else:
            try:
                async with asyncio.timeout(SPEAKER_TEST_TIMEOUT):
                    row["ok"] = await rt.speech.async_read_aloud(f"Testing — this is the {st.name}.", sp)
            except TimeoutError:
                row["error"] = "Speaker test timed out; playback is unconfirmed. No retry was sent."
            except Exception as err:
                row["error"] = (str(err) or type(err).__name__)[:200]
        results.append(row)
    connection.send_result(msg["id"], {"results": results,
        "note": "Acceptance does not independently confirm audibility."})


@websocket_api.websocket_command(
    {vol.Required("type"): "sentient/reading/try", vol.Optional("engine"): vol.Any(str, None),
     vol.Optional("voice"): vol.Any(str, None), vol.Optional("speaker"): vol.Any(str, None)}
)
@websocket_api.require_admin
@websocket_api.async_response
async def ws_reading_try(hass: HomeAssistant, connection, msg: dict[str, Any]) -> None:
    """Hear a line in the reading voice on the default speaker (or the one given)."""
    entry = _entry(hass)
    if entry is None:
        connection.send_error(msg["id"], "not_loaded", "Sentient is not set up")
        return
    opts = {**entry.data, **entry.options}
    speaker = msg.get("speaker") or opts.get("default_speaker") or next(iter(opts.get("speakers") or []), None)
    if not speaker:
        connection.send_error(msg["id"], "no_speaker", "Choose a default speaker under Speaking first")
        return
    out = await entry.runtime_data.speech.async_try_voice(speaker, msg.get("engine"), msg.get("voice"))
    st = hass.states.get(speaker)
    out["speaker_name"] = st.name if st else speaker
    connection.send_result(msg["id"], out)


def _games(hass: HomeAssistant):
    entry = _entry(hass)
    games = getattr(entry.runtime_data, "games", None) if entry is not None else None
    return games if games is not None and games.enabled else None


@websocket_api.websocket_command({vol.Required("type"): "sentient/games"})
@websocket_api.require_admin
@websocket_api.async_response
async def ws_games(hass: HomeAssistant, connection, msg: dict[str, Any]) -> None:
    games = _games(hass)
    if games is None:
        connection.send_result(msg["id"], {"enabled": False, "games": []})
        return
    connection.send_result(msg["id"], {"enabled": True, "games": games.listing(), "kinds": list(KINDS),
                                        "templates": games.template_listing()})


@websocket_api.websocket_command({
    vol.Required("type"): "sentient/games/edit",
    vol.Required("op"): vol.In(["save", "delete", "set", "remove", "restart", "start", "stop", "from_template"]),
    vol.Optional("template"): str, vol.Optional("values"): {str: vol.Any(str, None)},
    vol.Optional("game"): str, vol.Optional("name"): str, vol.Optional("setting"): str, vol.Optional("rules"): str,
    vol.Optional("kind"): str, vol.Optional("key"): str, vol.Optional("value"): str, vol.Optional("entry"): str,
})
@websocket_api.require_admin
@websocket_api.async_response
async def ws_games_edit(hass: HomeAssistant, connection, msg: dict[str, Any]) -> None:
    """Everything the Games page does to a game and its saved memory."""
    games = _games(hass)
    if games is None:
        connection.send_error(msg["id"], "off", "Story games are switched off in Settings")
        return
    op, gid = msg["op"], msg.get("game") or ""
    try:
        if op == "save":
            games.save_game(msg.get("name", ""), msg.get("setting", ""), msg.get("rules", ""), gid or None)
        elif op == "from_template":
            games.create_from_template(msg.get("template", ""), msg.get("values") or {})
        elif op == "stop":
            games.stop()
        elif gid not in games.games:
            raise ValueError("No such game")
        elif op == "delete":
            games.delete_game(gid)
        elif op == "set":
            games.set_entry(gid, msg.get("kind", "note"), msg.get("key", ""), msg.get("value", ""), msg.get("entry"))
        elif op == "remove":
            games.remove_entry(gid, msg.get("entry") or msg.get("key", ""))
        elif op == "restart":
            games.restart(gid)
        else:
            games.start(gid)
    except ValueError as err:
        connection.send_error(msg["id"], "invalid", str(err))
        return
    connection.send_result(msg["id"], {"games": games.listing()})


@websocket_api.websocket_command({vol.Required("type"): "sentient/reading/stop"})
@websocket_api.require_admin
@websocket_api.async_response
async def ws_reading_stop(hass: HomeAssistant, connection, msg: dict[str, Any]) -> None:
    """Stop every story being read and silence the speakers: the way out when the voice can't be used."""
    entry = _entry(hass)
    books = getattr(entry.runtime_data, "books", None) if entry is not None else None
    if books is None:
        connection.send_error(msg["id"], "not_loaded", "Sentient is not set up")
        return
    connection.send_result(msg["id"], await books.async_silence())


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
        vol.Optional("pinned"): bool,
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
    if "pinned" in msg:
        entry.runtime_data.memory.pin(item.id, msg["pinned"])
    connection.send_result(msg["id"], {"id": item.id, "text": item.text, "category": item.category, "pinned": item.pinned})


@websocket_api.websocket_command({vol.Required("type"): "sentient/memories"})
@page_access
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
@page_access
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
@websocket_api.require_admin  # sends a camera picture to the AI
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
@page_access
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
            vol.Optional("quiet_start"): vol.Any(None, str),
            vol.Optional("quiet_end"): vol.Any(None, str),
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
    {vol.Required("type"): "sentient/timeline", vol.Optional("hours", default=48): vol.All(vol.Coerce(float), vol.Range(min=0.1, max=720)),
     vol.Optional("kinds"): [str], vol.Optional("limit", default=300): vol.All(int, vol.Range(min=1, max=1000))}
)
@page_access
@callback
def ws_timeline(hass: HomeAssistant, connection, msg: dict[str, Any]) -> None:
    entry = _entry(hass)
    if entry is None:
        connection.send_error(msg["id"], "not_loaded", "Sentient is not set up")
        return
    items = entry.runtime_data.timeline.since(msg["hours"], msg.get("kinds"), msg["limit"])
    connection.send_result(msg["id"], {"items": items})


@websocket_api.websocket_command({vol.Required("type"): "sentient/digest", vol.Optional("hours", default=24): vol.Coerce(float)})
@page_access
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
@page_access
@callback
def ws_mutes(hass: HomeAssistant, connection, msg: dict[str, Any]) -> None:
    entry = _entry(hass)
    if entry is None:
        connection.send_error(msg["id"], "not_loaded", "Sentient is not set up")
        return
    mutes = entry.runtime_data.mutes
    if any(msg.get(k) for k in ("clear", "remove", "unlearn", "unlearn_all")) and not connection.user.is_admin:
        connection.send_error(msg["id"], "unauthorized", "Only an administrator can change what Sentient has learned")
        return
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
@page_access
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


@websocket_api.websocket_command({
    vol.Required("type"): "sentient/feedback/style", vol.Required("text"): str, vol.Required("note"): str,
})
@websocket_api.require_admin  # it changes the integration's settings (the style notes)
@callback
def ws_style(hass: HomeAssistant, connection, msg: dict[str, Any]) -> None:
    """'Say it differently': a short note on how something should have been said, kept with the personality."""
    entry = _entry(hass)
    if entry is None:
        connection.send_error(msg["id"], "not_loaded", "Sentient is not set up")
        return
    opts = {**entry.data, **entry.options}
    if not opts.get(CONF_STYLE_FEEDBACK, True):
        connection.send_error(msg["id"], "off", "'Say it differently' is turned off in Sentient's settings")
        return
    note = " ".join(msg["note"].split()).strip(" .")[:120]
    if not note:
        connection.send_error(msg["id"], "invalid", "What should be different?")
        return
    notes = [n.strip() for n in str(opts.get(CONF_STYLE_NOTES) or "").splitlines() if n.strip()]
    notes = [n for n in notes if n.lower() != note.lower()] + [note]
    hass.config_entries.async_update_entry(entry, options={**opts, CONF_STYLE_NOTES: "\n".join(notes[-MAX_STYLE_NOTES:])})
    entry.runtime_data.timeline.add("learned", f"Say it differently: {note}", f"about: {msg['text'][:100]}")
    connection.send_result(msg["id"], {"notes": notes[-MAX_STYLE_NOTES:]})


# ----- named routines -------------------------------------------------------------
@websocket_api.websocket_command({vol.Required("type"): "sentient/shortcuts"})
@page_access
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


@websocket_api.websocket_command({vol.Required("type"): "sentient/backups"})
@websocket_api.require_admin
@websocket_api.async_response
async def ws_backups(hass: HomeAssistant, connection, msg: dict[str, Any]) -> None:
    entry = _entry(hass)
    if entry is None:
        connection.send_error(msg["id"], "not_loaded", "Sentient is not set up")
        return
    connection.send_result(msg["id"], {"items": await entry.runtime_data.autobackup.async_list(),
                                       "on": bool(entry.runtime_data.options.get("auto_backup"))})


@websocket_api.websocket_command({
    vol.Required("type"): "sentient/backups/restore", vol.Required("name"): str,
    vol.Optional("settings", default=False): bool,
})
@websocket_api.require_admin
@websocket_api.async_response
async def ws_backup_restore(hass: HomeAssistant, connection, msg: dict[str, Any]) -> None:
    entry = _entry(hass)
    if entry is None:
        connection.send_error(msg["id"], "not_loaded", "Sentient is not set up")
        return
    try:
        result = await entry.runtime_data.autobackup.async_restore(msg["name"], msg["settings"])
    except (OSError, KeyError, TypeError, ValueError) as err:
        connection.send_error(msg["id"], "invalid", f"Couldn't restore that backup ({err})")
        return
    connection.send_result(msg["id"], result)


@websocket_api.websocket_command({vol.Required("type"): "sentient/backups/save"})
@websocket_api.require_admin
@websocket_api.async_response
async def ws_backup_save(hass: HomeAssistant, connection, msg: dict[str, Any]) -> None:
    entry = _entry(hass)
    if entry is None:
        connection.send_error(msg["id"], "not_loaded", "Sentient is not set up")
        return
    connection.send_result(msg["id"], {"name": await entry.runtime_data.autobackup.async_save()})


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
@page_access
@websocket_api.async_response
async def ws_energy(hass: HomeAssistant, connection, msg: dict[str, Any]) -> None:
    from .energy import async_report

    try:
        connection.send_result(msg["id"], await async_report(hass))
    except Exception as err:  # noqa: BLE001
        connection.send_error(msg["id"], "failed", str(err))


@websocket_api.websocket_command({vol.Required("type"): "sentient/security"})
@page_access
@callback
def ws_security(hass: HomeAssistant, connection, msg: dict[str, Any]) -> None:
    from homeassistant.auth.permissions.const import POLICY_READ

    from .security import check

    connection.send_result(msg["id"], check(
        hass, visible=lambda eid: connection.user.permissions.check_entity(eid, POLICY_READ)
    ))


@websocket_api.websocket_command({vol.Required("type"): "sentient/security/frigate_health"})
@page_access
@callback
def ws_frigate_health(hass: HomeAssistant, connection, msg: dict[str, Any]) -> None:
    if (entry := _entry(hass)) is None:
        connection.send_error(msg["id"], "not_loaded", "Sentient is not set up")
        return
    rt = entry.runtime_data
    result = {"enabled": False} if rt.speech.guest_mode else rt.frigate_health.report(connection.user)
    connection.send_result(msg["id"], result)


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
@page_access
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
@page_access
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
    from .kinds import KINDS, belongs, store_of, summary

    meta = KINDS[kind]
    rt = entry.runtime_data
    items = [i for i in rt.lists[store_of(kind)].listing() if belongs(kind, i)]
    for item in items:
        item["_summary"] = summary(kind, item, hass)
    if store_of(kind) == "alarms":
        nexts = {a["id"]: a["next"] for a in rt.alarms.listing()}
        for item in items:
            item["_next"] = nexts.get(item["id"])
    out = {
        "items": items,
        "meta": {k: meta[k] for k in ("title", "icon", "blurb", "add", "schema", "labels", "defaults")},
    }
    if kind == "schedules":  # learned habits the house was asked to take over
        out["learned"] = [{"id": r.id, "text": r.describe()} for r in rt.learner.all() if getattr(r, "adopted", False)]
    return out


from .kinds import KINDS as _ALL_KINDS  # noqa: E402

_KIND = vol.In(list(_ALL_KINDS))  # every list kind, so a new one never needs adding here


@websocket_api.websocket_command({vol.Required("type"): "sentient/items", vol.Required("kind"): _KIND})
@page_access
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
        if msg["kind"] == "rules":
            from .rules import safe_actions

            if why := safe_actions(item.get("actions") or []):
                raise ValueError(why)
        from .kinds import store_of

        if store_of(msg["kind"]) == "alarms":
            item["silent"] = msg["kind"] == "schedules"  # alarms speak; automated routines just do things
            item["made_by"] = "page"  # set by an admin on the page: run as set (anything else is checked when it rings)
        entry.runtime_data.lists[store_of(msg["kind"])].upsert(item)
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
    from .kinds import store_of

    entry.runtime_data.lists[store_of(msg["kind"])].remove(msg["item_id"])
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
    from .kinds import store_of

    rt = entry.runtime_data
    item = rt.lists[store_of(msg["kind"])].items.get(msg["item_id"])
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
        elif kind == "schedules":
            from .shortcuts import async_run_actions

            await async_run_actions(hass, item.get("actions") or [], item["name"])
            result = {"ran": item["name"]}
        elif kind == "autooff":
            on = [e for e in rt.autooff.targets(item) if hass.states.get(e) and hass.states.get(e).state == "on"]
            accepted = await rt.autooff.async_switch_off(item, on, force=True)
            result = {"switched_off": accepted, "note": "Service requests accepted; physical results are not independently verified."}
        else:
            result = await rt.checks.async_check(item, force=True)
    except Exception as err:  # noqa: BLE001
        connection.send_error(msg["id"], "failed", str(err))
        return
    connection.send_result(msg["id"], {**_items_payload(hass, entry, kind), "result": result})


@websocket_api.websocket_command({
    vol.Required("type"): "sentient/voice_check",
    vol.Optional("action", default="status"): vol.In(["status", "start", "start_single", "start_question", "start_cancel", "clear", "heard", "not_heard"]),
    vol.Optional("run_id"): str,
})
@websocket_api.require_admin
@websocket_api.async_response
async def ws_voice_check(hass: HomeAssistant, connection, msg):
    entry = _entry(hass)
    if entry is None:
        connection.send_error(msg["id"], "not_loaded", "Sentient is not set up")
        return
    rt = entry.runtime_data
    if rt.speech.guest_mode:
        connection.send_error(msg["id"], "private", "Voice checks are unavailable in guest mode")
        return
    if not rt.options.get(CONF_LIVE_VOICE_CHECK, True):
        connection.send_error(msg["id"], "disabled", "Enable Live voice check in Settings → Voice first")
        return
    action = msg["action"]
    try:
        if action in ("start", "start_single", "start_question", "start_cancel"):
            if not rt.options.get(CONF_SESSIONS):
                raise ValueError("Enable Conversation sessions and use the Sentient with backup agent in your voice pipeline")
            await rt.voice_checks.async_start({"start": "conversation", "start_single": "single", "start_question": "question", "start_cancel": "cancel"}[action])
        elif action == "clear":
            rt.voice_checks.stop()
        elif action in ("heard", "not_heard"):
            rt.voice_checks.confirm_heard(msg.get("run_id"), action == "heard")
        connection.send_result(msg["id"], rt.voice_checks.status())
    except ValueError as err:
        connection.send_error(msg["id"], "invalid", str(err))


@websocket_api.websocket_command({vol.Required("type"): "sentient/update_check"})
@websocket_api.require_admin
@websocket_api.async_response
async def ws_update_check(hass, connection, msg):
    entry = _entry(hass)
    if entry is None:
        connection.send_error(msg["id"], "not_loaded", "Sentient is not set up")
        return
    checker = entry.runtime_data.updates
    if not checker.enabled:
        connection.send_error(msg["id"], "disabled", "Enable the update checker in Settings → Updates first")
        return
    connection.send_result(msg["id"], await checker.async_check())
