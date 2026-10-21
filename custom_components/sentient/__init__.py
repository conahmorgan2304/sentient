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
    SERVICE_FORGET,
    SERVICE_REMEMBER,
    SERVICE_SAY,
)
from .llm_api import SentientAPI
from .memory import MemoryStore
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
    entry.runtime_data = SentientRuntime(
        options=options, memory=memory, speech=SpeechRouter(hass, options)
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
