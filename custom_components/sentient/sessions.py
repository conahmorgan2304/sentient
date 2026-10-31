"""Short-lived voice sessions. Nothing here is persisted or shared between speakers/users."""
from __future__ import annotations

import asyncio
import html
from copy import deepcopy
from contextvars import ContextVar
from dataclasses import dataclass, field
from datetime import timedelta
import re
from typing import Any
import uuid

from homeassistant.util import dt as dt_util

CONF_SESSIONS = 'conversation_sessions'
CONF_SESSION_SECONDS = 'session_seconds'
CONF_GUARDED = 'guarded_control'
CONF_UNDO = 'voice_undo'
CONF_EVIDENCE = 'evidence_awareness'
CONF_INCIDENTS = 'security_incidents'
GUARDED_API = 'sentient_guarded'
START = re.compile(r"\b(let'?s talk|keep listening|conversation mode|help me (?:sort|plan|work|figure))\b", re.I)
END = re.compile(
    r"^(?:(?:okay|ok|no|well)[, ]+)?(?:that'?s all(?: for now)?|that is all(?: for now)?|"
    r"that(?:'?s| is) enough|enough(?: now)?|stop(?: listening)?|end (?:the )?conversation|"
    r"goodbye|never ?mind|we(?:'?re| are) done|i(?:'?m| am) done|nothing else|no(?: thanks| thank you))"
    r"(?:[, ]+(?:please|thanks|thank you))?[.! ]*$", re.I)
# Cheap routing for clear requests; the existing model can classify less obvious requests
# through sentient_session. These expressions never resolve targets or authorise actions.
COMMAND = re.compile(
    r"^(?:(?:please|can you|could you|would you|will you)[, ]+)*"
    r"(?:turn|switch|set|dim|brighten|open|close|lock|unlock|start|stop|pause|resume|"
    r"play|mute|unmute|increase|decrease|raise|lower|activate|deactivate|"
    r"remind|announce|cancel|undo)\b", re.I)
INFORMATION = re.compile(
    r"^(?:(?:please|can you|could you|would you)[, ]+)*"
    r"(?:what|why|how|when|where|who|which|is|are|was|were|has|have|does|do|"
    r"tell me|explain|describe|check|give me (?:a|an|the) (?:report|summary|update)|"
    r"security|system|health|status)\b", re.I)
CORRECTION = re.compile(r"^(?:no[, ]+|i meant\b|not that\b|actually[, ]+|rather[, ]+|instead[, ]+)", re.I)
FOLLOW_UP = "Is there anything else you'd like to know?"


def normalise(text):
    return text.strip().replace("’", "'")


def spoken_text(response):
    """Read the final spoken response, including SSML, rather than tool metadata."""
    speech = response.speech
    if "plain" in speech:
        return speech["plain"].get("speech", "").strip()
    return html.unescape(re.sub(r"<[^>]+>", "", speech.get("ssml", {}).get("speech", ""))).strip()


def ends_in_question(text):
    return text.rstrip().rstrip('"”’').rstrip().endswith("?")

YES = re.compile(r'^(?:yes|yes please|yeah|confirm|go ahead|do it)[.! ]*$', re.I)
NO = re.compile(r'^(?:no|no thanks|cancel|cancel that|don\'t|do not)[.! ]*$', re.I)
UNDO = re.compile(r"^(?:undo|undo that|undo what you just did|put it back|actually[,]? put it back)[.! ]*$", re.I)


@dataclass
class VoiceSession:
    id: str
    owner: tuple[str | None, str | None, str | None]
    touched: Any
    active: bool = False
    waiting: bool = False
    topic: str = ''
    entities: list[str] = field(default_factory=list)
    question: str = ''
    turn: int = 0
    pending: dict | None = None
    undo: list[dict] = field(default_factory=list)
    undo_turn: int = -1
    last_action: dict | None = None
    lock: asyncio.Lock = field(default_factory=asyncio.Lock)
    running: bool = False
    authorised: bool = False
    closed: bool = False
    listen_override: bool | None = None
    mode: str = 'command'
    goal: str = ''
    task_state: str = 'in_progress'
    missing_detail: str = ''
    listen_reason: str = 'new_request'
    command_turn: bool = False
    correction: dict | None = None
    investigation: dict | None = None
    resume_choices: list[str] = field(default_factory=list)
    resumed_from: str | None = None
    resume_context: dict | None = None

    def describe(self) -> dict:
        return {'conversation_id': self.id, 'state': 'awaiting_confirmation' if self.pending else
                'listening' if self.waiting else 'discussion_idle' if self.active else 'wake_word', 'topic': self.topic,
                'entities': list(self.entities), 'unanswered_question': self.question,
                'mode': self.mode, 'goal': self.goal, 'task_state': self.task_state,
                'missing_detail': self.missing_detail, 'listen_reason': self.listen_reason,
                'correction': self.correction, 'investigation': self.investigation,
                'resumed_from': self.resumed_from, 'resume_context': self.resume_context,
                'pending_action': self.pending.get('summary') if self.pending else None,
                'confirmation_expires': self.pending['expires'].isoformat() if self.pending and self.pending.get('expires') else None,
                'last_action': self.last_action, 'undo_available': bool(self.undo),
                'last_activity': self.touched.isoformat()}


current_session: ContextVar[VoiceSession | None] = ContextVar('sentient_voice_session', default=None)


class Sessions:
    def __init__(self, options):
        from .topics import TopicMemory
        self.memory = TopicMemory()
        self.options = options
        self.items: dict[tuple, VoiceSession] = {}

    @property
    def seconds(self):
        return max(15, min(300, int(self.options.get(CONF_SESSION_SECONDS, 60))))

    def prune(self):
        cutoff = dt_util.utcnow() - timedelta(seconds=self.seconds)
        for key, value in list(self.items.items()):
            if not value.running and (value.closed or value.touched < cutoff):
                value.closed = True
                value.pending = None
                value.undo.clear()
                self.items.pop(key, None)

    def get(self, user_input):
        self.prune()
        owner = (user_input.device_id, user_input.satellite_id, user_input.context.user_id)
        cid = user_input.conversation_id or uuid.uuid4().hex
        key = (cid, *owner)
        if key not in self.items:
            if len(self.items) >= 100:
                idle = next((k for k, s in self.items.items() if not s.running), None)
                if idle is None:
                    raise ValueError('Too many active conversations')
                old = self.items.pop(idle)
                old.closed = True
            self.items[key] = VoiceSession(cid, owner, dt_util.utcnow())
        return self.items[key]

    def begin(self, session, text):
        session.turn += 1
        session.touched = dt_util.utcnow()
        session.waiting = False
        session.listen_reason = 'new_request'
        session.listen_override = None
        text = normalise(text)
        session.correction = None
        if CORRECTION.search(text) and (session.goal or session.pending or session.last_action):
            session.correction = {
                'request': text[:300], 'previous_goal': session.goal,
                'previous_entities': list(session.entities),
                'previous_pending': deepcopy(session.pending['actions']) if session.pending and 'actions' in session.pending else None,
                'previous_action': deepcopy(session.last_action),
            }
            session.task_state = 'in_progress'
        session.command_turn = bool(COMMAND.search(text))
        # Preserve the unanswered question through the next model call, so short answers
        # and corrections retain the task they belong to.
        if not session.correction and (not session.question or session.command_turn):
            session.goal = text[:200]
            session.task_state = 'in_progress'
            session.missing_detail = ''
        if self.options.get(CONF_SESSIONS):
            if START.search(text) or (not session.command_turn and INFORMATION.search(text)):
                session.active = True
                session.mode = 'discussion'
                session.topic = text[:200]
                session.goal = text[:200]
                session.listen_reason = 'information_discussion'

    def finish(self, session, result):
        """Apply one listening policy to the actual final reply, regardless of provider flags."""
        from homeassistant.helpers import intent

        text = spoken_text(result.response)
        session.question = ''
        session.waiting = False
        if result.response.response_type == intent.IntentResponseType.ERROR or not text:
            result.continue_conversation = False
            session.listen_reason = 'error_or_empty_reply'
            session.task_state = 'in_progress'
            return result
        # A discussion remains active until the user ends it (or its inactivity timeout).
        # A model's completed task or active=false must not end the user's discussion.
        if not ends_in_question(text):
            session.task_state = 'completed'
            session.missing_detail = ''
        elif session.task_state != 'completed':
            session.task_state = 'waiting'
        if session.active and not ends_in_question(text):
            text += " " + FOLLOW_UP
            result.response.async_set_speech(text)
        session.waiting = ends_in_question(text)
        result.continue_conversation = session.waiting
        if session.waiting:
            session.question = re.split(r"(?<=[.!])\s+", text)[-1][-300:]
            session.task_state = 'waiting' if not session.active else session.task_state
            session.listen_reason = 'discussion_follow_up' if session.active else 'reply_ends_in_question'
        else:
            session.task_state = 'completed'
            session.missing_detail = ''
            session.listen_reason = 'completed_single_request'
        return result

    def close(self, session):
        self.memory.discard(session)
        session.resume_choices.clear()
        session.active = False
        session.listen_reason = 'user_ended'
        session.task_state = 'completed'
        session.closed = True
        session.pending = None
        session.question = ''
        session.waiting = False
        session.undo.clear()
        session.correction = None
        session.investigation = None

    def rekey(self, session):
        """Use fresh provider history on resumption, never an old confirmation exchange."""
        for key, value in list(self.items.items()):
            if value is session:
                self.items.pop(key)
        session.id = uuid.uuid4().hex
        self.items[(session.id, *session.owner)] = session

    def record(self, session, text, result, guest=False):
        if self.options.get(CONF_SESSIONS) and not guest:
            self.memory.record(session, text, result)

    def describe(self):
        self.prune()
        return [s.describe() for s in self.items.values() if not s.closed]

    def stop(self):
        for session in self.items.values():
            self.close(session)
        self.items.clear()
        self.memory.clear()
