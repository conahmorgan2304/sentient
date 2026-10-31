"""Explicit, scoped voice actions. The model can propose; only a subsequent user turn confirms."""
from __future__ import annotations

import asyncio
from copy import deepcopy
from datetime import timedelta
from typing import Any

from homeassistant.auth.permissions.const import POLICY_CONTROL
from homeassistant.components.homeassistant.exposed_entities import async_should_expose
from homeassistant.core import Context
from homeassistant.exceptions import HomeAssistantError
from homeassistant.util import dt as dt_util

from .sessions import CONF_UNDO

# No service strings, scripts, scenes, templates, area targets or arbitrary service data.
ACTIONS = {
    'light': {'turn_on', 'turn_off'}, 'switch': {'turn_on', 'turn_off'},
    'fan': {'turn_on', 'turn_off'}, 'lock': {'lock', 'unlock'},
    'cover': {'open_cover', 'close_cover', 'stop_cover'},
    'climate': {'set_temperature', 'turn_off'}, 'vacuum': {'start', 'stop', 'return_to_base'},
}
LIGHT_ATTRS = ('brightness', 'color_mode', 'color_temp_kelvin', 'rgb_color', 'rgbw_color', 'rgbww_color', 'hs_color', 'xy_color')


def snapshot(state):
    return {'state': state.state, 'attributes': {k: deepcopy(state.attributes[k]) for k in LIGHT_ATTRS if k in state.attributes},
            'updated': state.last_updated.isoformat()}


def target_reached(state, action):
    if state is None or state.state in ('unavailable', 'unknown'):
        return False
    op = action['action']
    wanted = {'turn_on': 'on', 'turn_off': 'off', 'lock': 'locked', 'unlock': 'unlocked',
              'open_cover': 'open', 'close_cover': 'closed', 'start': 'cleaning', 'return_to_base': 'docked'}.get(op)
    if op == 'set_temperature':
        return state.attributes.get('temperature') == action['temperature']
    if wanted is None or state.state != wanted:
        return False
    if 'brightness_pct' in action and abs(float(state.attributes.get('brightness', -1000)) - action['brightness_pct'] * 2.55) > 2:
        return False
    if 'color_temp_kelvin' in action and state.attributes.get('color_temp_kelvin') != action['color_temp_kelvin']:
        return False
    if 'rgb_color' in action and list(state.attributes.get('rgb_color') or []) != action['rgb_color']:
        return False
    return True


class Control:
    def __init__(self, hass, options, timeline):
        self.hass, self.options, self.timeline = hass, options, timeline
        self._lock = asyncio.Lock()

    async def validate(self, actions, context):
        if not isinstance(actions, list) or not 1 <= len(actions) <= 20:
            raise ValueError('Choose between one and twenty explicit device actions')
        user = None
        if context and context.user_id:
            user = await self.hass.auth.async_get_user(context.user_id)
            if user is None or not user.is_active:
                raise ValueError('The user is not authorised')
        seen = set()
        for action in actions:
            if not isinstance(action, dict) or set(action) - {'entity_id', 'action', 'brightness_pct', 'color_temp_kelvin', 'rgb_color', 'temperature'}:
                raise ValueError('Unsupported action fields')
            eid, op = action.get('entity_id', ''), action.get('action')
            if not isinstance(eid, str) or eid in seen:
                raise ValueError('Each device must appear exactly once')
            seen.add(eid)
            domain = eid.split('.')[0]
            if op not in ACTIONS.get(domain, set()):
                raise ValueError('That device action is not supported by guarded control')
            state = self.hass.states.get(eid)
            if state is None or state.state in ('unknown', 'unavailable'):
                raise ValueError(f'{eid} has no reliable current state')
            if not async_should_expose(self.hass, 'conversation', eid):
                raise ValueError(f'{eid} is not exposed to Assist')
            if user and not user.permissions.check_entity(eid, POLICY_CONTROL):
                raise ValueError(f'No permission to control {eid}')
            if not self.hass.services.has_service(domain, op):
                raise ValueError(f'{domain}.{op} is unavailable')
            extra = set(action) - {'entity_id', 'action'}
            if extra and not (domain == 'light' and op == 'turn_on' or domain == 'climate' and op == 'set_temperature'):
                raise ValueError('Settings do not apply to that action')
            if domain == 'light':
                if 'temperature' in action or sum(k in action for k in ('rgb_color', 'color_temp_kelvin')) > 1:
                    raise ValueError('Choose one supported light colour setting')
                if 'brightness_pct' in action and not 1 <= float(action['brightness_pct']) <= 100:
                    raise ValueError('Brightness must be between 1 and 100')
                if 'rgb_color' in action and (not isinstance(action['rgb_color'], list) or len(action['rgb_color']) != 3 or
                                             any(type(v) is not int or not 0 <= v <= 255 for v in action['rgb_color'])):
                    raise ValueError('RGB needs three values between 0 and 255')
                if 'color_temp_kelvin' in action and not float(state.attributes.get('min_color_temp_kelvin', 1000)) <= float(action['color_temp_kelvin']) <= float(state.attributes.get('max_color_temp_kelvin', 10000)):
                    raise ValueError('Colour temperature is outside the light limits')
            if domain == 'climate' and op == 'set_temperature':
                if extra != {'temperature'}:
                    raise ValueError('Supply only a temperature for heating')
                temp = float(action['temperature'])
                if not float(state.attributes.get('min_temp', 5)) <= temp <= float(state.attributes.get('max_temp', 35)):
                    raise ValueError('Temperature is outside the thermostat limits')

    def needs_confirmation(self, actions):
        for a in actions:
            domain = a['entity_id'].split('.')[0]
            st = self.hass.states.get(a['entity_id'])
            if domain == 'lock':
                return True
            if domain == 'cover' and a['action'] != 'stop_cover':
                return True
            if domain in ('switch', 'vacuum') and a['action'] in ('turn_on', 'start'):
                return True
            if a['action'] == 'set_temperature':
                prior = st.attributes.get('temperature')
                fahrenheit = self.hass.config.units.temperature_unit == '°F'
                ceiling, delta = (71.6, 3.6) if fahrenheit else (22, 2)
                if prior is None or a['temperature'] > ceiling or a['temperature'] - float(prior) > delta:
                    return True
        return False

    def summary(self, actions):
        parts = []
        for a in actions:
            state = self.hass.states.get(a['entity_id'])
            text = f"{a['action'].replace('_', ' ')} {state.name if state else a['entity_id']}"
            for key, unit in (('temperature', 'degrees'), ('brightness_pct', '%'), ('color_temp_kelvin', 'kelvin'), ('rgb_color', 'RGB')):
                if key in a:
                    text += f" to {a[key]} {unit}"
            parts.append(text)
        return '; '.join(parts)

    async def propose(self, session, actions, context):
        if not session.authorised or not session.running or session.closed:
            return {'error': 'Use the guarded API through the Sentient with backup voice agent'}
        try:
            await self.validate(actions, context)
        except (ValueError, TypeError) as err:
            return {'error': str(err)}
        if session.pending:
            return {'error': 'Answer or cancel the pending action before requesting another'}
        actions = deepcopy(actions)
        session.entities = [a['entity_id'] for a in actions]
        if self.needs_confirmation(actions):
            session.pending = {'actions': actions, 'turn': session.turn,
                               'expires': dt_util.utcnow() + timedelta(seconds=60),
                               'before': {a['entity_id']: snapshot(self.hass.states.get(a['entity_id'])) for a in actions},
                               'summary': self.summary(actions)}
            session.question = f"Shall I {session.pending['summary']}?"
            return {'status': 'awaiting_confirmation', 'question': session.question,
                    'instruction': 'Read this question exactly. Do not claim the action happened.'}
        return await self.execute(session, actions, context)

    async def confirm(self, session, context):
        pending, session.pending = session.pending, None  # consume before awaiting; never execute twice
        if not pending or pending['turn'] >= session.turn or pending['expires'] < dt_util.utcnow():
            return {'error': 'That confirmation has expired; please ask again'}
        if not session.authorised or session.closed:
            return {'error': 'Guarded control is not active'}
        async with self._lock:
            try:
                await self.validate(pending['actions'], context)
            except (ValueError, TypeError) as err:
                return {'error': str(err)}
            for eid, before in pending['before'].items():
                if snapshot(self.hass.states.get(eid)) != before:
                    return {'error': 'A device changed while waiting; please ask again'}
            return await self._execute(session, pending['actions'], context)

    async def execute(self, session, actions, context):
        async with self._lock:
            return await self._execute(session, actions, context)

    async def _execute(self, session, actions, context):
        try:
            await self.validate(actions, context)
        except (ValueError, TypeError) as err:
            return {'error': str(err)}
        if session.undo_turn != session.turn:
            session.undo = []
            session.undo_turn = session.turn
        outcomes = []
        for a in actions:
            if session.closed:
                break
            eid = a['entity_id']
            before = snapshot(self.hass.states.get(eid))
            data = {k: v for k, v in a.items() if k not in ('entity_id', 'action')}
            try:
                async with asyncio.timeout(10):
                    await self.hass.services.async_call(eid.split('.')[0], a['action'], {'entity_id': eid, **data}, blocking=True,
                                                        context=context or Context())
                # Service completion is not physical confirmation. Allow normal state propagation briefly.
                for _ in range(10):
                    if target_reached(self.hass.states.get(eid), a):
                        break
                    await asyncio.sleep(0.1)
                state = self.hass.states.get(eid)
                verified = target_reached(state, a)
                outcomes.append({'entity_id': eid, 'status': 'verified' if verified else 'attempted'})
                if verified and eid.startswith('light.') and before['state'] in ('on', 'off') and self.options.get(CONF_UNDO):
                    previous = next((r for r in session.undo if r['entity_id'] == eid), None)
                    if previous and previous['after'] == before:
                        before = previous['before']
                    session.undo = [r for r in session.undo if r['entity_id'] != eid]
                    session.undo.append({'entity_id': eid, 'before': before, 'after': snapshot(state),
                                         'expires': dt_util.utcnow() + timedelta(minutes=5)})
            except (HomeAssistantError, TimeoutError, ValueError) as err:
                outcomes.append({'entity_id': eid, 'status': 'failed', 'reason': str(err)})
                break  # never replay a partly completed batch
        result = {'actions': outcomes, 'summary': self.summary(actions), 'note': 'Verified means the device reported the requested state.'}
        session.last_action = result
        self.timeline.add('voice', result['summary'], str(outcomes),
                          evidence='verified' if outcomes and all(o['status'] == 'verified' for o in outcomes) else 'attempted')
        return result

    async def undo(self, session, context):
        if not session.authorised or session.closed or not self.options.get(CONF_UNDO):
            return {'error': 'Undo is not enabled for guarded control'}
        async with self._lock:
            records, session.undo = session.undo, []
            if not records:
                return {'error': 'There is no recent verified light action to undo'}
            outcomes = []
            for record in records:
                eid = record['entity_id']
                state = self.hass.states.get(eid)
                if record['expires'] < dt_util.utcnow() or state is None or snapshot(state) != record['after']:
                    outcomes.append({'entity_id': eid, 'status': 'skipped', 'reason': 'expired or changed since the action'})
                    continue
                before = record['before']
                op = 'turn_on' if before['state'] == 'on' else 'turn_off'
                try:
                    await self.validate([{'entity_id': eid, 'action': op}], context)
                    data = {'entity_id': eid}
                    if op == 'turn_on':
                        attrs = before['attributes']
                        if 'brightness' in attrs:
                            data['brightness'] = attrs['brightness']
                        mode = attrs.get('color_mode')
                        key = 'color_temp_kelvin' if mode == 'color_temp' else f'{mode}_color'
                        if key in attrs:
                            data[key] = attrs[key]
                    async with asyncio.timeout(10):
                        await self.hass.services.async_call('light', op, data, blocking=True, context=context or Context())
                    outcomes.append({'entity_id': eid, 'status': 'attempted', 'reason': 'restore requested'})
                except (HomeAssistantError, TimeoutError, ValueError) as err:
                    outcomes.append({'entity_id': eid, 'status': 'failed', 'reason': str(err)})
            self.timeline.add('voice', 'Undo recent light changes', str(outcomes))
            return {'undo': outcomes}
