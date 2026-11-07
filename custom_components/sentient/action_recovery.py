"""Read fresh evidence for the last guarded batch; never replay uncertain dispatches."""
from copy import deepcopy
from datetime import timedelta
import re

from homeassistant.auth.permissions.const import POLICY_READ
from homeassistant.components.homeassistant.exposed_entities import async_should_expose
from homeassistant.util import dt as dt_util

from .control import snapshot, target_reached
from .voice_locale import clean

CONF = 'action_recovery'
PHRASES = {'that didn\'t work', 'that did not work', 'it didn\'t work', 'it did not work',
           'did that work', 'what failed', 'retry the failed devices', 'retry failed devices', 'try the failed ones again'}


def requested(text):
    return re.sub(r'\bdidnt\b', "didn't", clean(text)) in PHRASES


def enabled(options):
    return bool(options.get(CONF) and options.get('conversation_sessions'))


def outcome_text(hass, result):
    """A state report isn't proof of physical success."""
    parts = []
    labels = {'verified': 'reported the requested state', 'attempted': 'request sent; result not yet confirmed',
              'failed': 'request failed; delivery may be uncertain', 'skipped': 'not sent', 'not_started': 'not sent'}
    for row in result.get('recovery_outcomes', result.get('actions', [])):
        state = hass.states.get(row['entity_id'])
        label = 'not sent; validation failed' if row['status'] == 'failed' and row.get('dispatched') is False else labels.get(row['status'], 'result uncertain')
        parts.append(f"{state.name if state else 'A device'}: {label}.")
    return ' '.join(parts)


async def inspect(control, session, context):
    """Prepare a new, separate-turn confirmation only for never-dispatched actions."""
    session.pending = None
    record = control.recovery_records.get(session.owner)
    if not record or record['expires'] < dt_util.utcnow() or not any(session.owner):
        return {'text': "I don't have a recent guarded action record for this speaker and user. I haven't retried anything. Please name the device and what should have happened."}
    generation = control.recovery_generation
    user_id = getattr(context, 'user_id', None)
    if user_id != session.owner[2]:
        return {'text': 'That action record belongs to a different user.'}
    user = await control.hass.auth.async_get_user(user_id) if user_id else None
    if user_id and (user is None or not user.is_active):
        return {'text': 'I cannot access those device results with the current permissions.'}
    parts, retry, before = [], [], {}
    for row in record['rows']:
        action = row['action']
        eid = action['entity_id']
        if not async_should_expose(control.hass, 'conversation', eid) or (user and not user.permissions.check_entity(eid, POLICY_READ)):
            parts.append('One device is no longer accessible.')
            continue
        state = control.hass.states.get(eid)
        name = state.name if state else 'A device'
        if target_reached(state, action):
            parts.append(f'{name} now reports the requested state; I will not repeat it.')
        elif row['status'] == 'verified':
            parts.append(f'{name} previously reported success but has since changed. I will not overwrite that change.')
        elif row['dispatched']:
            parts.append(f'{name}: the request was sent, but the result is uncertain. I have not repeated it.')
        else:
            parts.append(f'{name}: the action was not sent.')
            try:
                await control.validate([action], context)
            except (ValueError, TypeError):
                parts.append('It cannot safely be sent with the current device state or permissions.')
                continue
            state = control.hass.states.get(eid)
            if state is not None and not target_reached(state, action):
                retry.append(deepcopy(action))
                before[eid] = snapshot(state)
    if (session.closed or record['expires'] < dt_util.utcnow() or generation != control.recovery_generation
            or control.recovery_records.get(session.owner) is not record or not enabled(control.options)):
        return {'text': 'That recovery request is no longer current. Please ask again.'}
    text = ' '.join(parts)
    if retry and session.authorised:
        summary = control.summary(retry)
        question = f'Shall I send only these actions that were not sent: {summary}?'
        session.pending = {'actions': retry, 'turn': session.turn, 'expires': dt_util.utcnow() + timedelta(seconds=60),
                           'before': before, 'summary': summary, 'question': question, 'recovery': True}
        return {'text': text + ' ' + question, 'listen': True}
    return {'text': text or 'No device result could be verified. Nothing has been retried.'}
