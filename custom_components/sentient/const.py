"""Constants for Sentient."""

from __future__ import annotations

DOMAIN = "sentient"

# Config / options keys
CONF_PERSONA = "persona"
CONF_SPEAKERS = "speakers"
CONF_DEFAULT_SPEAKER = "default_speaker"
CONF_QUIET_START = "quiet_start"
CONF_QUIET_END = "quiet_end"
CONF_MOTION_WINDOW = "motion_window"
CONF_BANNED_WORDS = "banned_words"
CONF_SUPPRESS_ENTITIES = "suppress_entities"
CONF_SUPPRESS_SECONDS = "suppress_seconds"
CONF_PROMPT_MEMORIES = "prompt_memories"
CONF_PASS_THROUGH_AREAS = "pass_through_areas"
CONF_ALL_OCCUPIED = "all_occupied"
CONF_AI_TASK_ENTITY = "ai_task_entity"
CONF_CAMERA_COOLDOWN = "camera_cooldown"
CONF_FALLBACK_SPEAKERS = "fallback_speakers"
CONF_DAILY_AI_LIMIT = "daily_ai_limit"
CONF_NOTICE_ENTITIES = "notice_entities"
CONF_CHATTINESS = "chattiness"
CONF_HOUSE_RULES = "house_rules"
CONF_LEARN_ENTITIES = "learn_entities"
CONF_SUGGEST_ROUTINES = "suggest_routines"
CONF_DOOR_CAMERA = "door_camera"
CONF_DOOR_TRIGGERS = "door_triggers"
CONF_ANNOUNCE_VISITORS = "announce_visitors"
CONF_PARCEL_ALERTS = "parcel_alerts"
CONF_FACE_SENSOR = "face_sensor"
CONF_BRIEFINGS = "briefings"
CONF_BRIEFING_SECTIONS = "briefing_sections"
CONF_BRIEFING_WEATHER = "briefing_weather"
CONF_BRIEFING_TODO = "briefing_todo"
CONF_BRIEFING_CALENDARS = "briefing_calendars"
CONF_BRIEFING_SYSTEM = "briefing_system"
CONF_BRIEFING_TIME = "briefing_time"
BRIEFING_SECTIONS = [
    "time", "weather", "reminders", "watches", "routines", "open",
    "visitors", "said", "system", "todo", "calendar",
]
DEFAULT_BRIEFING_SECTIONS = ["time", "weather", "reminders", "watches", "routines", "open", "visitors"]

DEFAULT_PERSONA = "Sentient"
DEFAULT_QUIET_START = "21:00:00"
DEFAULT_QUIET_END = "08:00:00"
DEFAULT_MOTION_WINDOW = 10  # minutes of activity history used to find people
DEFAULT_BANNED_WORDS = "sir, madam"
DEFAULT_SUPPRESS_SECONDS = 60
DEFAULT_PROMPT_MEMORIES = 25
DEFAULT_ALL_OCCUPIED = True
DEFAULT_DAILY_AI_LIMIT = 100  # background camera checks per day
DEFAULT_CHATTINESS = "quiet"
DEFAULT_SUGGEST_ROUTINES = True
DEFAULT_ANNOUNCE_VISITORS = False
DEFAULT_PARCEL_ALERTS = True
CHATTINESS_LIMITS = {"off": 0, "quiet": 2, "normal": 4, "chatty": 8}  # remarks per hour
DEFAULT_CAMERA_COOLDOWN = 60  # seconds between AI looks at the same camera watch

# Speech priorities
PRIORITY_LOW = "low"
PRIORITY_NORMAL = "normal"
PRIORITY_URGENT = "urgent"
PRIORITIES = [PRIORITY_LOW, PRIORITY_NORMAL, PRIORITY_URGENT]

# Services
SERVICE_SAY = "say"
SERVICE_REMEMBER = "remember"
SERVICE_FORGET = "forget"
SERVICE_WATCH = "watch"
SERVICE_CANCEL_WATCH = "cancel_watch"
SERVICE_REMIND = "remind"
SERVICE_ASK = "ask"
SERVICE_LEARN = "learn"
SERVICE_BRIEFING = "briefing"
SERVICE_TIDY = "tidy_memories"
SERVICE_ADOPT = "adopt_routine"
STATIC_URL = "/sentient_static"
FRONTEND_URL = "/sentient_frontend"
PANEL_URL_PATH = "sentient"
CONF_SIDEBAR = "sidebar_panel"
DEFAULT_SIDEBAR = True

# Events
EVENT_SPOKE = f"{DOMAIN}_spoke"
EVENT_HELD = f"{DOMAIN}_held"
EVENT_MEMORY_CHANGED = f"{DOMAIN}_memory_changed"
EVENT_WATCH_CHANGED = f"{DOMAIN}_watch_changed"
EVENT_WATCH_FIRED = f"{DOMAIN}_watch_fired"
EVENT_NOTICED = f"{DOMAIN}_noticed"
EVENT_ROUTINES_CHANGED = f"{DOMAIN}_routines_changed"
EVENT_VISITOR = f"{DOMAIN}_visitor"

# Memory
MEMORY_CATEGORIES = ["fact", "preference", "person", "routine", "note"]
MEMORY_STORAGE_VERSION = 1
MEMORY_MAX_ITEMS = 500

LLM_API_ID = DOMAIN
