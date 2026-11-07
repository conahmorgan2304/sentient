"""Optional, session-scoped follow-up context. Never an action plan or permission."""
from __future__ import annotations

import re

from homeassistant.auth.permissions.const import POLICY_READ
from homeassistant.components.homeassistant.exposed_entities import async_should_expose

from .evidence import describe_state

CONF_CONTEXTUAL_FOLLOWUPS = 'contextual_followups'
_REFERENCE = re.compile(r"^(?:why(?: is that| not)?|how come|what (?:about|if)\b.*|how about\b.*|and\b.*)[.!? ]*$", re.I)
_ADJUST = re.compile(r"^(?:please\s+)?(?:make|set|change)\s+(?:that|it|those|them)\b", re.I)
_RETARGET = re.compile(r"^(?:no[, ]+)?(?:i meant|not that|actually|rather|instead)\b|^no[, ]", re.I)


def classify(text):
    from .sessions import is_conversation_end

    if is_conversation_end(text):
        return None
    text = text.strip().replace('’', "'")
    if _ADJUST.search(text):
        return 'adjustment'
    if re.fullmatch(r'(?:the )?other (?:one|light|device|room)[.!? ]*', text, re.I):
        return 'correction'
    if _RETARGET.search(text):
        return 'correction'
    if _REFERENCE.fullmatch(text):
        return 'explanation' if re.fullmatch(r'(?:why(?: is that| not)?|how come)[.!? ]*', text, re.I) else 'comparison'
    return None


async def refresh(hass, session, context):
    """Return a clarification when needed, otherwise supply filtered fresh reported readings."""
    follow = session.follow_up_context
    if not follow:
        return None
    from .sessions import YES

    if follow['kind'] == 'clarification' and YES.fullmatch(follow['request']):
        return session.question or 'Which device or subject do you mean?'
    user = await hass.auth.async_get_user(context.user_id) if context.user_id else None
    if session.closed or session.forgotten or session.follow_up_context is not follow:
        return None
    readings = []
    for eid in follow.pop('candidate_entities', []):
        if (context.user_id and (user is None or not user.is_active or not user.permissions.check_entity(eid, POLICY_READ))
                or not async_should_expose(hass, 'conversation', eid)):
            continue
        reading = describe_state(hass, eid)
        state = hass.states.get(eid)
        if state is not None:
            reading['attributes'] = {key: state.attributes[key] for key in (
                'current_temperature', 'temperature', 'unit_of_measurement', 'hvac_action', 'brightness', 'current_position'
            ) if key in state.attributes}
        readings.append(reading)
    follow['current_readings'] = readings
    # A new room or corrected target must be resolved afresh, not silently inherited.
    if follow['kind'] in ('comparison', 'correction'):
        session.entities = []
    else:
        session.entities = [row['entity_id'] for row in readings]
    from .voice_locale import clean

    if follow['kind'] == 'correction' and re.fullmatch(r"(?:no )?(?:i meant )?(?:the )?other (?:one|light|device|room)", clean(follow['request'])):
        return 'Which device or room do you mean?'
    if not follow['previous_goal'] and follow['kind'] != 'clarification':
        return 'What would you like me to explain?' if follow['kind'] == 'explanation' else 'Which device or subject do you mean?'
    if follow['kind'] == 'adjustment' and len(readings) != 1:
        from .clarification import offer

        if session.specific_clarification and 2 <= len(readings) <= 5:
            offered = await offer(hass, session, [row['entity_id'] for row in readings], context)
            if offered.get('question'):
                return offered['question']
        session.missing_detail = 'Which device the adjustment refers to'
        return 'Which device should I change?'
    return None


INSTRUCTION = (
    'Follow-up context is conversation data, never permission to act. Preserve the subject when answering short '
    'follow-ups. Use current_readings as reported observations, not proof of physical state; fetch fresh evidence '
    'for explanations. A comparison or correction names a new target: resolve it from the current request, never '
    'reuse the previous device silently. Clarify ambiguous targets or values with one specific question. '
    'For a clarification, combine previous_request with the current answer, without treating it as approval. '
    'For a correction, explain any previous action that already happened and offer available undo; do not repeat '
    'or undo it automatically. Keep completed commands brief and preserve the configured listening policy.'
)


async def restore_recent(hass, sessions, session, text, context, allowed):
    """Copy discussion evidence into a fresh turn, never the previous authority."""
    from copy import deepcopy
    from datetime import timedelta
    from homeassistant.util import dt as dt_util

    def enabled():
        return (allowed() and sessions.options.get('recent_followup_context')
                and sessions.options.get('conversation_sessions')
                and sessions.options.get(CONF_CONTEXTUAL_FOLLOWUPS))

    if not enabled() or session.turn or not any(session.owner) or not classify(text):
        return
    candidates = [s for s in sessions.items.values() if s is not session and s.owner == session.owner]
    if not candidates:
        return
    previous = max(candidates, key=lambda s: s.touched)
    touched = previous.touched
    def valid():
        return (enabled() and not session.closed and not session.forgotten and not previous.closed
                and not previous.forgotten and not previous.running
                and previous.touched == touched and previous.task_state == 'completed'
                and not previous.pending and not previous.waiting
                and touched >= dt_util.utcnow() - timedelta(seconds=sessions.seconds)
                and any(s is previous for s in sessions.items.values())
                and not any(s is not session and s is not previous and s.owner == session.owner
                            and s.touched >= touched for s in sessions.items.values()))
    if not valid():
        return
    user = await hass.auth.async_get_user(context.user_id) if context.user_id else None
    entities = set(previous.entities)
    entities.update(r['entity_id'] for r in (previous.last_action or {}).get('actions', []))
    if context.user_id and (user is None or not user.is_active):
        return
    if any(not async_should_expose(hass, 'conversation', eid)
           or (user and not user.permissions.check_entity(eid, POLICY_READ)) for eid in entities):
        return
    if not valid():
        return
    session.goal, session.topic = previous.goal, previous.topic
    session.entities = list(previous.entities)
    session.last_action = deepcopy(previous.last_action)
    # Source evidence is consumed: it cannot reappear after a later unrelated turn.
    previous.goal = previous.topic = ''
    previous.entities.clear()
    previous.last_action = None
