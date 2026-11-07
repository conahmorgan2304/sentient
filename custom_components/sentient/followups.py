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
