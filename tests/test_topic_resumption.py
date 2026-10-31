"""Resume discussion context without reviving permissions, and inspect the last exchange."""
from datetime import timedelta
from types import SimpleNamespace
from unittest.mock import AsyncMock, patch

import pytest

from custom_components.sentient.sessions import current_session
from tests.test_voice_sessions_control import agent, request, response


async def discuss(obj, text='Why was the gate announcement quiet?', reply='Which speaker did you expect?'):
    async def model(user_input):
        current_session.get().entities = ['binary_sensor.gate']
        return response(reply)
    with patch.object(obj, '_async_process_legacy', side_effect=model):
        return await obj.async_process(request(text))


async def test_resume_after_timeout_refreshes_readings_and_rotates_history(hass, freezer):
    obj, rt = agent(hass, {'conversation_sessions': True})
    hass.states.async_set('binary_sensor.gate', 'on')
    await discuss(obj)
    prior = rt.sessions.get(request('next'))
    prior.pending = {'summary': 'unlock', 'actions': [{'entity_id': 'lock.gate', 'action': 'unlock'}]}
    prior.undo = [{'entity_id': 'light.lamp'}]
    prior.last_action = {'summary': 'old action'}
    freezer.tick(timedelta(seconds=61))
    hass.states.async_set('binary_sensor.gate', 'off')
    with patch.object(obj, '_async_process_legacy', AsyncMock()) as model, patch('homeassistant.components.homeassistant.exposed_entities.async_should_expose', return_value=True):
        resumed = await obj.async_process(request('Back to the gate issue', cid='new'))
    model.assert_not_awaited()
    assert resumed.conversation_id not in ('one', 'new')
    assert resumed.continue_conversation
    session = rt.sessions.get(request('next', cid=resumed.conversation_id))
    assert session.resume_context['current_readings'][0]['state'] == 'off'
    assert 'Which speaker' in session.resume_context['previous_question']
    assert session.pending is session.last_action is session.correction is None
    assert session.undo == []
    with patch.object(obj, '_async_process_legacy', AsyncMock()) as model:
        result = await obj.async_process(request('yes', cid=resumed.conversation_id))
    model.assert_not_awaited()
    assert 'expired' in result.response.speech['plain']['speech']


async def test_resume_ambiguity_requires_choice(hass):
    obj, rt = agent(hass, {'conversation_sessions': True})
    await discuss(obj, 'Why was the gate announcement quiet?')
    await discuss(obj, 'Is the gate sensor working?', 'Which sensor reading did you mean?')
    with patch.object(obj, '_async_process_legacy', AsyncMock()) as model, patch('homeassistant.components.homeassistant.exposed_entities.async_should_expose', return_value=True):
        result = await obj.async_process(request('Back to the gate issue', cid='new'))
        assert 'several discussions' in result.response.speech['plain']['speech']
        assert result.continue_conversation
        result = await obj.async_process(request('second', cid='new'))
    model.assert_not_awaited()
    session = rt.sessions.get(request('next', cid=result.conversation_id))
    assert 'announcement' in session.topic


@pytest.mark.parametrize('device,user', [('bedroom', None), ('kitchen', 'different_user')])
async def test_topics_never_cross_owner_scope(hass, device, user):
    obj, rt = agent(hass, {'conversation_sessions': True})
    await discuss(obj)
    result = await obj.async_process(request('Back to the gate issue', cid='new', device=device, user=user))
    assert "couldn't find" in result.response.speech['plain']['speech']
    assert not rt.sessions.get(request('next', cid='new', device=device, user=user)).resume_context


async def test_endings_and_retention_remove_resumable_topics(hass, freezer):
    obj, rt = agent(hass, {'conversation_sessions': True})
    await discuss(obj)
    await obj.async_process(request("that's all"))
    assert rt.sessions.memory.topics == []
    await discuss(obj)
    freezer.tick(timedelta(hours=25))
    result = await obj.async_process(request('Back to the gate issue', cid='new'))
    assert "couldn't find" in result.response.speech['plain']['speech']
    assert not rt.sessions.memory.topics


async def test_revoked_exposure_not_used_for_current_readings(hass):
    obj, rt = agent(hass, {'conversation_sessions': True})
    hass.states.async_set('binary_sensor.gate', 'on')
    await discuss(obj)
    with patch('homeassistant.components.homeassistant.exposed_entities.async_should_expose', return_value=False):
        result = await obj.async_process(request('Back to the gate issue', cid='new'))
    session = rt.sessions.get(request('next', cid=result.conversation_id))
    assert not session.entities and not session.resume_context['current_readings']


async def test_guest_mode_hides_resume_and_skips_diagnostics(hass):
    obj, rt = agent(hass, {'conversation_sessions': True})
    await discuss(obj)
    count = len(rt.sessions.memory.traces)
    rt.speech = SimpleNamespace(guest_mode=True)
    result = await obj.async_process(request('Back to the gate issue', cid='new'))
    assert 'stay private' in result.response.speech['plain']['speech']
    assert len(rt.sessions.memory.traces) == count


async def test_diagnostics_capture_actual_listening_and_clear_with_settings(hass):
    obj, rt = agent(hass, {'conversation_sessions': True})
    with patch.object(obj, '_async_process_legacy', AsyncMock(return_value=response('Done.', True))):
        await obj.async_process(request('Turn off the light'))
    row = rt.sessions.memory.diagnostics()[0]
    assert row['request'] == 'Turn off the light'
    assert row['reply'] == 'Done.' and row['listen'] is False
    assert row['reason'] == 'completed_single_request'
    assert row['goal'] == 'Turn off the light'
    assert not rt.sessions.memory.topics
    rt.sessions.stop()
    assert rt.sessions.memory.diagnostics() == []


async def test_resume_device_command_is_not_topic_request(hass):
    obj, _ = agent(hass, {'conversation_sessions': True})
    with patch.object(obj, '_async_process_legacy', AsyncMock(return_value=response('Resumed.'))) as model:
        result = await obj.async_process(request('Resume the vacuum'))
    model.assert_awaited_once()
    assert not result.continue_conversation


async def test_topic_and_diagnostic_memory_are_bounded(hass):
    obj, rt = agent(hass, {'conversation_sessions': True})
    for n in range(45):
        await discuss(obj, f'What is happening in zone {n}?')
    assert len(rt.sessions.memory.topics) == 8
    assert len(rt.sessions.memory.traces) == 40
    assert len(rt.sessions.memory.diagnostics()) == 10


async def test_completed_resumed_discussion_is_removed(hass):
    obj, rt = agent(hass, {'conversation_sessions': True})
    await discuss(obj)
    with patch('homeassistant.components.homeassistant.exposed_entities.async_should_expose', return_value=True):
        result = await obj.async_process(request('Back to the gate issue', cid='new'))
    await obj.async_process(request('enough', cid=result.conversation_id))
    assert not rt.sessions.memory.topics


async def test_pending_confirmation_is_never_a_topic_bookmark(hass):
    obj, rt = agent(hass, {'conversation_sessions': True})
    async def model(user_input):
        current_session.get().pending = {'summary': 'unlock the gate', 'expires': None}
        return response('Shall I unlock?')
    with patch.object(obj, '_async_process_legacy', side_effect=model):
        await obj.async_process(request('Is the gate secure?'))
    assert not rt.sessions.memory.topics


async def test_diagnostic_attributes_hidden_for_guests_and_excluded_from_recorder(hass):
    from tests.test_phase3 import _setup
    from custom_components.sentient.sensor import MindSensor
    entry = await _setup(hass, conversation_sessions=True)
    sensor = MindSensor(entry)
    assert {'voice_sessions', 'conversation_diagnostics'} <= sensor._unrecorded_attributes
    entry.runtime_data.sessions.memory.traces.append({'time': '2099-01-01T00:00:00+00:00', 'request': 'private'})
    entry.runtime_data.speech.guest_mode = True
    assert sensor.extra_state_attributes['conversation_diagnostics'] == []
