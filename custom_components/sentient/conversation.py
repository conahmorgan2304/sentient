"""A voice agent with a backup.

Point your Assist pipeline at this agent ("<persona> with backup") instead of your AI agent. Every
request goes to your AI agent as before — same prompt, same tools, same streaming. If the AI errors,
times out or is down, the same request is handed to a backup agent (Home Assistant's own, by default),
so "turn the lights off" and "what's the temperature?" still work. If the backup can't do it either,
it says plainly that the AI isn't answering. Failures count towards the self-check.
"""

from __future__ import annotations

import asyncio
import logging
from typing import Any, Literal

from homeassistant.components import conversation
from homeassistant.const import ATTR_SUPPORTED_FEATURES, EVENT_STATE_CHANGED
from homeassistant.core import Event, HomeAssistant, callback
from homeassistant.helpers import intent
from homeassistant.helpers.device_registry import DeviceEntryType, DeviceInfo
from homeassistant.helpers.entity_platform import AddConfigEntryEntitiesCallback

from . import SentientConfigEntry
from .const import CONF_BACKUP_AGENT, CONF_CHAT_AGENT, CONF_VOICE_AGENT, DEFAULT_BACKUP_AGENT, DOMAIN

_LOGGER = logging.getLogger(__name__)

MAIN_TIMEOUT = 45  # seconds; a voice request that takes longer than this has failed
FAILED_CODES = {intent.IntentResponseErrorCode.UNKNOWN}
NOT_ANSWERING = "The AI isn't answering right now, so I can only do simple things like lights and heating."


async def async_setup_entry(
    hass: HomeAssistant, entry: SentientConfigEntry, async_add_entities: AddConfigEntryEntitiesCallback
) -> None:
    async_add_entities([BackedUpAgent(entry)])


class BackedUpAgent(conversation.ConversationEntity):
    _attr_has_entity_name = True
    _attr_translation_key = "voice"
    _attr_icon = "mdi:account-voice"

    def __init__(self, entry: SentientConfigEntry) -> None:
        self._entry = entry
        self._attr_unique_id = f"{entry.entry_id}_voice"
        self._attr_device_info = DeviceInfo(
            identifiers={(DOMAIN, entry.entry_id)}, name=entry.title, manufacturer="Sentient",
            model="Home brain", entry_type=DeviceEntryType.SERVICE,
        )
        self.last_backup: str | None = None

    async def async_added_to_hass(self) -> None:
        await super().async_added_to_hass()

        @callback
        def _main_changed(event: Event) -> None:
            if event.data.get("entity_id") == self.main_agent:
                self.async_write_ha_state()  # pick up the main agent's features

        self.async_on_remove(self.hass.bus.async_listen(EVENT_STATE_CHANGED, _main_changed))

    @property
    def supported_languages(self) -> list[str] | Literal["*"]:
        return "*"

    # ----- which agents ------------------------------------------------------------------
    def _options(self) -> dict[str, Any]:
        rt = getattr(self._entry, "runtime_data", None)
        return rt.options if rt is not None else {**self._entry.data, **self._entry.options}

    @property
    def main_agent(self) -> str | None:
        opts = self._options()
        for agent in (opts.get(CONF_VOICE_AGENT), opts.get(CONF_CHAT_AGENT)):
            if agent and agent != self.entity_id:
                return agent
        return None

    @property
    def backup_agent(self) -> str:
        agent = self._options().get(CONF_BACKUP_AGENT) or DEFAULT_BACKUP_AGENT
        return DEFAULT_BACKUP_AGENT if agent in (self.entity_id, self.main_agent) else agent

    def _main_state(self):
        return self.hass.states.get(self.main_agent) if self.main_agent else None

    @property
    def supported_features(self) -> int:
        """The main agent's (so the pipeline treats this agent just like it)."""
        st = self._main_state()
        return int(st.attributes.get(ATTR_SUPPORTED_FEATURES, 0)) if st else 0

    @property
    def supports_streaming(self) -> bool:
        agent = conversation.async_get_agent(self.hass, self.main_agent) if self.main_agent else None
        return bool(getattr(agent, "supports_streaming", False))

    @property
    def extra_state_attributes(self) -> dict[str, Any]:
        return {"main_agent": self.main_agent, "backup_agent": self.backup_agent, "last_backup": self.last_backup}

    # ----- answering ---------------------------------------------------------------------
    async def _converse(self, agent_id: str, user_input: conversation.ConversationInput) -> conversation.ConversationResult:
        return await conversation.async_converse(
            self.hass, user_input.text, user_input.conversation_id, user_input.context,
            language=user_input.language, agent_id=agent_id, device_id=user_input.device_id,
            satellite_id=user_input.satellite_id, extra_system_prompt=user_input.extra_system_prompt,
        )

    async def async_process(self, user_input: conversation.ConversationInput) -> conversation.ConversationResult:
        gate = getattr(getattr(self._entry, "runtime_data", None), "ai", None)
        why = "no main agent chosen in Sentient's settings"
        if self.main_agent:
            try:
                async with asyncio.timeout(MAIN_TIMEOUT):
                    result = await self._converse(self.main_agent, user_input)
                response = result.response
                if response.response_type != intent.IntentResponseType.ERROR or response.error_code not in FAILED_CODES:
                    if gate is not None:
                        gate.ok()
                    return result
                why = response.speech.get("plain", {}).get("speech") or "an error"
            except (TimeoutError, Exception) as err:  # noqa: BLE001 - anything at all: use the backup
                why = str(err) or type(err).__name__
            if gate is not None:
                gate.failed(RuntimeError(f"voice agent: {why}"))
        _LOGGER.info("Sentient: the main voice agent failed (%s); using %s", why, self.backup_agent)
        from homeassistant.util import dt as dt_util

        self.last_backup = dt_util.utcnow().isoformat()
        self.async_write_ha_state()
        rt = getattr(self._entry, "runtime_data", None)
        if rt is not None:
            rt.timeline.add("health", "Voice answered by the backup agent", f"the AI failed: {why[:120]}")
        try:
            result = await self._converse(self.backup_agent, user_input)
            if result.response.response_type != intent.IntentResponseType.ERROR:
                return result
        except Exception as err:  # noqa: BLE001
            _LOGGER.debug("Backup agent failed too: %s", err)
            result = None
        response = intent.IntentResponse(language=user_input.language)
        response.async_set_speech(NOT_ANSWERING)
        try:  # so a streaming pipeline speaks it too
            from homeassistant.components.conversation.chat_log import current_chat_log

            if (log := current_chat_log.get()) is not None:
                log.async_add_assistant_content_without_tools(
                    conversation.AssistantContent(agent_id=self.entity_id, content=NOT_ANSWERING)
                )
        except Exception as err:  # noqa: BLE001
            _LOGGER.debug("Couldn't add to the chat log: %s", err)
        return conversation.ConversationResult(
            response=response, conversation_id=result.conversation_id if result else user_input.conversation_id
        )
