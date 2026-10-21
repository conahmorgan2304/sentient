"""Setup wizard for Sentient."""

from __future__ import annotations

from typing import Any

import voluptuous as vol

from homeassistant.config_entries import ConfigEntry, ConfigFlow, ConfigFlowResult, OptionsFlow
from homeassistant.core import callback
from homeassistant.helpers import selector

from .const import (
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
)

_SPEAKER = selector.EntitySelector(selector.EntitySelectorConfig(domain="assist_satellite", multiple=True))
_ONE_SPEAKER = selector.EntitySelector(selector.EntitySelectorConfig(domain="assist_satellite"))


def _speech_schema(defaults: dict[str, Any]) -> vol.Schema:
    def d(key: str, fallback: Any = None) -> dict[str, Any]:
        value = defaults.get(key, fallback)
        return {} if value is None else {"default": value}

    return vol.Schema(
        {
            vol.Optional(CONF_SPEAKERS, **d(CONF_SPEAKERS, [])): _SPEAKER,
            vol.Optional(CONF_DEFAULT_SPEAKER, **d(CONF_DEFAULT_SPEAKER)): _ONE_SPEAKER,
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
            vol.Required(CONF_PROMPT_MEMORIES, **d(CONF_PROMPT_MEMORIES, DEFAULT_PROMPT_MEMORIES)): selector.NumberSelector(
                selector.NumberSelectorConfig(min=0, max=100, step=1, mode=selector.NumberSelectorMode.BOX)
            ),
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


class SentientOptionsFlow(OptionsFlow):
    """Change persona and speech rules any time."""

    async def async_step_init(self, user_input: dict[str, Any] | None = None) -> ConfigFlowResult:
        current = {**self.config_entry.data, **self.config_entry.options}
        if user_input is not None:
            return self.async_create_entry(data=user_input)
        schema = vol.Schema(
            {vol.Required(CONF_PERSONA, default=current.get(CONF_PERSONA, DEFAULT_PERSONA)): selector.TextSelector()}
        ).extend(_speech_schema(current).schema)
        return self.async_show_form(step_id="init", data_schema=schema)
