"""Constants for Sentient."""

from __future__ import annotations

DOMAIN = "sentient"

# Config / options keys
CONF_PERSONA = "persona"
CONF_SPEAKERS = "speakers"
CONF_DEFAULT_SPEAKER = "default_speaker"
CONF_QUIET_START = "quiet_start"
CONF_BUSY_ENTITIES = "busy_entities"  # while any is on, non-urgent speech is held back like a hush
CONF_QUIET_END = "quiet_end"
CONF_MOTION_WINDOW = "motion_window"
CONF_BANNED_WORDS = "banned_words"
CONF_SUPPRESS_ENTITIES = "suppress_entities"
CONF_SUPPRESS_SECONDS = "suppress_seconds"
CONF_PROMPT_MEMORIES = "prompt_memories"
CONF_PASS_THROUGH_AREAS = "pass_through_areas"
CONF_ALL_OCCUPIED = "all_occupied"
CONF_AI_TASK_ENTITY = "ai_task_entity"
CONF_SECOND_OPINION = "second_opinion_ai"
CONF_NOTIFY_TARGETS = "notify_targets"
CONF_CHIME = "chime"
CONF_DIGEST_TIME = "digest_time"
CONF_MORNING_BRIEFING = "morning_briefing"
CONF_MORNING_BRIEFING_AREAS = "morning_briefing_areas"  # only when someone first comes into one of these rooms
CONF_WELCOME = "welcome_home"
CONF_SPEAK_VOLUME = "speak_volume"
CONF_NIGHT_VOLUME = "night_volume"
CONF_DUCK_MEDIA = "duck_media"
CONF_CRITICAL = "critical_alerts"
CONF_ENERGY = "energy_insight"
CONF_LEFT_OPEN_MINUTES = "left_open_minutes"
CONF_LEFT_OPEN_IGNORE = "left_open_ignore"
CONF_LEAVING_CHECK = "leaving_check"
CONF_ENERGY_PRICE = "energy_price"
CONF_REMARK_REPEAT_HOURS = "remark_repeat_hours"  # the same remark isn't said again for this long
DEFAULT_REMARK_REPEAT_HOURS = 3
CONF_SEARCH_HINTS = "search_hints"  # tell the voice when web/image/video search tools are there
CONF_WEB_SEARCH = "web_search"  # Sentient's own voice-only web search, through your SearXNG
CONF_SEARXNG_URL = "searxng_url"
SEARCH_COMPANION = "voice_satellite_llm_tools"
SEARCH_COMPANION_URL = "https://github.com/jxlarrea/voice-satellite-card-llm-tools"
CONF_ALEXA_SKILL = "alexa_skill"  # answer questions asked through an Alexa skill
CONF_ALEXA_WAIT = "alexa_wait"
CONF_ALEXA_LATE = "alexa_late_answers"
DEFAULT_ALEXA_WAIT = 6
CONF_MUSIC = "music"  # music by asking, through Music Assistant (only when it's set up)
CONF_PRICE_ENTITY = "price_entity"  # time-of-use price: cheap-electricity suggestions (empty: off)
CONF_ENERGY_CURRENCY = "energy_currency"  # empty: Home Assistant's own currency
CURRENCIES = ["GBP", "EUR", "USD", "CAD", "AUD", "NZD", "CHF", "SEK", "NOK", "DKK", "PLN", "CZK", "ZAR", "INR", "JPY"]
CURRENCY_SYMBOLS = {"GBP": "£", "EUR": "€", "USD": "$", "CAD": "CA$", "AUD": "A$", "NZD": "NZ$", "CHF": "CHF ",
                    "SEK": "kr ", "NOK": "kr ", "DKK": "kr ", "PLN": "zł ", "CZK": "Kč ", "ZAR": "R", "INR": "₹", "JPY": "¥"}
CONF_CRITICAL_IGNORE = "critical_ignore"
CONF_CRITICAL_EXTRA = "critical_extra"
CONF_WELCOME_PEOPLE = "welcome_people"
CONF_WELCOME_MIN_AWAY = "welcome_min_away"  # minutes away before a welcome
CONF_WELCOME_UNTIL = "welcome_until"  # welcome in the evening until this time, even in quiet hours
DEFAULT_WELCOME_MIN_AWAY = 20
DEFAULT_CHIME = True
CONF_NOTIFY_WHEN = "notify_when"
NOTIFY_KINDS = ["critical", "security", "urgent", "held_quiet", "unheard", "visitors", "watches", "health", "digest"]
DEFAULT_NOTIFY_WHEN = ["critical", "security", "urgent", "unheard", "visitors"]
CONF_CAMERA_COOLDOWN = "camera_cooldown"
CONF_FALLBACK_SPEAKERS = "fallback_speakers"
CONF_DAILY_AI_LIMIT = "daily_ai_limit"
CONF_APPLIANCES = "appliances"
CONF_APPLIANCE_ANNOUNCE = "appliance_announce"
CONF_APPLIANCE_WATTS = "appliance_watts"
CONF_NOTICE_ENTITIES = "notice_entities"
CONF_CHATTINESS = "chattiness"
CONF_TALK_STYLE = "conversation_style"  # brief / natural
TALK_STYLES = ["brief", "natural"]
DEFAULT_TALK_STYLE = "brief"
CONF_CARRY_ON_MINUTES = "carry_on_minutes"  # a new wake word this soon carries on the last conversation
DEFAULT_CARRY_ON_MINUTES = 0
MAX_CARRY_ON_MINUTES = 5  # Home Assistant forgets a conversation after five quiet minutes
CONF_FOCUSED_TOOLS = "focused_tools"  # only the Sentient tools that fit the request
CONF_ASK_LATER = "ask_later"  # remember to ask how something went
CONF_PUT_RIGHT = "put_right"  # "no, the other one": switch back what the voice just switched, then redo
CONF_NEVER_ENTITIES = "never_entities"  # devices Sentient must never change by itself
CONF_SELF_CHECK = "self_check"  # nightly self-check from its own records (no AI)
CONF_SELF_ADJUST = "self_adjust"  # quieten remark kinds the house keeps hushing
CONF_TRUST_LIGHTS = "trust_lights"  # do / tell / ask before the house switches these by itself
CONF_TRUST_SWITCHES = "trust_switches"
CONF_TRUST_COVERS = "trust_covers"
CONF_TRUST_HEATING = "trust_heating"
TRUST_LEVELS = ["do", "tell", "ask"]
CONF_PRIVACY_LOG = "privacy_log"  # keep a record of what leaves the house (metadata only)
CONF_PROPOSALS = "proposals"  # suggest small settings changes from the self-check, applied only on a yes
CONF_MANUALS = "manuals"  # answer from documents in a media folder
CONF_MANUALS_FOLDER = "manuals_folder"
DEFAULT_MANUALS_FOLDER = "manuals"
CONF_GOALS = "goals"  # goals over days or weeks
CONF_BOOKS = "books"  # audiobooks and ebooks read aloud, with bookmarks
CONF_BOOKS_FOLDER = "books_folder"
DEFAULT_BOOKS_FOLDER = "books"
CONF_READING_TTS = "reading_tts"  # a different text-to-speech service for books (blank: the usual voice)
CONF_READING_VOICE = "reading_voice"  # that service's voice
CONF_READ_AHEAD = "read_ahead"  # make the next piece's audio while one is read
READING_SAMPLE = ("Once upon a time, in a house at the end of a quiet lane, a small fox curled up by the fire, "
                  "and waited for the story to begin.")
CONF_RECIPES = "recipes"  # recipes and a step-by-step cooking mode
CONF_RECIPES_FOLDER = "recipes_folder"
DEFAULT_RECIPES_FOLDER = "recipes"
CONF_RECIPES_LIST = "recipes_list"  # shopping list for ingredients
CONF_TANDOOR_URL = "tandoor_url"
CONF_TANDOOR_TOKEN = "tandoor_token"
CONF_HOUSE_RULES = "house_rules"
CONF_LEARN_ENTITIES = "learn_entities"
CONF_OFFER_TOGETHER = "offer_together"  # habits done at the same time offered as one
CONF_LEARN_ANCHORS = "learn_anchors"  # habits learned against sunset, sunrise or getting up, not just the clock
CONF_SUGGEST_ROUTINES = "suggest_routines"
CONF_LINK_TRIGGERS = "link_triggers"
CONF_DOOR_CAMERA = "door_camera"
CONF_DOOR_TRIGGERS = "door_triggers"
CONF_ANNOUNCE_VISITORS = "announce_visitors"
CONF_PARCEL_ALERTS = "parcel_alerts"
CONF_FACE_SENSOR = "face_sensor"
CONF_DOORSTEP_SENSORS = "doorstep_sensors"
CONF_ZONE_VISITS = "zone_visits"
CONF_ZONE_DOOR_SENSORS = "zone_door_sensors"
CONF_BRIEFINGS = "briefings"
CONF_BRIEFING_SECTIONS = "briefing_sections"
CONF_BRIEFING_WEATHER = "briefing_weather"
CONF_BRIEFING_TODO = "briefing_todo"
CONF_BRIEFING_CALENDARS = "briefing_calendars"
CONF_BRIEFING_SYSTEM = "briefing_system"
CONF_OUTSIDE_INFO = "outside_info"  # sensors read into the briefing and the look at tomorrow (prices, warnings…)
CONF_PARCEL_TRACKERS = "parcel_trackers"  # "out for delivery" sets a door watch for the day
CONF_BRIEFING_TIME = "briefing_time"
BRIEFING_SECTIONS = [
    "time", "weather", "reminders", "watches", "routines", "open",
    "visitors", "said", "system", "todo", "calendar", "energy", "security",
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
DEFAULT_DAILY_AI_LIMIT = 100  # background AI calls per day (0 = no limit)
DEFAULT_APPLIANCE_WATTS = 5
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
SERVICE_FORGET_ROUTINE = "forget_routine"
STATIC_URL = "/sentient_static"
FRONTEND_URL = "/sentient_frontend"
PANEL_URL_PATH = "sentient"
CONF_SIDEBAR = "sidebar_panel"
CONF_PAGE_ADMIN_ONLY = "page_admin_only"  # only administrators can open the Sentient page
CONF_VOICE_UNLOCK = "voice_unlock"  # guarded voice control may unlock doors (off: it can still lock them)
CONF_CHAT_AGENT = "chat_agent"
CONF_VOICE_AGENT = "voice_agent"
CONF_BACKUP_AGENT = "backup_agent"
DEFAULT_BACKUP_AGENT = "conversation.home_assistant"
CONF_ANIMAL_SENSORS = "animal_sensors"
CONF_AWAY_LIGHTS_ON = "away_lights_on"
CONF_LOOK_CACHE = "look_cache_minutes"  # reuse a camera answer to the same question for this long (0 = never)
DEFAULT_LOOK_CACHE = 3
CONF_CAMERA_PREFILTER = "camera_prefilter"  # skip camera checks and watches when the picture hasn't changed
CONF_BUDGET_DRIVE = "budget_drive"  # per-feature daily AI caps; 0 = share the overall limit
CONF_BUDGET_NOTICING = "budget_noticing"
CONF_BUDGET_CAMERAS = "budget_cameras"
CONF_ROUTINE_EVIDENCE = "routine_evidence"  # routine offers say how often it's been seen
CONF_SUGGESTIONS = "suggestions"  # the Sentient page offers things it could use
CONF_SIMPLE = "simple_settings"  # the settings page shows the essentials first (new installs)
CONF_WHATS_NEW = "whats_new"  # what's new after an update, on the Sentient page
CONF_CONFIRM = "confirm_kinds"  # the voice asks "shall I?" before these; empty = never
CONFIRM_KINDS = {
    "locks": "unlock or lock a door",
    "garage": "open or close a garage door or gate",
    "covers": "open or close blinds, curtains or shutters",
    "heating": "turn the heating up by more than 2 degrees or set it above 22",
    "appliances": "start an appliance (washing machine, dishwasher, dryer, vacuum)",
}
CONF_SELF_TEST = "self_test"  # off / nightly / weekly
SELF_TEST_MODES = ["off", "nightly", "weekly"]
CLIP_SCOPES = ["alerts", "zones", "all"]
CLIP_WHENS = ["always", "quiet", "away", "quiet_or_away"]
CLIP_NOTIFY_MODES = ["off", "worth", "suspicious", "all"]
CLIP_SOURCES = ["sentient", "frigate"]
CONF_RAIN = "rain_warnings"
CONF_RAIN_ENTITIES = "rain_entities"
CONF_HUTCH_CHECK = "hutch_check"
CONF_HUTCH_SPOT = "hutch_spot"
DEFAULT_HUTCH_SPOT = "rabbit pen"
CONF_CAT_WATCH = "cat_watch"
CONF_CAT_SENSORS = "cat_sensors"
CONF_CAT_LOUD = "cat_loud"
CONF_AUTO_BACKUP = "auto_backup"
CONF_BACKUP_KEEP = "backup_keep"
DEFAULT_BACKUP_KEEP = 4
CONF_BATTERY_FORECAST = "battery_forecast"  # when batteries will run out, in the weekly report
CONF_BINS_CHECK = "bins_check"
CONF_BINS_SPOT = "bins_spot"
DEFAULT_BINS_SPOT = "bins"
CONF_BINS_CALENDAR = "bins_calendar"
CONF_BINS_TIME = "bins_time"
DEFAULT_BINS_TIME = "19:00:00"
CONF_PHONE_BUTTONS = "phone_buttons"  # which notifications get buttons: door, reminders
PHONE_BUTTON_KINDS = ["door", "reminders", "questions"]
CONF_PHONE_APPS = "phone_apps"  # the companion app's notify actions (notify.mobile_app_…) that can show buttons
CONF_SMART_AI = "smart_ai_task"  # optional stronger AI for camera security and the overnight think; empty = the usual one
CONF_FACES = "faces"  # names from face recognition (Frigate, doorbells); private, only when asked
CONF_FACE_PEOPLE = "face_people"
CONF_FACE_TEACH = "face_teach"  # ask on the phone who an unnamed face was, and teach Frigate
CONF_FACE_TEACH_CAMERAS = "face_teach_cameras"
CONF_FACE_CAMERAS = "face_cameras"  # only names from these cameras count (empty: all)
CONF_FACE_FORGET_HOURS = "face_forget_hours"  # delete unnamed faces in Frigate after this many hours (0: leave to Frigate)
CONF_CLIP_WATCH = "clip_watch"  # watch Frigate's clips on chosen cameras and write down what happened
CONF_CLIP_CAMERAS = "clip_cameras"
CONF_CLIP_SCOPE = "clip_scope"  # alerts / zones / all
CONF_CLIP_WHEN = "clip_when"  # always / quiet / away / quiet_or_away
CONF_CLIP_NOTIFY = "clip_notify"  # off / worth / suspicious / all
CONF_CLIP_SOURCE = "clip_source"  # sentient / frigate
CONF_CLIP_WHOSE = "clip_whose"  # compare with the household's face photos in the same call
CONF_CLIP_LOOKS = "clip_looks"  # "Alex: motorbike helmet and riding jacket" — for when a face can't be seen
CONF_CLIP_DAYS = "clip_days"
CONF_CLIP_KEEP = "clip_keep"
CONF_BUDGET_CLIPS = "budget_clips"
DEFAULT_BUDGET_CLIPS = 30
CONF_RULES = "rules"  # "when this, do that" rules, and making them by voice
CONF_RELEVANT_MEMORIES = "relevant_memories"  # pick memories that match what was asked, not just the newest
CONF_KNOWN_CARS = "known_cars"  # "Sam: dark grey hatchback", one per line; empty = cars aren't named
CONF_EVENT_CLIPS = "event_clips"  # watch Frigate's clip of an event, not just a still
CONF_PROMPT_RECENT = "prompt_recent"  # the last hour and busy rooms in the voice's context
CONF_AWAY_PEOPLE = "away_people"
CONF_AWAY_LIGHTS = "away_lights"
CONF_AWAY_AFTER = "away_after"
CONF_HOLIDAY = "holiday_mode"  # holiday mode available (a switch and by voice)
CONF_HOLIDAY_THERMOSTATS = "holiday_thermostats"  # turned down to frost protection on holiday (none unless chosen)
CONF_HOLIDAY_FROST = "holiday_frost"
CONF_HOLIDAY_END_ON_RETURN = "holiday_end_on_return"
DEFAULT_HOLIDAY_FROST = 7
DEFAULT_AWAY_AFTER = 30
CONF_SCREEN = "screen"
CONF_SCREEN_ACTIONS = "screen_actions"
CONF_SCREEN_CLEAR_ACTIONS = "screen_clear_actions"
CONF_ANIMAL_LOW = "animal_low"
CONF_ANIMAL_HIGH = "animal_high"
DEFAULT_ANIMAL_LOW = 3
DEFAULT_ANIMAL_HIGH = 27
CONF_BATTERY_THRESHOLD = "battery_threshold"
CONF_HEALTH_IGNORE = "health_ignore"
CONF_IGNORE_CAMERAS = "ignore_cameras"
CONF_HIDE_SUBSTREAMS = "hide_substreams"
DEFAULT_BATTERY_THRESHOLD = 20
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
EVENT_HEALTH_CHANGED = f"{DOMAIN}_health_changed"
EVENT_MODE_CHANGED = f"{DOMAIN}_mode_changed"

# Memory
MEMORY_CATEGORIES = ["fact", "preference", "person", "routine", "note", "correction"]
MEMORY_STORAGE_VERSION = 1
MEMORY_MAX_ITEMS = 500

LLM_API_ID = DOMAIN

# heating that learns
CONF_HEATING_ENTITIES = "heating_entities"
CONF_PREHEAT = "preheat"
CONF_COMFORT_TEMP = "comfort_temp"
CONF_INSIDE_SENSORS = "inside_sensors"
CONF_WARM_BY_WEEKDAYS = "warm_by_weekdays"
CONF_WARM_BY_WEEKENDS = "warm_by_weekends"
CONF_HEATING_LIMIT = "heating_limit"
DEFAULT_HEATING_LIMIT = 0  # off unless set; only ever on the thermostats chosen in Sentient

# night watch
CONF_NIGHT_WATCH = "night_watch"
CONF_NIGHT_WATCH_ENTITIES = "night_watch_entities"
CONF_NIGHT_WATCH_ANNOUNCE = "night_watch_announce"
CONF_NIGHT_WATCH_SPEAKERS = "night_watch_speakers"
DEFAULT_NIGHT_WATCH = True

# weekly report
CONF_WEEKLY_REPORT = "weekly_report"
DEFAULT_WEEKLY_REPORT = True

# personality
CONF_PERSONALITY = "personality"
CONF_PERSONALITY_NOTES = "personality_notes"
CONF_STYLE_FEEDBACK = "style_feedback"  # "say it differently" on things it said
CONF_STYLE_NOTES = "style_notes"  # how people want things said, one per line
MAX_STYLE_NOTES = 8
DEFAULT_PERSONALITY = "calm"
PERSONALITIES = {
    "calm": "calm, warm and to the point",
    "dry": "quietly witty, with dry, understated British humour now and then — the answer always comes first, and never a joke about safety, security or anything upsetting",
    "warm": "warm and friendly, like a kind member of the family who's glad to help",
    "cheeky": "cheeky and playful, with light banter and the odd tease — but plain and serious about safety, security or anything upsetting",
    "composed": "polite and composed, like a capable butler, never stiff and never using titles",
}


NATURAL_TALK = (
    "How to talk (this replaces any other rule about reply length, except for device commands): "
    "for a command, a few words are enough (\"Done, the landing light's off.\"). For questions and chat, talk like a "
    "person in the room: two to four natural sentences — react to what was said, answer, and add one relevant thought "
    "when there is one. Ask a short follow-up question when a person naturally would, but not after every reply and "
    "never more than one question at a time. Use contractions and varied wording; no stock phrases (\"Is there "
    "anything else…\", \"I hope that helps\", \"Certainly!\"), no lists or headings, and don't repeat the question "
    "back. Bring in what you remember about the household when it fits. Light humour and opinions to suit your "
    "personality are welcome, but be plain and calm about safety, security, alarms or anything upsetting. If you "
    "don't know something, just say so."
)


def talk_line(options: dict) -> str | None:
    """The 'natural' speaking rules, or None for the brief style (the agent's own rules apply)."""
    return NATURAL_TALK if options.get(CONF_TALK_STYLE, DEFAULT_TALK_STYLE) == "natural" else None


def personality_line(options: dict) -> str:
    """One sentence about how the house should sound, for every prompt."""
    style = PERSONALITIES.get(options.get(CONF_PERSONALITY) or DEFAULT_PERSONALITY, PERSONALITIES[DEFAULT_PERSONALITY])
    notes = (options.get(CONF_PERSONALITY_NOTES) or "").strip()
    line = f"Your personality: {style}." + (f" {notes}" if notes else "")
    said = [n.strip() for n in str(options.get(CONF_STYLE_NOTES) or "").splitlines() if n.strip()]
    if said and options.get(CONF_STYLE_FEEDBACK, True):
        line += " How the household has asked for things to be said: " + "; ".join(said[-MAX_STYLE_NOTES:]) + "."
    return line

# camera security
CONF_CAMSEC = "camera_security"
CONF_CAMSEC_SENSORS = "camera_security_sensors"
CONF_CAMSEC_INSIDE = "camera_security_inside"
CONF_CAMSEC_ENTRIES = "camera_security_entries"
CONF_CAMSEC_DESCRIBE = "camera_security_describe"

# Disco mode
CONF_PARTY_LIGHTS = "party_lights"
CONF_PARTY_STYLE = "party_style"
CONF_PARTY_MINUTES = "party_minutes"
DEFAULT_PARTY_STYLE = "disco"
DEFAULT_PARTY_MINUTES = 60
PARTY_STYLES = ["disco", "party", "rainbow", "chill", "halloween", "christmas"]
CONF_PARTY_WHOLE_HOUSE = "party_whole_house"
CONF_PARTY_EXCLUDE = "party_exclude"
CONF_PARTY_SOUND = "party_sound"
CONF_PARTY_SOUND_SWITCH = "party_sound_switch"
CONF_PARTY_SHOW = "party_show"
DEFAULT_PARTY_SHOW = ["disco"]

# Room-aware volume, messages that follow people, the drive
CONF_ROOM_VOLUME = "room_volume"
CONF_FOLLOW_YOU = "follow_you"
CONF_FOLLOW_MINUTES = "follow_minutes"
DEFAULT_FOLLOW_MINUTES = 60
CONF_DRIVE_CAMERA = "drive_camera"
CONF_DRIVE_SPOT = "drive_spot"
CONF_DRIVE_TRIGGERS = "drive_triggers"
DEFAULT_DRIVE_SPOT = "drive"

# The mind: conversations, follow-ups, curiosity, overnight reflection
CONF_CONVERSATIONS = "conversation_memory"
CONF_FOLLOW_UPS = "follow_ups"
CONF_CURIOSITY = "curiosity"
CONF_REFLECTION = "reflection"
CONF_REFLECTION_TIME = "reflection_time"
DEFAULT_REFLECTION_TIME = "04:30:00"
EVENT_WATCH_EXPIRED = f"{DOMAIN}_watch_expired"
CONF_FULL_VOLUME_SPEAKERS = "full_volume_speakers"
CONF_TTS_ENTITY = "tts_entity"
CONF_SPEAK_HOUSE_LANGUAGE = "speak_house_language"  # Sentient's own English sentences said in the house's language
CONF_MEDIA_SPEAKERS = "media_speakers"
CONF_MEDIA_IMPORTANT_ONLY = "media_important_only"  # Echos: alarms, reminders and safety alerts only
CONF_FORESIGHT = "foresight"
CONF_FORESIGHT_TIME = "foresight_time"
DEFAULT_FORESIGHT_TIME = "19:30:00"
CONF_SLEEP = "sleep_detection"
CONF_SLEEP_MINUTES = "sleep_minutes"
DEFAULT_SLEEP_MINUTES = 20
CONF_BEDTIME_ROUTINES = "bedtime_routines"
CONF_SLEEP_ACTIONS = "sleep_actions"
CONF_WAKE_ACTIONS = "wake_actions"

# Optional voice diagnostics (enabled by default to preserve existing installations).
CONF_LIVE_VOICE_CHECK = "live_voice_check"
CONF_SPEECH_DELIVERY_HISTORY = "speech_delivery_history"

CONF_ALEXA_TTS_ONLY = "alexa_tts_only"

CONF_UPDATE_CHECKER = "update_checker"

# Battery and sleepy cameras: wake them before the AI looks, so it doesn't judge a standby picture
CONF_CAMERA_WAKE = "camera_wake"  # actions to run first ({{ camera }} is the camera being looked at)
CONF_CAMERA_WAKE_CAMERAS = "camera_wake_cameras"  # only these cameras (empty: every camera)
CONF_CAMERA_WAKE_SECONDS = "camera_wake_seconds"  # then wait this long for a fresh picture
DEFAULT_CAMERA_WAKE_SECONDS = 5

CONF_RECOVERY = "conversation_recovery"

CONF_AUTOMATIC_LANGUAGE = "automatic_language"
CONF_VOICE_LANGUAGE = "voice_language"

CONF_REMEMBER_LANGUAGE_PHRASES = "remember_language_phrases"
CONF_VOICE_TIMING_DIAGNOSTICS = "voice_timing_diagnostics"

CONF_SPECIFIC_CLARIFICATION = "specific_clarification"

CONF_ACTION_RECOVERY = "action_recovery"
