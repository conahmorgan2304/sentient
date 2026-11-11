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
    r"^(?:(?:okay|ok|no|well) )?(?:that(?:'?s| is) (?:all(?: for now)?|enough|it)|"
    r"enough(?: now)?|stop(?: listening| now)?|end (?:the )?conversation|"
    r"goodbye|never ?mind|we(?:'?re| are) done|i(?:'?m| am) (?:done|finished)|all done|"
    r"nothing else|no more|(?:no|nope|nah) (?:thanks|thank you|i(?:'?m| am) (?:good|fine|okay|ok|alright|all right))|"
    r"(?:no )?i (?:don't|do not) need anything else|thanks|thank you)"
    r"(?: (?:please|thanks|thank you))?$", re.I)
GENERAL_FOLLOWUP = re.compile(
    r"\b(?:anything|something) (?:else|more|further)\b|"
    r"\b(?:any more|further) (?:help|assistance|questions)\b", re.I)
NEGATIVE_REPLY = {"no", "nope", "nah", "not now", "not right now"}
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
# Conversation cues (setting "Understand sign-offs, corrections and changes of subject", on by default).
CONF_CUES = "conversation_cues"
SIGN_OFF = re.compile(
    r"(?:(?:that'?s|that is|it'?s|oh|ok|okay) )?(?:great|perfect|lovely|brilliant|smashing|fab|fine|grand|cool|nice one|"
    r"sound|ace|spot on|sorted|got it|i'?m good|all good|that'?ll do|that will do|no that'?s fine|cheers(?: mate| then)?|"
    r"ta(?: very much)?)(?: (?:thanks|thank you|cheers|ta|mate))*", re.I)
CUE_CORRECTION = re.compile(
    r"^(?:sorry[, ]+(?:i meant|i said|not)\b|(?:that'?s |no[, ]+)?(?:the )?wrong (?:one|light|lamp|room|device|thing|speaker)\b|"
    r"i said\b|not the \w+(?: \w+)?[, ]+the\b)", re.I)
SUBJECT_CHANGE = re.compile(
    r"^(?:(?:ok(?:ay)?|right|anyway|so|also)[, ]+)?(?:(?:on )?(?:a )?(?:different|another|new|separate) "
    r"(?:note|subject|topic|question|thing)|changing the subject|change of subject|something else|unrelated(?: question)?|"
    r"while i (?:have|'?ve got) you)\b[,:.!?]?\s*(.*)$", re.I)
FOLLOW_UP = "Is there anything else you'd like to know?"
# Said in turn so it doesn't sound like a script; every one matches GENERAL_FOLLOWUP, so a plain "no" still ends it.
FOLLOW_UPS = (
    FOLLOW_UP,
    "Anything else?",
    "Anything else I can help with?",
    "Anything more on that, or something else?",
    "Want to know anything else?",
    "Anything else on your mind?",
    "Something else I can check for you?",
    "Any more questions?",
)


def is_follow_up(text: str) -> bool:
    """One of our own 'anything else?' questions (not a real question from the model)."""
    from .voice_locale import FOLLOWUPS

    return (text or "").strip() in (*FOLLOW_UPS, *FOLLOWUPS.values())


def normalise(text):
    return text.strip().replace("’", "'")


def is_conversation_end(text, question='', persona='', language=None, cues=False):
    """Recognise a complete spoken ending, tolerating STT punctuation and name addressing.

    With conversation cues on, everyday sign-offs ("cheers", "perfect, thanks", "that'll do") end it too, but only
    when nothing specific is waiting for an answer, so "perfect" never answers "shall I turn the heating up?".
    """
    from .voice_locale import ending, answer, generic_question

    if ending(text, language) or (answer(text, language) == 'no' and (not question or generic_question(question, language) or GENERAL_FOLLOWUP.search(question))):
        return True

    def clean(value):
        value = normalise(value).casefold()
        value = re.sub(r"\bthankyou\b", "thank you", value)
        value = re.sub(r"[^\w\s']", " ", value).strip("'")
        return " ".join(value.split())

    text = clean(text)
    name = clean(persona)
    if name:
        if text.startswith(name + " "):
            text = text[len(name) + 1:]
        if text.endswith(" " + name):
            text = text[:-len(name) - 1]
    if END.fullmatch(text):
        return True
    if cues and SIGN_OFF.fullmatch(text) and (not question or bool(GENERAL_FOLLOWUP.search(question))):
        return True
    for prefix in ("no thank you ", "no thanks ", "thank you ", "thanks "):
        if text.startswith(prefix) and END.fullmatch(text[len(prefix):]):
            return True
    # "No" can answer a useful question (e.g. "Can you hear that speaker?").
    # Only treat it as a sign-off when declining the generic follow-up or when idle.
    return text in NEGATIVE_REPLY and (not question or bool(GENERAL_FOLLOWUP.search(question)))


def spoken_text(response):
    """Read the final spoken response, including SSML, rather than tool metadata."""
    speech = response.speech
    if "plain" in speech:
        return speech["plain"].get("speech", "").strip()
    return html.unescape(re.sub(r"<[^>]+>", "", speech.get("ssml", {}).get("speech", ""))).strip()


def ends_in_question(text):
    return text.rstrip().rstrip('"”’」』').rstrip().endswith(("?", "？", "؟"))

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
    recovery_record: dict | None = None
    lock: asyncio.Lock = field(default_factory=asyncio.Lock)
    running: bool = False
    authorised: bool = False
    closed: bool = False
    forgotten: bool = False
    listen_override: bool | None = None
    mode: str = 'command'
    goal: str = ''
    task_state: str = 'in_progress'
    missing_detail: str = ''
    listen_reason: str = 'new_request'
    command_turn: bool = False
    language: str | None = None
    specific_clarification: bool = False
    turn_text: str = ""
    clarification: dict | None = None
    clarification_resolution: dict | None = None
    follow_up_context: dict | None = None
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
                'clarification': self.clarification, 'clarification_resolution': self.clarification_resolution,
                'correction': self.correction, 'investigation': self.investigation,
                **({'follow_up_context': self.follow_up_context} if self.follow_up_context else {}),
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

    def begin(self, session, text, language=None):
        session.specific_clarification = bool(self.options.get("specific_clarification") and self.options.get(CONF_SESSIONS))
        session.turn_text = text[:300]
        session.clarification_resolution = None
        session.language = language
        session.turn += 1
        session.touched = dt_util.utcnow()
        session.waiting = False
        session.listen_reason = 'new_request'
        session.listen_override = None
        text = normalise(text)
        from .followups import CONF_CONTEXTUAL_FOLLOWUPS, classify

        connected = self.options.get(CONF_SESSIONS) and self.options.get(CONF_CONTEXTUAL_FOLLOWUPS)
        prior_follow = session.follow_up_context or {}
        cues = bool(self.options.get(CONF_CUES, True))
        change = SUBJECT_CHANGE.match(text) if cues and (session.active or session.goal) else None
        if change:
            # "On another note, ..." starts afresh: no devices, correction or follow-up carried over from before.
            text = change.group(1).strip() or text
            session.entities = []
            session.correction = None
            session.follow_up_context = None
            prior_follow = {}
        kind = classify(text) if connected else None
        if connected and not kind and cues and not change and CUE_CORRECTION.search(text):
            kind = 'correction'
        if (connected and prior_follow.get('needs_clarification') and not kind
                and not COMMAND.search(text) and not INFORMATION.search(text)):
            kind = 'clarification'
        session.follow_up_context = ({'kind': kind, 'request': text[:300], 'previous_goal': session.goal,
                                      'previous_topic': session.topic, 'candidate_entities': list(session.entities)} if kind else None)
        if kind == 'clarification':
            session.follow_up_context['previous_request'] = prior_follow.get('previous_request') or prior_follow['request']
            if not session.goal:
                session.goal = f"{prior_follow['request']} ({text})"[:200]
        elif connected and not kind and (COMMAND.search(text) or INFORMATION.search(text)):
            session.entities = []  # an explicit new subject must not inherit old targets
        session.correction = None
        if (CORRECTION.search(text) or (cues and not change and CUE_CORRECTION.search(text)) or kind in ('correction', 'adjustment')) and (session.goal or session.pending or session.last_action):
            session.correction = {
                'request': text[:300], 'previous_goal': session.goal,
                'previous_entities': list(session.entities),
                'previous_pending': deepcopy(session.pending['actions']) if session.pending and 'actions' in session.pending else None,
                'previous_action': deepcopy(session.last_action),
            }
            session.task_state = 'in_progress'
        session.command_turn = bool(COMMAND.search(text)) or kind == "adjustment"
        # Preserve the unanswered question through the next model call, so short answers
        # and corrections retain the task they belong to.
        if not kind and not session.correction and (not session.question or session.command_turn):
            session.goal = text[:200]
            session.task_state = 'in_progress'
            session.missing_detail = ''
        if self.options.get(CONF_SESSIONS):
            if START.search(text) or (not session.command_turn and INFORMATION.search(text)):
                session.active = True
                session.mode = 'discussion'
                if not kind:
                    session.topic = text[:200]
                    session.goal = text[:200]
                session.listen_reason = 'information_discussion'
        if change:
            session.topic = text[:200] if text != change.group(0) else ''
            session.goal = session.topic or session.goal
            session.listen_reason = 'new_subject'

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
        if (session.active and not session.command_turn and not ends_in_question(text)
                and self.options.get("conversation_style") != "natural"):
            # brief style: keep a discussion going with a varied "anything else?"; the natural style asks its own
            # follow-up questions when they fit, so a stock one isn't added
            text += " " + self.next_follow_up(session.language)
            result.response.async_set_speech(text)
        from .conversation_policy import decide_listening

        listening = decide_listening(reply_is_question=ends_in_question(text),
                                     discussion_active=session.active and not session.command_turn)
        session.waiting = listening.continue_listening
        result.continue_conversation = listening.continue_listening
        session.listen_reason = listening.reason
        if session.waiting:
            session.question = re.split(r"(?<=[.!])\s+", text)[-1][-300:]
            session.task_state = 'waiting' if not session.active else session.task_state
        else:
            session.task_state = 'completed'
            session.missing_detail = ''
        return result

    def next_follow_up(self, language=None) -> str:
        """A different 'anything else?' each time, never one of the last few."""
        import random
        from .voice_locale import FOLLOWUPS

        if local := FOLLOWUPS.get((language or "").split("-")[0].lower()):
            return local

        recent = getattr(self, "_recent_follow_ups", [])
        choice = random.choice([f for f in FOLLOW_UPS if f not in recent] or list(FOLLOW_UPS))
        self._recent_follow_ups = (recent + [choice])[-4:]
        return choice

    def close(self, session):
        if self.options.get('recent_followup_context'):
            for other in self.items.values():
                if other is not session and other.owner == session.owner:
                    other.goal = other.topic = ''
                    other.entities.clear()
                    other.last_action = None
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
        session.follow_up_context = None
        session.clarification = None
        session.clarification_resolution = None
        session.recovery_record = None
        session.investigation = None

    def rekey(self, session):
        """Use fresh provider history on resumption, never an old confirmation exchange."""
        for key, value in list(self.items.items()):
            if value is session:
                self.items.pop(key)
        session.id = uuid.uuid4().hex
        self.items[(session.id, *session.owner)] = session

    def record(self, session, text, result, guest=False):
        if self.options.get(CONF_SESSIONS) and not guest and not session.forgotten:
            self.memory.record(session, text, result)

    def describe(self):
        self.prune()
        return [s.describe() for s in self.items.values() if not s.closed]

    def stop(self):
        for session in self.items.values():
            self.close(session)
        self.items.clear()
        self.memory.clear()
