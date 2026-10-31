"""Optional guarded API: replaces Assist + Sentient for a dedicated conversation agent."""
from __future__ import annotations

import voluptuous as vol
from homeassistant.helpers import llm
from homeassistant.components.homeassistant.exposed_entities import async_should_expose

from .llm_api import SentientAPI, _runtime, _Tool
from .sessions import current_session, CONF_SESSIONS, GUARDED_API
from .control import ACTIONS
from .evidence import describe_state

# Explicit whitelist: no arbitrary routine, scene, rule, offer or heating execution.
SAFE_TOOLS = {'sentient_remember', 'sentient_correction', 'sentient_recall', 'sentient_forget',
              'sentient_house_check', 'sentient_conversations', 'sentient_why', 'sentient_security',
              'sentient_energy', 'sentient_timeline', 'sentient_visitors', 'sentient_watches',
              'sentient_routines', 'sentient_heating', 'sentient_night_watch', 'sentient_weekly_report',
              'sentient_remind', 'sentient_watch', 'sentient_cancel_watch', 'sentient_follow_up',
              'sentient_look', 'sentient_what_happened', 'sentient_briefing', 'sentient_announce',
              'sentient_quiet', 'sentient_shush'}


class SessionTool(_Tool):
    name = 'sentient_session'
    description = ('Track the current goal, topic, selected entities, missing detail and task state. '
                   'Use active true for information, security, health or system discussions, or an explicit '
                   'request to talk. Do not open discussions for device commands. A completed task does not '
                   'close a discussion: only the user ends it. For a single command, ask a question only '
                   'when clarification or confirmation is necessary; otherwise finish without a question. '
                   'On a correction, update the existing goal and entities; do not repeat a completed action.')
    parameters = vol.Schema({vol.Optional('active'): bool, vol.Optional('topic'): str,
                             vol.Optional('entities'): [str], vol.Optional('question'): str,
                             vol.Optional('goal'): str, vol.Optional('missing_detail'): str,
                             vol.Optional('task_state'): vol.In(['in_progress', 'waiting', 'completed'])})

    async def async_call(self, hass, tool_input, llm_context):
        rt, session = _runtime(hass), current_session.get()
        if rt is None or session is None or not session.running or session.closed:
            return {'error': 'Use the Sentient with backup agent for sessions'}
        if not rt.options.get(CONF_SESSIONS):
            return {'error': 'Conversation sessions are disabled'}
        a = tool_input.tool_args
        if a.get('active') and not session.command_turn:
            session.active = True
            session.mode = 'discussion'
        for key in ('goal', 'missing_detail'):
            if key in a:
                setattr(session, key, a[key][:200])
        if 'task_state' in a:
            session.task_state = a['task_state']
        if 'topic' in a:
            session.topic = a['topic'][:200]
        if 'entities' in a:
            session.entities = [e for e in a['entities'][:20] if hass.states.get(e)]
        if 'question' in a:
            session.question = a['question'][:300]
        return session.describe()


class ControlTool(_Tool):
    name = 'sentient_control'
    description = ('Control explicit exposed devices. Use entity IDs from sentient_devices. '
                   'Never substitute another entity for an ambiguous request. For a scene-like request, '
                   'pass explicit light settings. Sensitive actions return a confirmation question: '
                   'read it exactly and wait. This tool cannot confirm its own request. '
                   'When status is attempted, do not claim the device changed.')
    parameters = vol.Schema({vol.Required('actions'): [vol.Schema({
        vol.Required('entity_id'): str, vol.Required('action'): vol.In(sorted(set().union(*ACTIONS.values()))),
        vol.Optional('brightness_pct'): vol.All(vol.Coerce(float), vol.Range(min=1, max=100)),
        vol.Optional('color_temp_kelvin'): vol.Coerce(int),
        vol.Optional('rgb_color'): [vol.All(int, vol.Range(min=0, max=255))],
        vol.Optional('temperature'): vol.Coerce(float),
    })]})

    async def async_call(self, hass, tool_input, llm_context):
        rt, session = _runtime(hass), current_session.get()
        if rt is None or session is None:
            return {'error': 'Use guarded control through the Sentient with backup agent'}
        return await rt.control.propose(session, tool_input.tool_args['actions'], llm_context.context)


class DevicesTool(_Tool):
    name = 'sentient_devices'
    description = ('Find exposed devices by name, entity ID or area. Returns IDs, supported guarded actions, '
                   'reported states, timestamps and limits. If more than one matches, ask which device. '
                   'Unknown or unavailable readings cannot establish the current state.')
    parameters = vol.Schema({vol.Optional('query', default=''): str})

    async def async_call(self, hass, tool_input, llm_context):
        from .presence import area_of
        from homeassistant.helpers import area_registry as ar
        from homeassistant.auth.permissions.const import POLICY_READ

        query = tool_input.tool_args.get('query', '').lower()
        user = await hass.auth.async_get_user(llm_context.context.user_id) if llm_context.context and llm_context.context.user_id else None
        if llm_context.context and llm_context.context.user_id and (user is None or not user.is_active):
            return {'error': 'User not authorised'}
        out = []
        for state in hass.states.async_all():
            if not async_should_expose(hass, 'conversation', state.entity_id):
                continue
            if user and not user.permissions.check_entity(state.entity_id, POLICY_READ):
                continue
            area = ar.async_get(hass).async_get_area(area_of(hass, state.entity_id) or '')
            name = area.name if area else ''
            if query and query not in f'{state.entity_id} {state.name} {name}'.lower():
                continue
            row = describe_state(hass, state.entity_id)
            row.update(area=name, actions=sorted(ACTIONS.get(state.domain, [])),
                       attributes={k: state.attributes[k] for k in ('temperature', 'min_temp', 'max_temp', 'device_class',
                       'brightness', 'color_mode', 'color_temp_kelvin', 'rgb_color', 'min_color_temp_kelvin', 'max_color_temp_kelvin') if k in state.attributes})
            out.append(row)
            if len(out) >= 60:
                return {'devices': out, 'truncated': True, 'note': 'Narrow the query to find more devices'}
        return {'devices': out}


class IncidentsTool(_Tool):
    name = 'sentient_incidents'
    description = ('Read grouped security evidence: contact changes, person detections, alerts and clips. '
                   'Nearby timestamps are a possible link, not proof of the same person. Never invent missing links.')
    parameters = vol.Schema({vol.Optional('query', default=''): str})

    async def async_call(self, hass, tool_input, llm_context):
        rt = _runtime(hass)
        return {'incidents': rt.incidents.recent(tool_input.tool_args.get('query', ''))} if rt else {'error': 'Not loaded'}


class GuardedAPI(SentientAPI):
    def __init__(self, hass):
        llm.API.__init__(self, hass=hass, id=GUARDED_API, name='Sentient guarded')

    async def async_get_api_instance(self, llm_context):
        instance = await super().async_get_api_instance(llm_context)
        instance.api = self
        instance.tools = [t for t in instance.tools if t.name in SAFE_TOOLS or isinstance(t, (SessionTool, IncidentsTool))]
        instance.tools += [DevicesTool(), ControlTool()]
        instance.api_prompt += ('\nGuarded control: use sentient_devices to resolve exact exposed entities, then '
                                'sentient_control. No scripts, scenes, alarms or arbitrary service execution. '
                                'Ask before guessing a device. Pending actions need a separate user reply; '
                                'you cannot confirm them yourself. Undo is handled directly when the user says undo that. '
                                'A reported target state is verification of the report, not independent physical proof.')
        return instance
