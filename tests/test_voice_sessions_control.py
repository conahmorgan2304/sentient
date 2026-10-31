"""Session isolation, trusted confirmation turns, exact actions and cautious undo."""
import asyncio
from dataclasses import replace
from datetime import timedelta
from types import SimpleNamespace
from unittest.mock import AsyncMock, Mock, patch

import pytest
from homeassistant.components import conversation
from homeassistant.core import Context
from homeassistant.helpers import intent, llm
from homeassistant.util import dt as dt_util
from pytest_homeassistant_custom_component.common import MockConfigEntry, async_mock_service

from custom_components.sentient.sessions import Sessions, current_session, GUARDED_API
from custom_components.sentient.control import Control
from custom_components.sentient.conversation import BackedUpAgent
from custom_components.sentient.voice_api import GuardedAPI
from custom_components.sentient.const import DOMAIN


def request(text, cid='one', device='kitchen', user=None):
    return conversation.ConversationInput(text=text, context=Context(user_id=user), conversation_id=cid,
        device_id=device, satellite_id=None, language='en', agent_id='conversation.sentient')


def response(text='Done', listen=False):
    out = intent.IntentResponse(language='en')
    out.async_set_speech(text)
    return conversation.ConversationResult(response=out, continue_conversation=listen)


def active(options=None):
    sessions = Sessions(options or {})
    session = sessions.get(request('start'))
    sessions.begin(session, 'start')
    session.running = session.authorised = True
    return sessions, session


async def test_sessions_scope_and_expiry(freezer):
    sessions = Sessions({'conversation_sessions': True, 'session_seconds': 30})
    one = sessions.get(request('help me sort the heating'))
    sessions.begin(one, 'help me sort the heating')
    assert one.active
    assert sessions.get(request('upstairs?')) is one
    assert sessions.get(request('yes', device='bedroom')) is not one
    assert sessions.get(request('yes', user='someone')) is not one
    one.pending = {'summary': 'unlock'}
    freezer.tick(timedelta(seconds=31))
    assert sessions.get(request('yes')) is not one
    assert one.closed and one.pending is None


async def test_control_does_not_accept_model_confirmation(hass):
    _, session = active()
    hass.states.async_set('lock.front', 'locked')
    service = async_mock_service(hass, 'lock', 'unlock')
    control = Control(hass, {}, Mock())
    with patch('custom_components.sentient.control.async_should_expose', return_value=True):
        result = await control.propose(session, [{'entity_id': 'lock.front', 'action': 'unlock', 'confirmed': True}], Context())
        assert result.get('error') and not service
        result = await control.propose(session, [{'entity_id': 'lock.front', 'action': 'unlock'}], Context())
        assert result['status'] == 'awaiting_confirmation' and not service
        assert (await control.confirm(session, Context())).get('error') and not service


@pytest.mark.parametrize('change', ['expired', 'device_changed', 'permission_removed'])
async def test_pending_action_is_revalidated(hass, freezer, change):
    _, session = active()
    hass.states.async_set('lock.front', 'locked')
    service = async_mock_service(hass, 'lock', 'unlock')
    control = Control(hass, {}, Mock())
    with patch('custom_components.sentient.control.async_should_expose', return_value=True) as expose:
        await control.propose(session, [{'entity_id': 'lock.front', 'action': 'unlock'}], Context())
        session.turn += 1
        if change == 'expired':
            freezer.tick(timedelta(seconds=61))
        elif change == 'device_changed':
            hass.states.async_set('lock.front', 'unlocked')
        else:
            expose.return_value = False
        assert (await control.confirm(session, Context())).get('error')
    assert not service and session.pending is None


async def test_exact_pending_batch_executes_once(hass):
    _, session = active()
    hass.states.async_set('lock.front', 'locked')
    calls = []
    async def unlock(call):
        calls.append(call)
        hass.states.async_set('lock.front', 'unlocked')
    hass.services.async_register('lock', 'unlock', unlock)
    control = Control(hass, {}, Mock())
    action = {'entity_id': 'lock.front', 'action': 'unlock'}
    with patch('custom_components.sentient.control.async_should_expose', return_value=True):
        await control.propose(session, [action], Context())
        action['entity_id'] = 'lock.other'
        session.turn += 1
        result = await control.confirm(session, Context())
        assert result['actions'] == [{'entity_id': 'lock.front', 'status': 'verified'}]
        assert (await control.confirm(session, Context())).get('error')
    assert len(calls) == 1 and calls[0].data['entity_id'] == 'lock.front'


@pytest.mark.parametrize('entity,op', [('scene.movie', 'turn_on'), ('script.goodnight', 'turn_on'), ('alarm_control_panel.home', 'alarm_disarm')])
async def test_unrestricted_actions_are_not_available(hass, entity, op):
    _, session = active()
    hass.states.async_set(entity, 'on')
    control = Control(hass, {}, Mock())
    assert (await control.propose(session, [{'entity_id': entity, 'action': op}], Context())).get('error')


async def test_undo_restores_light_settings_but_skips_external_changes(hass):
    _, session = active()
    control = Control(hass, {'voice_undo': True}, Mock())
    hass.states.async_set('light.lamp', 'on', {'brightness': 100, 'color_mode': 'rgb', 'rgb_color': [1, 2, 3]})
    calls = []
    async def light(call):
        calls.append(call)
        attrs = {'brightness': round(call.data.get('brightness_pct', 0) * 2.55) if 'brightness_pct' in call.data else call.data.get('brightness'),
                 'color_mode': 'rgb', 'rgb_color': call.data.get('rgb_color', [1, 2, 3])}
        hass.states.async_set('light.lamp', 'on', attrs)
    hass.services.async_register('light', 'turn_on', light)
    with patch('custom_components.sentient.control.async_should_expose', return_value=True):
        result = await control.propose(session, [{'entity_id': 'light.lamp', 'action': 'turn_on', 'brightness_pct': 80, 'rgb_color': [5, 6, 7]}], Context())
        assert result['actions'][0]['status'] == 'verified' and session.undo
        await control.undo(session, Context())
        assert calls[-1].data['brightness'] == 100 and calls[-1].data['rgb_color'] == [1, 2, 3]
        await control.propose(session, [{'entity_id': 'light.lamp', 'action': 'turn_on', 'brightness_pct': 70}], Context())
        hass.states.async_set('light.lamp', 'off')
        count = len(calls)
        result = await control.undo(session, Context())
        assert result['undo'][0]['status'] == 'skipped' and len(calls) == count


async def test_service_success_without_state_change_is_attempted(hass):
    _, session = active()
    hass.states.async_set('light.lamp', 'off')
    async_mock_service(hass, 'light', 'turn_on')
    with patch('custom_components.sentient.control.async_should_expose', return_value=True):
        result = await Control(hass, {'voice_undo': True}, Mock()).propose(session, [{'entity_id': 'light.lamp', 'action': 'turn_on'}], Context())
    assert result['actions'][0]['status'] == 'attempted' and not session.undo


def agent(hass, options):
    sessions = Sessions(options)
    rt = SimpleNamespace(options=options, sessions=sessions, control=Control(hass, options, Mock()), ai=Mock())
    entry = SimpleNamespace(entry_id='sentient', title='Sentient', runtime_data=rt)
    obj = BackedUpAgent(entry)
    obj.hass = hass
    obj.entity_id = 'conversation.sentient'
    return obj, rt


async def test_agent_conversation_mode_and_explicit_end(hass):
    obj, rt = agent(hass, {'conversation_sessions': True})
    with patch.object(obj, '_async_process_legacy', AsyncMock(return_value=response('Of course'))) as process:
        assert (await obj.async_process(request('help me sort the heating'))).continue_conversation
        assert (await obj.async_process(request('and upstairs?'))).continue_conversation
        assert 'Current Sentient session' in process.call_args.args[0].extra_system_prompt
        assert not (await obj.async_process(request("that's all"))).continue_conversation
        assert process.await_count == 2
    assert not rt.sessions.describe()


async def test_simple_commands_and_questions_preserve_behaviour(hass):
    obj, rt = agent(hass, {'conversation_sessions': True})
    with patch.object(obj, '_async_process_legacy', AsyncMock(side_effect=[response('Done'), response('Which room?', True)])):
        assert not (await obj.async_process(request('turn the light off'))).continue_conversation
        assert (await obj.async_process(request('make it warmer'))).continue_conversation
        assert rt.sessions.describe()[0]['state'] == 'listening'


async def test_agent_confirms_directly_without_second_model_call(hass):
    obj, rt = agent(hass, {'guarded_control': True, 'voice_agent': 'conversation.ai'})
    hass.states.async_set('lock.front', 'locked')
    calls = []
    async def unlock(call):
        calls.append(call)
        hass.states.async_set('lock.front', 'unlocked')
    hass.services.async_register('lock', 'unlock', unlock)
    async def main(_agent, user_input):
        await rt.control.propose(current_session.get(), [{'entity_id': 'lock.front', 'action': 'unlock'}], user_input.context)
        current_session.get().question = 'Would you like something else?'
        return response('Unlocked!')  # unsafe model text must be overridden
    with patch.object(obj, '_guarded_configuration', return_value=True), patch.object(obj, '_converse', side_effect=main) as model, patch('custom_components.sentient.control.async_should_expose', return_value=True):
        result = await obj.async_process(request('unlock the front door'))
        assert result.continue_conversation and result.response.speech['plain']['speech'].startswith('Shall I')
        assert 'unlock' in result.response.speech['plain']['speech']
        assert not calls
        result = await obj.async_process(request('yes'))
        assert len(calls) == 1 and model.await_count == 1
        assert 'reported' in result.response.speech['plain']['speech']


async def test_guarded_misconfiguration_and_failure_never_fall_back(hass):
    obj, rt = agent(hass, {'guarded_control': True, 'voice_agent': 'conversation.ai'})
    with patch.object(obj, '_converse', AsyncMock(side_effect=RuntimeError('offline'))) as main, patch.object(obj, '_async_process_legacy', AsyncMock()) as backup:
        result = await obj.async_process(request('turn on the light'))
        assert 'only Sentient guarded' in result.response.speech['plain']['speech']
        main.assert_not_awaited()
        with patch.object(obj, '_guarded_configuration', return_value=True):
            result = await obj.async_process(request('turn on the light'))
        assert "haven't retried" in result.response.speech['plain']['speech']
        backup.assert_not_awaited()


async def test_supported_agent_requires_only_guarded_api(hass):
    from homeassistant.helpers import entity_registry as er
    entry = MockConfigEntry(domain='openai_conversation', options={'llm_hass_api': [GUARDED_API]})
    entry.add_to_hass(hass)
    registered = er.async_get(hass).async_get_or_create('conversation', 'openai_conversation', 'test', config_entry=entry)
    obj, _ = agent(hass, {'guarded_control': True, 'voice_agent': registered.entity_id})
    assert obj._guarded_configuration()
    hass.config_entries.async_update_entry(entry, options={'llm_hass_api': [GUARDED_API, 'assist']})
    assert not obj._guarded_configuration()


async def test_guarded_api_excludes_bypasses_and_rejects_direct_use(hass):
    from tests.test_phase3 import _setup
    await _setup(hass, guarded_control=True)
    ctx = llm.LLMContext(platform='test', context=Context(), language='en', assistant='conversation', device_id=None)
    api = await GuardedAPI(hass).async_get_api_instance(ctx)
    names = {t.name for t in api.tools}
    assert {'sentient_control', 'sentient_devices', 'sentient_session', 'sentient_incidents'} <= names
    assert not names & {'sentient_scene', 'sentient_run_routine', 'sentient_make_rule', 'sentient_party'}
    result = await api.async_call_tool(llm.ToolInput(tool_name='sentient_control', tool_args={'actions': [{'entity_id': 'lock.front', 'action': 'unlock'}]}))
    assert result.get('error')


async def test_undo_combines_light_changes_within_one_turn(hass):
    _, session = active()
    control = Control(hass, {'voice_undo': True}, Mock())
    for eid in ['light.one', 'light.two']:
        hass.states.async_set(eid, 'off')
    calls = []
    async def change(call):
        calls.append(call)
        hass.states.async_set(call.data['entity_id'], 'on' if call.service == 'turn_on' else 'off')
    hass.services.async_register('light', 'turn_on', change)
    hass.services.async_register('light', 'turn_off', change)
    with patch('custom_components.sentient.control.async_should_expose', return_value=True):
        for eid in ['light.one', 'light.two']:
            await control.propose(session, [{'entity_id': eid, 'action': 'turn_on'}], Context())
        assert len(session.undo) == 2
        session.turn += 1
        await control.undo(session, Context())
    assert hass.states.get('light.one').state == hass.states.get('light.two').state == 'off'


async def test_correction_cancels_pending_action_and_other_speaker_cannot_confirm(hass):
    obj, rt = agent(hass, {'guarded_control': True, 'voice_agent': 'conversation.ai'})
    hass.states.async_set('lock.front', 'locked')
    calls = async_mock_service(hass, 'lock', 'unlock')
    async def main(_agent, user_input):
        if 'unlock' in user_input.text:
            await rt.control.propose(current_session.get(), [{'entity_id': 'lock.front', 'action': 'unlock'}], user_input.context)
        return response('Please clarify')
    with patch.object(obj, '_guarded_configuration', return_value=True), patch.object(obj, '_converse', side_effect=main), patch('custom_components.sentient.control.async_should_expose', return_value=True):
        await obj.async_process(request('unlock the front door'))
        await obj.async_process(request('yes', device='bedroom'))
        assert not calls
        await obj.async_process(request('no'))
        await obj.async_process(request('yes'))
        assert not calls


async def test_user_permissions_are_checked_independently_of_exposure(hass):
    _, session = active()
    hass.states.async_set('light.lamp', 'off')
    calls = async_mock_service(hass, 'light', 'turn_on')
    user = SimpleNamespace(is_active=True, permissions=Mock())
    user.permissions.check_entity.return_value = False
    with patch('custom_components.sentient.control.async_should_expose', return_value=True), patch.object(hass.auth, 'async_get_user', AsyncMock(return_value=user)):
        result = await Control(hass, {}, Mock()).propose(session, [{'entity_id': 'light.lamp', 'action': 'turn_on'}], Context(user_id='limited'))
    assert result.get('error') and not calls


async def test_temperature_and_cover_confirmation(hass):
    control = Control(hass, {}, Mock())
    hass.states.async_set('climate.lounge', 'heat', {'temperature': 19})
    hass.states.async_set('cover.gate', 'closed')
    assert not control.needs_confirmation([{'entity_id': 'climate.lounge', 'action': 'set_temperature', 'temperature': 20}])
    assert control.needs_confirmation([{'entity_id': 'climate.lounge', 'action': 'set_temperature', 'temperature': 23}])
    assert control.needs_confirmation([{'entity_id': 'cover.gate', 'action': 'open_cover'}])
    assert not control.needs_confirmation([{'entity_id': 'cover.gate', 'action': 'stop_cover'}])


async def test_model_cannot_end_an_active_discussion(hass):
    from tests.test_phase3 import _setup
    from custom_components.sentient.voice_api import SessionTool
    entry = await _setup(hass, conversation_sessions=True)
    session = entry.runtime_data.sessions.get(request('start'))
    session.running = True
    session.active = True
    token = current_session.set(session)
    ctx = llm.LLMContext(platform='test', context=Context(), language='en', assistant='conversation', device_id=None)
    try:
        result = await SessionTool().async_call(hass, llm.ToolInput(tool_name='sentient_session', tool_args={'active': False}), ctx)
    finally:
        current_session.reset(token)
    assert session.active and result['state'] == 'discussion_idle'


async def test_guarded_configuration_uses_selected_subentry(hass):
    from types import MappingProxyType
    from homeassistant.config_entries import ConfigSubentry
    from homeassistant.helpers import entity_registry as er

    entry = MockConfigEntry(domain='openai_conversation', options={'llm_hass_api': ['assist']})
    entry.add_to_hass(hass)
    sub = ConfigSubentry(data=MappingProxyType({'llm_hass_api': [GUARDED_API]}),
                         subentry_type='conversation', title='Guarded', unique_id=None)
    hass.config_entries.async_add_subentry(entry, sub)
    registered = er.async_get(hass).async_get_or_create(
        'conversation', 'openai_conversation', 'guarded-sub', config_entry=entry,
        config_subentry_id=sub.subentry_id)
    obj, _ = agent(hass, {'guarded_control': True, 'voice_agent': registered.entity_id})
    assert obj._guarded_configuration()
    hass.config_entries.async_update_subentry(entry, sub, data={'llm_hass_api': [GUARDED_API, 'assist']})
    assert not obj._guarded_configuration()


async def test_undo_cancels_an_unrelated_pending_confirmation(hass):
    obj, rt = agent(hass, {'guarded_control': True, 'voice_undo': True, 'voice_agent': 'conversation.ai'})
    session = rt.sessions.get(request('start'))
    session.pending = {'summary': 'unlock front', 'turn': 0,
                       'expires': dt_util.utcnow() + timedelta(seconds=60)}
    with patch.object(obj, '_guarded_configuration', return_value=True):
        await obj.async_process(request('undo that'))
    assert session.pending is None


async def test_feature_settings_apply_without_restart_and_clear_pending_actions(hass):
    from tests.test_phase3 import _setup
    from custom_components.sentient import _async_options_updated

    entry = await _setup(hass)
    rt = entry.runtime_data
    session = rt.sessions.get(request('start'))
    session.pending = {'summary': 'unlock'}
    hass.config_entries.async_update_entry(entry, options={
        **entry.options, 'conversation_sessions': True, 'voice_undo': True,
        'security_incidents': True, 'session_seconds': 120})
    await _async_options_updated(hass, entry)
    assert rt.sessions.seconds == 120
    assert rt.sessions.options['conversation_sessions']
    assert rt.control.options['voice_undo']
    assert rt.incidents.enabled
    assert session.closed and session.pending is None
