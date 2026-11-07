"""Read-only explanations of recorded conversation decisions; no inferred intentions."""
from datetime import timedelta

from homeassistant.auth.permissions.const import POLICY_READ
from homeassistant.components.homeassistant.exposed_entities import async_should_expose
from homeassistant.util import dt as dt_util

from .voice_locale import clean

CONF = 'behaviour_explanations'
LISTEN = {'why are you still listening', 'why are you listening', 'why did you stop listening'}
ACTION = {'did you actually do it', 'did you do it', 'what did you actually do'}
REASONS = {
    'awaiting_confirmation': 'I was waiting for confirmation before sending a device action.',
    'specific_clarification': 'I was waiting for a device or value to be clarified.',
    'specific_clarification_retry': 'The device or value was still unclear.',
    'follow_up_clarification': 'I needed to know which device or subject you meant.',
    'reply_ends_in_question': 'My previous reply ended with a question.',
    'discussion_follow_up': 'The information discussion was still open and my reply asked a follow-up question.',
    'completed_single_request': 'The single request was complete and my reply did not end with a question.',
    'error_or_empty_reply': 'The previous turn returned an error or an empty reply.',
    'user_ended': 'You ended the conversation.',
}


async def answer(hass, sessions, session, text, context, allowed):
    query = clean(text)
    if not (sessions.options.get(CONF) and sessions.options.get('conversation_sessions')) or query not in LISTEN | ACTION:
        return None
    if not allowed():
        return 'Recorded conversation details are unavailable in guest mode.'
    if not any(session.owner):
        return 'I cannot identify a recent conversation for this speaker and user.'
    candidates = [s for s in sessions.items.values() if s.owner == session.owner and s.turn and not s.closed]
    previous = max(candidates, key=lambda s: s.touched) if candidates else None
    if previous is None or previous.touched < dt_util.utcnow() - timedelta(seconds=sessions.seconds):
        return 'I do not have a recent conversation record for this speaker and user.'
    if previous is not session and previous.running:
        return 'The earlier request is still running; I do not have its final result yet.'
    if query in LISTEN:
        reason = ('awaiting_confirmation' if previous.pending else
                  'specific_clarification' if previous.clarification else previous.listen_reason)
        explanation = REASONS.get(reason, 'The recorded decision does not give me a specific reason.')
        return explanation + ' This describes the agent’s listening decision; I cannot verify the microphone’s hardware state.'
    from copy import deepcopy
    from .action_recovery import outcome_text

    result = deepcopy(previous.last_action or {})
    rows = result.get('recovery_outcomes', result.get('actions', []))
    if not rows:
        return 'I have no recorded guarded device action for that exchange. That does not prove that another integration did nothing.'
    touched = previous.touched
    user = await hass.auth.async_get_user(context.user_id) if context.user_id else None
    if (not allowed() or not sessions.options.get(CONF) or session.closed or previous.closed
            or previous.forgotten or previous.touched != touched
            or not any(s is previous for s in sessions.items.values())
            or touched < dt_util.utcnow() - timedelta(seconds=sessions.seconds)):
        return 'That conversation record is no longer available.'
    if context.user_id and (user is None or not user.is_active):
        return 'I cannot access the recorded devices with the current permissions.'
    visible = [r for r in rows if async_should_expose(hass, 'conversation', r['entity_id'])
               and (user is None or user.permissions.check_entity(r['entity_id'], POLICY_READ))]
    if not visible:
        return 'The recorded devices are not currently accessible.'
    return ('For the last recorded guarded action in this session: ' + outcome_text(hass, {'actions': visible})
            + ' These are recorded results, not a fresh check or proof of physical success. I have not repeated any action.')
