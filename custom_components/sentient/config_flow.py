"""Setup wizard for Sentient."""

from __future__ import annotations

from typing import Any

import voluptuous as vol

from homeassistant.config_entries import ConfigEntry, ConfigFlow, ConfigFlowResult, OptionsFlow
from homeassistant.core import callback
from homeassistant.helpers import selector

from .const import (
    CONF_AI_TASK_ENTITY,
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
            vol.Optional(CONF_AI_TASK_ENTITY, **d(CONF_AI_TASK_ENTITY)): selector.EntitySelector(
                selector.EntitySelectorConfig(domain="ai_task")
            ),
            vol.Required(CONF_CAMERA_COOLDOWN, **d(CONF_CAMERA_COOLDOWN, DEFAULT_CAMERA_COOLDOWN)): selector.NumberSelector(
                selector.NumberSelectorConfig(min=10, max=3600, step=10, unit_of_measurement="s", mode=selector.NumberSelectorMode.BOX)
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
        return self.async_show_menu(step_id="init", menu_options=["settings", "memories"])

    async def async_step_settings(self, user_input: dict[str, Any] | None = None) -> ConfigFlowResult:
        current = {**self.config_entry.data, **self.config_entry.options}
        if user_input is not None:
            return self.async_create_entry(data=user_input)
        schema = vol.Schema(
            {vol.Required(CONF_PERSONA, default=current.get(CONF_PERSONA, DEFAULT_PERSONA)): selector.TextSelector()}
        ).extend(_speech_schema(current).schema)
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
