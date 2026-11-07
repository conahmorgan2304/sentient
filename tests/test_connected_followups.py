"""Connected turns preserve meaning, not stale readings or action permission."""
from unittest.mock import AsyncMock, patch

import pytest

from custom_components.sentient.followups import classify, CONF_CONTEXTUAL_FOLLOWUPS
from custom_components.sentient.sessions import current_session
from custom_components.sentient.config_flow import _speech_schema
from custom_components.sentient.websocket import GROUPS
from tests.test_voice_sessions_control import agent, request, response
from tests.test_phone_answers import _setup

OPTIONS = {'conversation_sessions': True, CONF_CONTEXTUAL_FOLLOWUPS: True}


@pytest.mark.parametrize('text,kind', [('Why?', 'explanation'), ('Why is that?', 'explanation'),
    ('What about upstairs?', 'comparison'), ('And the kitchen?', 'comparison'),
    ('Make that 20 degrees', 'adjustment'), ('No, I meant the kitchen', 'correction'),
    ('What time is it?', None), ('Turn on the lamp', None), ('No thankyou', None)])
def test_followup_detection(text, kind):
    assert classify(text) == kind


async def test_why_preserves_subject_and_refreshes_readings(hass):
    obj, rt = agent(hass, OPTIONS)
    hass.states.async_set('climate.room', 'heat', {'temperature': 19})
    prompts = []
    async def model(turn):
        prompts.append(turn.extra_system_prompt)
        current_session.get().entities = ['climate.room']
        return response('The heating is on.')
    with patch.object(obj, '_async_process_legacy', side_effect=model), patch('custom_components.sentient.followups.async_should_expose', return_value=True):
        await obj.async_process(request('How is the heating?'))
        hass.states.async_set('climate.room', 'heat', {'temperature': 21})
        await obj.async_process(request('Why?'))
    session = rt.sessions.get(request(''))
    assert session.goal == 'How is the heating?'
    assert session.follow_up_context['current_readings'][0]['attributes']['temperature'] == 21
    assert 'never permission to act' in prompts[-1]


async def test_comparison_clears_previous_target_but_retains_subject(hass):
    obj, rt = agent(hass, OPTIONS)
    session = rt.sessions.get(request(''))
    session.goal = session.topic = 'How is the heating?'
    session.entities = ['climate.lounge']
    hass.states.async_set('climate.lounge', 'heat')
    with patch.object(obj, '_async_process_legacy', AsyncMock(return_value=response('The kitchen is warm.'))), patch('custom_components.sentient.followups.async_should_expose', return_value=True):
        await obj.async_process(request('What about the kitchen?'))
    assert session.goal == 'How is the heating?' and session.entities == []
    assert session.follow_up_context['kind'] == 'comparison'


@pytest.mark.parametrize('entities', [[], ['climate.a', 'climate.b']])
async def test_ambiguous_adjustment_asks_once_and_preserves_request(hass, entities):
    obj, rt = agent(hass, OPTIONS)
    session = rt.sessions.get(request(''))
    session.goal = 'Check the heating'
    session.entities = entities
    for eid in entities:
        hass.states.async_set(eid, 'heat')
    with patch.object(obj, '_async_process_legacy', AsyncMock(return_value=response('Which kitchen thermostat?'))) as model, patch('custom_components.sentient.followups.async_should_expose', return_value=True):
        out = await obj.async_process(request('Make that 20 degrees'))
        assert out.response.speech['plain']['speech'] == 'Which device should I change?'
        assert out.continue_conversation and model.await_count == 0 and session.pending is None
        await obj.async_process(request('The kitchen'))
        assert model.await_count == 1
        assert 'Make that 20 degrees' in model.call_args.args[0].extra_system_prompt
        assert session.follow_up_context['kind'] == 'clarification'


async def test_correction_retains_previous_outcome_without_executing(hass):
    obj, rt = agent(hass, OPTIONS)
    session = rt.sessions.get(request(''))
    session.goal = 'Switch off the lamp'
    session.entities = ['light.lamp']
    session.last_action = {'summary': 'turn off lamp', 'actions': [{'entity_id': 'light.lamp', 'status': 'verified'}]}
    hass.states.async_set('light.lamp', 'off')
    with patch.object(obj, '_async_process_legacy', AsyncMock(return_value=response('Which kitchen light?'))), patch('custom_components.sentient.followups.async_should_expose', return_value=True):
        await obj.async_process(request('No, I meant the kitchen'))
    assert session.correction['previous_action']['actions'][0]['status'] == 'verified'
    assert session.entities == [] and session.pending is None


async def test_unexposed_device_is_not_in_followup_context(hass):
    obj, rt = agent(hass, OPTIONS)
    session = rt.sessions.get(request(''))
    session.goal = 'Check heating'
    session.entities = ['climate.private']
    hass.states.async_set('climate.private', 'heat')
    with patch.object(obj, '_async_process_legacy', AsyncMock(return_value=response('Unknown.'))) as model, patch('custom_components.sentient.followups.async_should_expose', return_value=False):
        await obj.async_process(request('Why?'))
    assert session.follow_up_context['current_readings'] == []
    assert 'climate.private' not in model.call_args.args[0].extra_system_prompt


@pytest.mark.parametrize('options', [{'conversation_sessions': True}, {'contextual_followups': True}])
async def test_disabled_or_missing_sessions_does_not_enrich(hass, options):
    obj, rt = agent(hass, options)
    with patch.object(obj, '_async_process_legacy', AsyncMock(return_value=response('Okay.'))) as model:
        await obj.async_process(request('Why?'))
    assert 'follow_up_context' not in (model.call_args.args[0].extra_system_prompt or '')


async def test_settings_disable_clears_context(hass):
    entry = await _setup(hass, **OPTIONS)
    session = entry.runtime_data.sessions.get(request(''))
    session.goal = 'Heating'
    entry.runtime_data.sessions.begin(session, 'Why?')
    assert session.follow_up_context
    hass.config_entries.async_update_entry(entry, options={**OPTIONS, CONF_CONTEXTUAL_FOLLOWUPS: False})
    await hass.async_block_till_done()
    assert session.follow_up_context is None and not entry.runtime_data.sessions.items


def test_setting_is_available_in_both_settings_surfaces():
    assert CONF_CONTEXTUAL_FOLLOWUPS in {str(key) for key in _speech_schema({}).schema}
    assert CONF_CONTEXTUAL_FOLLOWUPS in next(row[4] for row in GROUPS if row[0] == 'voice')


async def test_ending_discards_followup_and_next_session_asks_subject(hass):
    obj, rt = agent(hass, OPTIONS)
    session = rt.sessions.get(request(''))
    session.goal = 'Heating'
    with patch.object(obj, '_async_process_legacy', AsyncMock()) as model:
        await obj.async_process(request('No thankyou'))
        out = await obj.async_process(request('Why?'))
    assert out.response.speech['plain']['speech'] == 'What would you like me to explain?'
    assert model.await_count == 0 and session.follow_up_context is None


async def test_different_device_does_not_inherit_followup_subject(hass):
    obj, rt = agent(hass, OPTIONS)
    rt.sessions.get(request('')).goal = 'Private heating discussion'
    with patch.object(obj, '_async_process_legacy', AsyncMock()) as model:
        out = await obj.async_process(request('Why?', device='bedroom'))
    assert out.response.speech['plain']['speech'] == 'What would you like me to explain?'
    model.assert_not_awaited()


async def test_single_target_adjustment_does_not_grant_action_permission(hass):
    obj, rt = agent(hass, OPTIONS)
    session = rt.sessions.get(request(''))
    session.goal = 'Heating target'
    session.entities = ['climate.room']
    hass.states.async_set('climate.room', 'heat', {'temperature': 19})
    with patch.object(obj, '_async_process_legacy', AsyncMock(return_value=response('Which temperature unit?'))) as model, patch('custom_components.sentient.followups.async_should_expose', return_value=True):
        out = await obj.async_process(request('Make that 20'))
    assert model.await_count == 1 and out.continue_conversation
    assert session.entities == ['climate.room']
    assert not session.authorised and not session.pending


async def test_expiry_discards_subject(hass, freezer):
    from datetime import timedelta

    obj, rt = agent(hass, {**OPTIONS, 'session_seconds': 15})
    rt.sessions.get(request('')).goal = 'Heating'
    freezer.tick(timedelta(seconds=16))
    with patch.object(obj, '_async_process_legacy', AsyncMock()) as model:
        out = await obj.async_process(request('Why?'))
    assert out.continue_conversation and model.await_count == 0
    assert 'What would you like me to explain?' in out.response.speech['plain']['speech']


async def test_new_subject_clears_old_device_targets(hass):
    obj, rt = agent(hass, OPTIONS)
    session = rt.sessions.get(request(''))
    session.goal = 'Heating'
    session.entities = ['climate.room']
    with patch.object(obj, '_async_process_legacy', AsyncMock(return_value=response('It is sunny.'))):
        await obj.async_process(request('What is the weather?'))
    assert session.goal == 'What is the weather?' and session.entities == []
    assert session.follow_up_context is None


async def test_cancel_during_permission_lookup_does_not_recreate_context(hass):
    from types import SimpleNamespace

    obj, rt = agent(hass, OPTIONS)
    session = rt.sessions.get(request('', user='alice'))
    session.goal = 'Heating'
    async def lookup(user_id):
        rt.sessions.close(session)
        return SimpleNamespace(is_active=True)
    with patch.object(hass.auth, 'async_get_user', side_effect=lookup), patch.object(obj, '_async_process_legacy', AsyncMock()) as model:
        out = await obj.async_process(request('Why?', user='alice'))
    assert not out.continue_conversation and session.follow_up_context is None
    model.assert_not_awaited()


async def test_denied_read_permission_removes_device(hass):
    from types import SimpleNamespace
    from unittest.mock import Mock

    obj, rt = agent(hass, OPTIONS)
    session = rt.sessions.get(request('', user='alice'))
    session.goal = 'Heating'
    session.entities = ['climate.private']
    hass.states.async_set('climate.private', 'heat')
    user = SimpleNamespace(is_active=True, permissions=Mock(check_entity=Mock(return_value=False)))
    with patch.object(hass.auth, 'async_get_user', AsyncMock(return_value=user)), patch.object(obj, '_async_process_legacy', AsyncMock(return_value=response('Unknown'))) as model:
        await obj.async_process(request('Why?', user='alice'))
    assert 'climate.private' not in model.call_args.args[0].extra_system_prompt


async def test_yes_does_not_resolve_ambiguous_device(hass):
    obj, rt = agent(hass, OPTIONS)
    session = rt.sessions.get(request(''))
    session.goal = 'Heating'
    with patch.object(obj, '_async_process_legacy', AsyncMock(return_value=response('Which thermostat?'))) as model:
        await obj.async_process(request('Make that 20 degrees'))
        result = await obj.async_process(request('Yes'))
        assert model.await_count == 0
        assert result.response.speech['plain']['speech'] == 'Which device should I change?'
        await obj.async_process(request('The kitchen'))
        assert model.await_count == 1
        assert session.follow_up_context['previous_request'] == 'Make that 20 degrees'
