"""Sentient — an evolving AI brain for Home Assistant."""

from __future__ import annotations

from dataclasses import dataclass
import logging
from typing import Any

import voluptuous as vol

from homeassistant.config_entries import ConfigEntry
from homeassistant.const import Platform
from homeassistant.core import HomeAssistant, ServiceCall, ServiceResponse, SupportsResponse
from homeassistant.helpers import config_validation as cv
from homeassistant.helpers import llm

from .const import (
    DOMAIN,
    MEMORY_CATEGORIES,
    PRIORITIES,
    SERVICE_ASK,
    SERVICE_BRIEFING,
    SERVICE_CANCEL_WATCH,
    SERVICE_FORGET,
    SERVICE_LEARN,
    SERVICE_REMIND,
    SERVICE_REMEMBER,
    SERVICE_SAY,
    SERVICE_TIDY,
    SERVICE_WATCH,
)
from .llm_api import SentientAPI
from .memory import MemoryStore
from .presence import ActivityTracker
from .learning import Learner
from .notice import Noticer
from .upkeep import Upkeep, gather_briefing
from .spots import Spots
from .visitors import Visitors
from .watches import WatchManager
from .speech import SpeechRouter

_LOGGER = logging.getLogger(__name__)

PLATFORMS: list[Platform] = [Platform.SENSOR]

CONFIG_SCHEMA = cv.config_entry_only_config_schema(DOMAIN)


@dataclass
class SentientRuntime:
    """Everything a loaded Sentient entry owns."""

    options: dict[str, Any]
    memory: MemoryStore
    speech: SpeechRouter
    tracker: ActivityTracker
    watches: WatchManager
    noticer: Noticer
    learner: Learner
    upkeep: Upkeep
    visitors: Visitors
    spots: Spots


type SentientConfigEntry = ConfigEntry[SentientRuntime]


SAY_SCHEMA = vol.Schema(
    {
        vol.Required("message"): cv.string,
        vol.Optional("priority", default="normal"): vol.In(PRIORITIES),
        vol.Optional("speaker"): vol.Any(cv.entity_ids, cv.string),
    }
)
REMEMBER_SCHEMA = vol.Schema(
    {
        vol.Required("text"): cv.string,
        vol.Optional("category", default="fact"): vol.In(MEMORY_CATEGORIES),
    }
)
FORGET_SCHEMA = vol.Schema({vol.Required("what"): cv.string})
WATCH_SCHEMA = vol.Schema(
    {
        vol.Required("what"): cv.string,
        vol.Required("entity"): cv.string,
        vol.Optional("to_state"): cv.string,
        vol.Optional("above"): vol.Coerce(float),
        vol.Optional("below"): vol.Coerce(float),
        vol.Optional("question"): cv.string,
        vol.Optional("look_for"): cv.string,
        vol.Optional("message"): cv.string,
        vol.Optional("until"): cv.string,
        vol.Optional("minutes"): vol.Coerce(float),
        vol.Optional("repeat", default=False): cv.boolean,
        vol.Optional("priority", default="normal"): vol.In(PRIORITIES),
        vol.Optional("speaker"): cv.string,
        vol.Optional("wake_on"): vol.Any(cv.entity_ids, cv.string),
        vol.Optional("spot"): cv.string,
    }
)
ASK_SCHEMA = vol.Schema(
    {
        vol.Required("question"): cv.string,
        vol.Optional("priority", default="normal"): vol.In(PRIORITIES),
        vol.Optional("speaker"): cv.string,
        vol.Optional("extra_prompt"): cv.string,
        vol.Optional("preannounce", default=False): cv.boolean,
    }
)
REMIND_SCHEMA = vol.Schema(
    {
        vol.Required("message"): cv.string,
        vol.Optional("at"): cv.string,
        vol.Optional("minutes"): vol.Coerce(float),
        vol.Optional("speaker"): cv.string,
        vol.Optional("priority", default="urgent"): vol.In(PRIORITIES),
    }
)
CANCEL_WATCH_SCHEMA = vol.Schema({vol.Required("what"): cv.string})


def _entry_options(entry: ConfigEntry) -> dict[str, Any]:
    return {**entry.data, **entry.options}


async def async_setup(hass: HomeAssistant, config: dict) -> bool:
    """Register the LLM API and services once."""
    hass.data.setdefault(DOMAIN, {})
    hass.data[DOMAIN]["unregister_api"] = llm.async_register_api(hass, SentientAPI(hass))
    _register_services(hass)
    return True


async def async_setup_entry(hass: HomeAssistant, entry: SentientConfigEntry) -> bool:
    options = _entry_options(entry)
    memory = MemoryStore(hass, entry.entry_id)
    await memory.async_load()
    tracker = ActivityTracker(hass, options)
    await tracker.async_start()
    entry.async_on_unload(tracker.async_stop)
    speech = SpeechRouter(hass, options, tracker)
    watches = WatchManager(hass, entry.entry_id, options, speech)
    spots = Spots(hass, entry.entry_id, options)
    await spots.async_load()
    watches.spots = spots
    await watches.async_start()
    entry.async_on_unload(watches.async_stop)
    noticer = Noticer(hass, options, speech, memory, watches)
    noticer.async_start()
    entry.async_on_unload(noticer.async_stop)
    learner = Learner(hass, entry.entry_id, options, speech, tracker)
    await learner.async_start()
    entry.async_on_unload(learner.async_stop)
    upkeep = Upkeep(hass, lambda: entry.runtime_data)
    upkeep.async_start()
    entry.async_on_unload(upkeep.async_stop)
    visitors = Visitors(hass, entry.entry_id, options, speech, watches)
    await visitors.async_start()
    entry.async_on_unload(visitors.async_stop)
    entry.runtime_data = SentientRuntime(
        options=options, memory=memory, speech=speech, tracker=tracker, watches=watches,
        noticer=noticer, learner=learner, upkeep=upkeep, visitors=visitors, spots=spots,
    )
    entry.async_on_unload(entry.add_update_listener(_async_options_updated))
    await hass.config_entries.async_forward_entry_setups(entry, PLATFORMS)
    return True


async def async_unload_entry(hass: HomeAssistant, entry: SentientConfigEntry) -> bool:
    return await hass.config_entries.async_unload_platforms(entry, PLATFORMS)


async def _async_options_updated(hass: HomeAssistant, entry: SentientConfigEntry) -> None:
    options = _entry_options(entry)
    entry.runtime_data.options = options
    entry.runtime_data.speech.options = options
    entry.runtime_data.tracker.options = options
    entry.runtime_data.watches.options = options
    entry.runtime_data.noticer.options = options
    entry.runtime_data.learner.options = options
    entry.runtime_data.visitors.options = options
    entry.runtime_data.spots.options = options


def _runtime(hass: HomeAssistant) -> SentientRuntime:
    entries = hass.config_entries.async_loaded_entries(DOMAIN)
    if not entries:
        raise vol.Invalid("Sentient is not set up")
    return entries[0].runtime_data


def _register_services(hass: HomeAssistant) -> None:
    async def say(call: ServiceCall) -> ServiceResponse:
        rt = _runtime(hass)
        speaker = call.data.get("speaker")
        targets: list[str] | None = None
        if isinstance(speaker, list):
            targets = speaker
        elif isinstance(speaker, str) and speaker:
            found = rt.speech.find_speaker(speaker)
            targets = [found] if found else None
        result = await rt.speech.async_say(
            call.data["message"], call.data["priority"], targets, source="service"
        )
        return result.as_dict()

    async def remember(call: ServiceCall) -> ServiceResponse:
        memory = _runtime(hass).memory.add(call.data["text"], call.data["category"], source="service")
        return {"saved": memory.text, "id": memory.id}

    async def forget(call: ServiceCall) -> ServiceResponse:
        removed = _runtime(hass).memory.forget(call.data["what"])
        return {"forgotten": removed.text if removed else None}

    async def watch(call: ServiceCall) -> ServiceResponse:
        data = dict(call.data)
        created = _runtime(hass).watches.add(data.pop("what"), data.pop("entity"), **data)
        return created.summary()

    async def briefing(call: ServiceCall) -> ServiceResponse:
        rt = _runtime(hass)
        if call.data.get("speak", True):
            return await rt.upkeep.async_speak_briefing(call.data.get("priority", "normal"))
        return await gather_briefing(hass, rt)

    async def tidy(call: ServiceCall) -> ServiceResponse:
        return await _runtime(hass).upkeep.async_tidy()

    async def learn(call: ServiceCall) -> ServiceResponse:
        routines = await _runtime(hass).learner.async_learn()
        return {"routines": [r.summary() for r in routines]}

    async def ask(call: ServiceCall) -> ServiceResponse:
        rt = _runtime(hass)
        targets = None
        if speaker := call.data.get("speaker"):
            found = rt.speech.find_speaker(speaker)
            targets = [found] if found else None
        result = await rt.speech.async_ask(
            call.data["question"], call.data["priority"], targets, source="service",
            extra_prompt=call.data.get("extra_prompt"), preannounce=call.data["preannounce"],
        )
        return result.as_dict()

    async def remind(call: ServiceCall) -> ServiceResponse:
        created = _runtime(hass).watches.add_reminder(
            call.data["message"], until=call.data.get("at"), minutes=call.data.get("minutes"),
            speaker=call.data.get("speaker"), priority=call.data["priority"],
        )
        return created.summary()

    async def cancel_watch(call: ServiceCall) -> ServiceResponse:
        removed = _runtime(hass).watches.cancel(call.data["what"])
        return {"cancelled": removed.what if removed else None}

    hass.services.async_register(
        DOMAIN, SERVICE_WATCH, watch, schema=WATCH_SCHEMA, supports_response=SupportsResponse.OPTIONAL
    )
    hass.services.async_register(
        DOMAIN, SERVICE_BRIEFING, briefing,
        schema=vol.Schema({
            vol.Optional("speak", default=True): cv.boolean,
            vol.Optional("priority", default="normal"): vol.In(PRIORITIES),
        }),
        supports_response=SupportsResponse.OPTIONAL,
    )
    hass.services.async_register(DOMAIN, SERVICE_TIDY, tidy, supports_response=SupportsResponse.OPTIONAL)
    hass.services.async_register(
        DOMAIN, SERVICE_LEARN, learn, supports_response=SupportsResponse.OPTIONAL
    )
    hass.services.async_register(
        DOMAIN, SERVICE_ASK, ask, schema=ASK_SCHEMA, supports_response=SupportsResponse.OPTIONAL
    )
    hass.services.async_register(
        DOMAIN, SERVICE_REMIND, remind, schema=REMIND_SCHEMA, supports_response=SupportsResponse.OPTIONAL
    )
    hass.services.async_register(
        DOMAIN, SERVICE_CANCEL_WATCH, cancel_watch, schema=CANCEL_WATCH_SCHEMA,
        supports_response=SupportsResponse.OPTIONAL,
    )
    hass.services.async_register(
        DOMAIN, SERVICE_SAY, say, schema=SAY_SCHEMA, supports_response=SupportsResponse.OPTIONAL
    )
    hass.services.async_register(
        DOMAIN, SERVICE_REMEMBER, remember, schema=REMEMBER_SCHEMA,
        supports_response=SupportsResponse.OPTIONAL,
    )
    hass.services.async_register(
        DOMAIN, SERVICE_FORGET, forget, schema=FORGET_SCHEMA,
        supports_response=SupportsResponse.OPTIONAL,
    )
