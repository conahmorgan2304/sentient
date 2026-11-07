"""Bounded device/value clarification. A selection is data, never action approval."""
from __future__ import annotations

import re
from copy import deepcopy

from homeassistant.auth.permissions.const import POLICY_READ
from homeassistant.components.homeassistant.exposed_entities import async_should_expose
from homeassistant.helpers import area_registry as ar

from .presence import area_of
from .voice_locale import clean

CONF = 'specific_clarification'


def enabled(options):
    return bool(options.get(CONF) and options.get('conversation_sessions'))


async def candidates(hass, ids, context):
    user_id = getattr(context, 'user_id', None)
    user = await hass.auth.async_get_user(user_id) if user_id else None
    if user_id and (user is None or not user.is_active):
        return []
    rows = []
    for eid in dict.fromkeys(ids):
        state = hass.states.get(eid)
        if state is None or not async_should_expose(hass, 'conversation', eid):
            continue
        if user and not user.permissions.check_entity(eid, POLICY_READ):
            continue
        area = ar.async_get(hass).async_get_area(area_of(hass, eid) or '')
        name, room = state.name, area.name if area else ''
        label = f'{name} ({room})' if room else name
        rows.append({'entity_id': eid, 'name': name, 'area': room, 'label': label})
    return rows


async def offer(hass, session, ids, context, kind='device'):
    """Use real names and a stable ordered choice list, not arbitrary model wording."""
    if session.closed or not session.running:
        return {'error': 'No active conversation'}
    if session.owner[2] != getattr(context, 'user_id', None):
        return {'error': 'This clarification belongs to a different user'}
    turn = session.turn
    if kind not in ('device', 'temperature'):
        return {'error': 'Unsupported clarification kind'}
    resolution = session.clarification_resolution or {}
    if (kind == 'temperature' and 'temperature' in resolution) or (kind == 'device' and resolution.get('entities')):
        return {'error': 'The user already supplied this detail. Use clarification_resolution; do not ask the same question again.'}
    if kind == 'device':
        if not 2 <= len(ids) <= 5 or len(set(ids)) != len(ids):
            return {'error': 'Provide two to five distinct candidate entity IDs; narrow the choices first'}
        rows = await candidates(hass, ids, context)
        if session.closed or session.forgotten or not session.running or session.turn != turn:
            return {'error': 'Conversation ended'}
        if len(rows) != len(ids):
            return {'error': 'Some choices are no longer accessible; resolve the devices again'}
        if len({clean(row['label']) for row in rows}) != len(rows):
            return {'error': 'These devices have identical names and rooms. Ask for distinct names; do not guess'}
        question = 'Which device: ' + '; '.join(f"{i + 1}, {row['label']}" for i, row in enumerate(rows)) + '?'
    else:
        targets = list(session.entities[:5])
        rows = await candidates(hass, targets, context)
        if len(rows) != len(targets) or session.closed or session.forgotten or not session.running or session.turn != turn:
            return {'error': 'The temperature target is no longer accessible; resolve it again'}
        question = 'What temperature would you like?'
    previous = session.clarification
    request = (previous or {}).get('request') or resolution.get('request') or session.turn_text
    if not previous and session.correction and session.correction.get('previous_goal'):
        request = session.correction['previous_goal'] + '; correction: ' + session.turn_text
    attempts = previous.get('attempts', 0) if previous else 0
    session.pending = None  # a new target/value question is not a security confirmation
    session.clarification = {'kind': kind, 'choices': rows, 'request': request[:300], 'attempts': attempts,
                             'question': question, 'turn': session.turn,
                             'correction': deepcopy({k: v for k, v in (session.correction or {}).items() if k != 'previous_pending'})}
    session.question = question
    session.task_state = 'waiting'
    session.missing_detail = kind
    session.listen_reason = 'specific_clarification'
    return {'question': question, 'choices': rows, 'note': 'Ask exactly this question and wait. Do not act. Selection is not confirmation.'}


def selection(text, rows):
    """Exact named/numbered/room choices only; never match a substring or an affirmative."""
    raw = clean(text)
    said = re.sub(r'^(?:(?:no|actually|i meant|the)\s+)+', '', raw)
    matches = [i for i, row in enumerate(rows) if said and any(raw == clean(row[k]) or said == clean(row[k])
               for k in ('name', 'area', 'label') if row[k])]
    picked = None
    if said in ('both', 'both of them') and len(rows) == 2:
        picked = list(range(2))
    elif said in ('all', 'all of them'):
        picked = list(range(len(rows)))
    ordinal = {'first': 0, 'first one': 0, '1': 0, 'one': 0, 'second': 1, 'second one': 1, '2': 1, 'two': 1,
               'third': 2, 'third one': 2, '3': 2, 'fourth': 3, 'fourth one': 3, '4': 3, 'fifth': 4, 'fifth one': 4, '5': 4}
    if said in ordinal and ordinal[said] < len(rows):
        picked = [ordinal[said]]
    # A device called "Second" must not silently select a different numbered choice.
    if len(matches) > 1 or (matches and picked is not None and matches != picked):
        return None
    return matches or picked


async def consume(hass, session, text, context):
    """Return a local retry/stop or structured selection for the normal action path."""
    item = session.clarification
    if item is None:
        return None
    if session.owner[2] != getattr(context, 'user_id', None):
        return {'stop': 'This clarification belongs to a different user. Please start again.'}
    from .sessions import COMMAND, INFORMATION
    if COMMAND.search(text) or INFORMATION.search(text):
        session.clarification = None  # a new explicit request supersedes the unanswered question
        session.question = ''
        session.missing_detail = ''
        session.entities = []
        return None
    resolved = None
    old = item['choices']
    rows = await candidates(hass, [r['entity_id'] for r in old], context)
    if session.closed or session.forgotten or session.clarification is not item:
        return {'stop': 'That conversation has ended. Please start again.'}
    if rows != old:
        session.clarification = None
        return {'stop': 'The available device choices changed. Please ask again with the device name.'}
    if item['kind'] == 'device':
        indices = selection(text, rows)
        if indices is not None:
            resolved = {'entities': [rows[i]['entity_id'] for i in indices]}
    else:
        match = re.fullmatch(r'\s*(-?\d{1,3}(?:[.,]\d{1,2})?)\s*(?:degrees?\s*)?(c|f|celsius|fahrenheit|°c|°f)?[.!? ]*', text, re.I)
        if match:
            resolved = {'temperature': float(match[1].replace(',', '.')), 'unit': match[2] or None,
                        'entities': [row['entity_id'] for row in rows]}
    if resolved is not None:
        session.clarification = None
        session.pending = None
        session.clarification_resolution = {'request': item['request'], 'answer': text[:200], **resolved,
                                            'permission': 'selection only; require a new confirmation for sensitive actions'}
        session.correction = deepcopy(item.get('correction')) or None
        session.goal = item['request']
        session.command_turn = bool(COMMAND.search(item['request']))
        session.entities = resolved.get('entities', [])
        session.question = ''
        session.missing_detail = ''
        session.follow_up_context = None
        session.listen_reason = 'clarification_resolved'
        return {'resolved': True}
    item['attempts'] += 1
    if item['attempts'] >= 2:
        session.clarification = None
        return {'stop': 'I still need an exact device name or temperature. Please start again with the full request.'}
    return {'question': 'I could not match that answer. Please use a listed name or number. ' + item['question']}
