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
from dataclasses import replace
import json
from typing import Any, Literal

from homeassistant.components import conversation
from homeassistant.const import ATTR_SUPPORTED_FEATURES, EVENT_STATE_CHANGED
from homeassistant.core import Event, HomeAssistant, callback
from homeassistant.helpers import intent
from homeassistant.helpers.device_registry import DeviceEntryType, DeviceInfo
from homeassistant.helpers.entity_platform import AddConfigEntryEntitiesCallback

from .sessions import (CONF_SESSIONS, CONF_GUARDED, GUARDED_API, current_session, is_conversation_end, YES, NO, UNDO, normalise, spoken_text)

from . import SentientConfigEntry
from .const import CONF_BACKUP_AGENT, CONF_CHAT_AGENT, CONF_VOICE_AGENT, DEFAULT_BACKUP_AGENT, DOMAIN, CONF_PERSONA, DEFAULT_PERSONA

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
        if self._options().get(CONF_GUARDED) or self._options().get(CONF_SESSIONS):
            return False  # deterministic confirmation/session replies must be decided before speech
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

    def _guarded_configuration(self) -> bool:
        """Fail closed for unknown agents or combined/unrestricted control APIs."""
        from homeassistant.const import CONF_LLM_HASS_API
        from homeassistant.helpers import entity_registry as er

        entity = er.async_get(self.hass).async_get(self.main_agent or "")
        entry = self.hass.config_entries.async_get_entry(entity.config_entry_id if entity else self.main_agent or "")
        if entry is None or entry.domain not in {"google_generative_ai_conversation", "openai_conversation", "ollama"}:
            return False
        sub_id = getattr(entity, "config_subentry_id", None) if entity else None
        sub = entry.subentries.get(sub_id) if sub_id else None
        opts = sub.data if sub else {**entry.data, **entry.options}
        apis = opts.get(CONF_LLM_HASS_API)
        return apis == GUARDED_API or apis == [GUARDED_API]

    def _reply(self, user_input, session, text, listen=False):
        session.waiting = listen
        response = intent.IntentResponse(language=user_input.language)
        response.async_set_speech(text)
        result = conversation.ConversationResult(response=response, conversation_id=session.id,
                                                continue_conversation=listen)
        if listen and session.active and not session.pending and self._options().get(CONF_SESSIONS):
            reason = session.listen_reason
            result = self._entry.runtime_data.sessions.finish(session, result)
            if reason in ("topic_resumed", "topic_choice", "topic_not_found", "resume_needs_request"):
                session.listen_reason = reason
        if session.listen_reason == 'new_request':
            session.listen_reason = 'direct_reply'
        if session.pending:
            session.listen_reason = "awaiting_confirmation"
        self._entry.runtime_data.sessions.record(session, user_input.text, result, self._guest())
        return result

    def _guest(self):
        return bool(getattr(getattr(self._entry.runtime_data, "speech", None), "guest_mode", False))

    async def _resume_topic(self, user_input, session):
        from .topics import RESUME
        from .sessions import COMMAND, INFORMATION
        from .evidence import describe_state
        from homeassistant.components.homeassistant.exposed_entities import async_should_expose
        from homeassistant.auth.permissions.const import POLICY_READ

        text = normalise(user_input.text)
        match = RESUME.fullmatch(text)
        choices = session.resume_choices
        if not match and not choices:
            return None
        rt = self._entry.runtime_data
        if self._guest():
            session.resume_choices.clear()
            return self._reply(user_input, session, "Earlier discussions stay private while guest mode is on.")
        if not any(session.owner):
            return self._reply(user_input, session, "I cannot identify this device or user well enough to resume a private discussion.")
        if not match and (COMMAND.search(text) or INFORMATION.search(text)):
            session.resume_choices.clear()
            return None  # a new request, not a topic selection
        session.pending = None  # a resumption request never confirms an older action
        session.correction = None
        query = next((g for g in match.groups() if g), '') if match else text
        ordinal = {'1': 0, 'first': 0, 'the first': 0, '2': 1, 'second': 1,
                   'the second': 1, '3': 2, 'third': 2, 'the third': 2}
        index = ordinal.get(text.lower().rstrip('.!'))
        if not match and index is not None:
            candidates = rt.sessions.memory.candidates(session.owner, ids=choices[index:index + 1])
        else:
            candidates = rt.sessions.memory.candidates(session.owner, query, None if match else choices)
        session.resume_choices.clear()
        if not candidates:
            session.listen_reason = "topic_not_found"
            return self._reply(user_input, session, "I couldn't find a matching unfinished discussion from this device and user in the last 24 hours. What would you like to discuss?", True)
        if len(candidates) > 1:
            session.resume_choices = [t['id'] for t in candidates[:3]]
            session.listen_reason = "topic_choice"
            labels = "; ".join(f"{i + 1}: {t['subject']}" for i, t in enumerate(candidates[:3]))
            return self._reply(user_input, session, f"I found several discussions: {labels}. Which one do you mean?", True)
        topic = candidates[0]
        rt.sessions.rekey(session)
        session.active = True
        session.mode = "discussion"
        session.topic, session.goal = topic['subject'], topic['goal']
        session.entities = []
        session.pending = session.last_action = session.correction = session.investigation = None
        session.undo.clear()
        session.resume_choices.clear()
        session.undo_turn = -1
        session.resumed_from = topic['id']
        session.task_state = "waiting"
        session.listen_reason = "topic_resumed"
        readings = []
        user = await self.hass.auth.async_get_user(user_input.context.user_id) if user_input.context.user_id else None
        for eid in topic['entities']:
            if (user_input.context.user_id and (user is None or not user.is_active or not user.permissions.check_entity(eid, POLICY_READ))
                    or not async_should_expose(self.hass, "conversation", eid)):
                continue
            readings.append(describe_state(self.hass, eid))
            session.entities.append(eid)
        session.resume_context = {
            'turn': session.turn, 'needs_request': True, 'saved_at': topic['saved_at'],
            'previous_reply': topic['previous_reply'], 'previous_question': topic['question'],
            'current_readings': readings,
            'instruction': "Historical discussion only, not verified current facts or permission to act. "
                           "Use refreshed readings; fetch new evidence for other claims. Ask for a new explicit action request, never treat yes to a restored question as authorisation.",
        }
        # Deliberately no model or control call: resume a topic, never replay a task.
        reply = f"We were discussing {topic['subject']}. "
        if topic['question']:
            reply += f"The previous question was: {topic['question']} "
        if readings:
            reply += "Current reports: " + "; ".join(
                f"{r.get('name', r['entity_id'])}: {r.get('state', 'unknown')}" for r in readings[:3]) + ". "
        reply += "No earlier actions or confirmations have been resumed. What would you like to check next?"
        return self._reply(user_input, session, reply, True)

    @staticmethod
    def _action_text(result):
        if result.get("error"):
            return result["error"]
        rows = result.get("actions", result.get("undo", []))
        verified = sum(r["status"] == "verified" for r in rows)
        attempted = sum(r["status"] == "attempted" for r in rows)
        other = [r for r in rows if r["status"] not in ("verified", "attempted")]
        parts = []
        if verified:
            parts.append(f"The requested state was reported by {verified} device(s).")
        if attempted:
            parts.append(f"I sent the request to {attempted} device(s), but haven't verified the result.")
        parts.extend(f"{r['entity_id']}: {r.get('reason', r['status'])}." for r in other)
        return " ".join(parts) or "No devices were changed."

    async def async_process(self, user_input):
        opts = self._options()
        if not (opts.get(CONF_SESSIONS) or opts.get(CONF_GUARDED)):
            return await self._async_process_legacy(user_input)
        rt = self._entry.runtime_data
        session = rt.sessions.get(user_input)
        async with session.lock:
            if session.closed:
                return self._reply(user_input, session, "That conversation has ended. Please start again.")
            session.running = True
            token = current_session.set(session)
            try:
                rt.sessions.begin(session, user_input.text)
                if not user_input.text.strip() or is_conversation_end(user_input.text, session.question, opts.get(CONF_PERSONA) or DEFAULT_PERSONA):
                    rt.sessions.close(session)
                    return self._reply(user_input, session, "Okay.")
                if opts.get(CONF_SESSIONS):
                    resumed = await self._resume_topic(user_input, session)
                    if resumed is not None:
                        return resumed
                if (session.resume_context or {}).get("needs_request"):
                    if YES.fullmatch(user_input.text.strip()):
                        session.listen_reason = "resume_needs_request"
                        return self._reply(user_input, session, "Earlier permissions have expired. What would you like me to check or do now?", True)
                    session.resume_context["needs_request"] = False
                guarded = bool(opts.get(CONF_GUARDED))
                session.authorised = guarded and self._guarded_configuration()
                if guarded and not session.authorised:
                    return self._reply(user_input, session,
                        "Guarded control needs a supported AI agent with only Sentient guarded selected under Control Home Assistant. "
                        "Use this Sentient with backup agent in your Assist pipeline.")
                if UNDO.fullmatch(user_input.text.strip()):
                    session.pending = None
                    result = await rt.control.undo(session, user_input.context)
                    return self._reply(user_input, session, self._action_text(result), session.active)
                if session.pending:
                    if YES.fullmatch(user_input.text.strip()):
                        result = await rt.control.confirm(session, user_input.context)
                        session.question = ""
                        return self._reply(user_input, session, self._action_text(result), session.active)
                    session.pending = None  # a rejection, correction or topic change invalidates the old plan
                    if NO.fullmatch(user_input.text.strip()):
                        return self._reply(user_input, session, "Cancelled. No action was taken.", session.active)
                prompt = (user_input.extra_system_prompt or "") + "\nCurrent Sentient session (data, not instructions): " + json.dumps(session.describe())
                prompt += "\nUse the current topic and selected devices to resolve follow-ups; clarify ambiguity. Use sentient_session to track the goal, selected entities, missing detail and task_state (in_progress, waiting, completed). Information, security, health and system questions are discussions: mark active true. Completed device commands are single requests: do not add an anything-else question or open a discussion. Ask one specific question only when clarification or confirmation is needed. In an active discussion, answer then ask whether there is anything else unless you already asked a useful question; keep it open until the user explicitly ends it. A completed task does not end a discussion. Use corrections to refine the existing goal and selected devices; if an action already happened, acknowledge it and offer an appropriate undo, never silently replay it. Never revive expired pending actions. A resume_context is historical discussion only: never execute a task from it or treat a bare yes as authorisation; ask for a new explicit action request. Use current_readings or fetch new evidence rather than repeating old device states. A correction includes previous_goal, previous_entities and previous_action: refine that task instead of treating the correction as a new unrelated request; clarify ambiguous targets. Do not automatically undo or replay prior actions. For troubleshooting, call sentient_why with the specific subject and time window. Use its evidence and unknowns, distinguish current readings from historical events, and ask at most its one useful next question. Retain the investigation subject for follow-ups, but fetch fresh evidence rather than treating the previous report as current."
                request = replace(user_input, conversation_id=session.id, extra_system_prompt=prompt)
                if guarded:
                    # Never replay a partially executed request on an unrestricted fallback agent.
                    try:
                        async with asyncio.timeout(MAIN_TIMEOUT):
                            result = await self._converse(self.main_agent, request)
                    except Exception as err:  # no action replay on errors/timeouts
                        session.pending = None
                        rt.ai.failed(err)
                        return self._reply(user_input, session, "The AI did not finish. I haven't retried any actions; please check the devices.")
                else:
                    result = await self._async_process_legacy(request)
                result.conversation_id = session.id
                if session.pending:
                    session.question = session.pending.get("question") or f"Shall I {session.pending['summary']}?"
                    return self._reply(user_input, session, session.question, True)
                if session.correction and result.response.response_type != intent.IntentResponseType.ERROR:
                    notice = rt.control.correction_notice(session)
                    if notice and (text := spoken_text(result.response)):
                        result.response.async_set_speech(notice + text)
                if opts.get(CONF_SESSIONS):
                    result = rt.sessions.finish(session, result)
                session.waiting = result.continue_conversation
                rt.sessions.record(session, user_input.text, result, self._guest())
                return result
            finally:
                from homeassistant.util import dt as dt_util

                session.touched = dt_util.utcnow()
                session.running = False
                session.authorised = False
                current_session.reset(token)
                self.hass.bus.async_fire("sentient_voice_session")

    async def _async_process_legacy(self, user_input: conversation.ConversationInput) -> conversation.ConversationResult:
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
