"""Bounded, in-memory unfinished topics and conversation diagnostics; never action plans."""
from __future__ import annotations

from collections import deque
from copy import deepcopy
from datetime import timedelta
import re
import uuid

from homeassistant.util import dt as dt_util

RESUME = re.compile(
    r"^(?:back to|let'?s get back to|go back to)\s+(.+?)[.!?]*$|"
    r"^(?:resume|continue) (?:our|the|that) (?:conversation|discussion)(?: about (.+?))?[.!?]*$", re.I)
STOP_WORDS = set("the a an our that this issue problem discussion conversation about back to why did what is was".split())


def words(text):
    return {w for w in re.findall(r"[a-z0-9]+", text.lower()) if len(w) > 2 and w not in STOP_WORDS}


class TopicMemory:
    def __init__(self):
        self.topics = []
        self.traces = deque(maxlen=40)

    def prune(self):
        cutoff = (dt_util.utcnow() - timedelta(hours=24)).isoformat()
        self.topics = [t for t in self.topics if t['saved_at'] >= cutoff][:40]
        self.traces = deque((t for t in self.traces if t['time'] >= cutoff), maxlen=40)

    def record(self, session, text, result):
        from .sessions import spoken_text, YES, NO, UNDO, FOLLOW_UP
        from homeassistant.helpers import intent

        self.prune()
        reply = spoken_text(result.response)
        self.traces.appendleft({
            'time': dt_util.utcnow().isoformat(), 'conversation_id': session.id,
            'request': text[:300], 'reply': reply[:500], 'goal': session.goal,
            'entities': list(session.entities[:12]), 'mode': session.mode,
            'task_state': session.task_state, 'listen': result.continue_conversation,
            'reason': session.listen_reason, 'resumed_from': session.resumed_from,
        })
        if (session.closed or not session.active or session.pending or session.command_turn
                or session.listen_reason != 'discussion_follow_up'
                or session.undo_turn == session.turn or (session.resume_context or {}).get('turn') == session.turn
                or not any(session.owner)
                or YES.fullmatch(text.strip()) or NO.fullmatch(text.strip()) or UNDO.fullmatch(text.strip())
                or result.response.response_type == intent.IntentResponseType.ERROR or not reply):
            return
        subject = (session.investigation or {}).get('subject') or session.topic or session.goal
        if not subject:
            return
        question = session.question if session.question != FOLLOW_UP else ''
        # Explicit field allowlist. No pending, undo, correction, action data or authorisation.
        topic = {
            'id': uuid.uuid4().hex, 'owner': session.owner, 'source_session': session.id,
            'subject': subject[:200], 'goal': session.goal[:200],
            'entities': list((session.entities or (session.investigation or {}).get('entities', []))[:12]),
            'question': question[:300], 'previous_reply': reply[:500],
            'saved_at': dt_util.utcnow().isoformat(),
        }
        existing = next((t for t in self.topics if t['owner'] == session.owner
                         and t['subject'].casefold() == topic['subject'].casefold()), None)
        if existing:
            topic['id'] = existing['id']
        self.topics = [t for t in self.topics if t['id'] != topic['id']]
        self.topics.insert(0, topic)
        owned = [t['id'] for t in self.topics if t['owner'] == session.owner][:8]
        self.topics = [t for t in self.topics if t['owner'] != session.owner or t['id'] in owned][:40]

    def candidates(self, owner, query='', ids=None):
        self.prune()
        candidates = [t for t in self.topics if t['owner'] == owner and (ids is None or t['id'] in ids)]
        tokens = words(query)
        if tokens:
            candidates = [t for t in candidates if tokens <= words(t['subject'] + ' ' + t['goal'])]
        return deepcopy(candidates)

    def discard(self, session):
        self.topics = [t for t in self.topics if not (
            t['owner'] == session.owner and (t['source_session'] == session.id or t['id'] == session.resumed_from))]

    def clear(self):
        self.topics.clear()
        self.traces.clear()

    def diagnostics(self):
        self.prune()
        return deepcopy(list(self.traces)[:10])
