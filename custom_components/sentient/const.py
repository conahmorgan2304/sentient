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

DEFAULT_PERSONA = "Sentient"
DEFAULT_QUIET_START = "21:00:00"
DEFAULT_QUIET_END = "08:00:00"
DEFAULT_MOTION_WINDOW = 10  # minutes of activity history used to find people
DEFAULT_BANNED_WORDS = "sir, madam"
DEFAULT_SUPPRESS_SECONDS = 60
DEFAULT_PROMPT_MEMORIES = 25
DEFAULT_ALL_OCCUPIED = True

# Speech priorities
PRIORITY_LOW = "low"
PRIORITY_NORMAL = "normal"
PRIORITY_URGENT = "urgent"
PRIORITIES = [PRIORITY_LOW, PRIORITY_NORMAL, PRIORITY_URGENT]

# Services
SERVICE_SAY = "say"
SERVICE_REMEMBER = "remember"
SERVICE_FORGET = "forget"

# Events
EVENT_SPOKE = f"{DOMAIN}_spoke"
EVENT_HELD = f"{DOMAIN}_held"
EVENT_MEMORY_CHANGED = f"{DOMAIN}_memory_changed"

# Memory
MEMORY_CATEGORIES = ["fact", "preference", "person", "routine", "note"]
MEMORY_STORAGE_VERSION = 1
MEMORY_MAX_ITEMS = 500

LLM_API_ID = DOMAIN
