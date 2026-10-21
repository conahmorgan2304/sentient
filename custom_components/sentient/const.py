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

DEFAULT_PERSONA = "Sentient"
DEFAULT_QUIET_START = "21:00:00"
DEFAULT_QUIET_END = "08:00:00"
DEFAULT_MOTION_WINDOW = 10  # minutes of activity history used to find people
DEFAULT_BANNED_WORDS = "sir, madam"
DEFAULT_SUPPRESS_SECONDS = 60
DEFAULT_PROMPT_MEMORIES = 25
DEFAULT_ALL_OCCUPIED = True
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

# Events
EVENT_SPOKE = f"{DOMAIN}_spoke"
EVENT_HELD = f"{DOMAIN}_held"
EVENT_MEMORY_CHANGED = f"{DOMAIN}_memory_changed"
EVENT_WATCH_CHANGED = f"{DOMAIN}_watch_changed"
EVENT_WATCH_FIRED = f"{DOMAIN}_watch_fired"

# Memory
MEMORY_CATEGORIES = ["fact", "preference", "person", "routine", "note"]
MEMORY_STORAGE_VERSION = 1
MEMORY_MAX_ITEMS = 500

LLM_API_ID = DOMAIN
