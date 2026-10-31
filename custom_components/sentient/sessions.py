"""Short-lived voice sessions. Nothing here is persisted or shared between speakers/users."""
from __future__ import annotations

import asyncio
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
END = re.compile(r"^(?:that'?s all|that is all|stop listening|end conversation|goodbye|never mind)[.! ]*$", re.I)
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

    def describe(self) -> dict:
        return {'conversation_id': self.id, 'state': 'awaiting_confirmation' if self.pending else
                'listening' if self.active or self.waiting else 'wake_word', 'topic': self.topic,
                'entities': list(self.entities), 'unanswered_question': self.question,
                'pending_action': self.pending.get('summary') if self.pending else None,
                'confirmation_expires': self.pending['expires'].isoformat() if self.pending and self.pending.get('expires') else None,
                'last_action': self.last_action, 'undo_available': bool(self.undo),
                'last_activity': self.touched.isoformat()}


current_session: ContextVar[VoiceSession | None] = ContextVar('sentient_voice_session', default=None)


class Sessions:
    def __init__(self, options):
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
        session.question = ''
        session.waiting = False
        session.listen_override = None
        if self.options.get(CONF_SESSIONS) and START.search(text):
            session.active = True
            session.topic = text[:200]

    def close(self, session):
        session.active = False
        session.closed = True
        session.pending = None
        session.question = ''
        session.waiting = False
        session.undo.clear()

    def describe(self):
        self.prune()
        return [s.describe() for s in self.items.values() if not s.closed]

    def stop(self):
        for session in self.items.values():
            self.close(session)
        self.items.clear()
