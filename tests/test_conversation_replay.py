"""Replay realistic turns without an extra model call or live microphone."""
from unittest.mock import AsyncMock, patch

import pytest
from homeassistant.core import Context
from homeassistant.helpers import intent, llm

from tests.test_voice_sessions_control import agent, request, response
from custom_components.sentient.sessions import current_session
from custom_components.sentient.voice_api import SessionTool


@pytest.mark.parametrize('text', [
    'Turn off the kitchen light', 'Could you please turn on the lamp?',
    'Set the heating to 20', 'Lock the door', 'Start the vacuum',
])
async def test_completed_command_ignores_provider_keep_listening(hass, text):
    obj, rt = agent(hass, {'conversation_sessions': True})
    with patch.object(obj, '_async_process_legacy', AsyncMock(return_value=response('Done.', True))):
        result = await obj.async_process(request(text))
    assert not result.continue_conversation
    state = rt.sessions.describe()[0]
    assert state['task_state'] == 'completed'
    assert state['listen_reason'] == 'completed_single_request'
    assert state['mode'] == 'command'


@pytest.mark.parametrize('reply', ['Which light?', 'Which room?”', 'Shall I unlock the front door?'])
async def test_command_reopens_only_for_spoken_question(hass, reply):
    obj, rt = agent(hass, {'conversation_sessions': True})
    with patch.object(obj, '_async_process_legacy', AsyncMock(return_value=response(reply, False))):
        result = await obj.async_process(request('Turn the light off'))
    assert result.continue_conversation
    assert rt.sessions.describe()[0]['listen_reason'] == 'reply_ends_in_question'


@pytest.mark.parametrize('text', [
    'What is the temperature?', 'Is the house secure?', 'Check system health',
    'Are any batteries running low?', 'Tell me about the security alerts',
    'How is the system running?', 'Can you explain why the bedroom is cold?',
])
async def test_information_discussion_adds_followup(hass, text):
    obj, rt = agent(hass, {'conversation_sessions': True})
    with patch.object(obj, '_async_process_legacy', AsyncMock(return_value=response('Here is the report.'))):
        result = await obj.async_process(request(text))
    assert result.continue_conversation
    assert result.response.speech['plain']['speech'].endswith("Is there anything else you'd like to know?")
    assert rt.sessions.describe()[0]['mode'] == 'discussion'


@pytest.mark.parametrize('ending', [
    "that's all", 'that is all', 'enough', "that's enough", 'enough now',
    'stop listening', 'stop', 'no thanks', "no, that's all", 'nothing else',
    "we’re done", "that's all for now, thank you", 'that is enough please',
])
async def test_discussion_replay_stays_open_until_user_finishes(hass, ending):
    obj, rt = agent(hass, {'conversation_sessions': True})
    turns = [
        ('Is the house secure?', 'The gate is open.'),
        ('What about the back door?', 'It is reporting closed.'),
        ('And the system?', 'One battery is low.'),
        ('Turn off the hall light', 'The light is reporting off.'),
    ]
    with patch.object(obj, '_async_process_legacy', AsyncMock(side_effect=[response(reply) for _, reply in turns])) as model:
        for text, _ in turns:
            assert (await obj.async_process(request(text))).continue_conversation
        assert not (await obj.async_process(request(ending))).continue_conversation
        assert model.await_count == len(turns)
    assert rt.sessions.describe() == []


async def test_clarification_replay_keeps_goal_then_finishes(hass):
    obj, rt = agent(hass, {'conversation_sessions': True})
    prompts = []
    async def model(user_input):
        prompts.append(user_input.extra_system_prompt)
        return response('Which light?' if len(prompts) == 1 else 'The landing light is off.', True)
    with patch.object(obj, '_async_process_legacy', side_effect=model):
        assert (await obj.async_process(request('Turn off the light'))).continue_conversation
        assert (await obj.async_process(request('The landing'))).continue_conversation is False
    assert 'Which light?' in prompts[1]
    assert 'Turn off the light' in prompts[1]
    assert rt.sessions.describe()[0]['goal'] == 'Turn off the light'


async def test_no_duplicate_followup_or_false_question_from_metadata(hass):
    obj, rt = agent(hass, {'conversation_sessions': True})
    with patch.object(obj, '_async_process_legacy', AsyncMock(return_value=response('Which sensor is offline?'))):
        result = await obj.async_process(request('Help me figure out system health'))
    assert result.response.speech['plain']['speech'] == 'Which sensor is offline?'
    obj, rt = agent(hass, {'conversation_sessions': True})
    async def model(user_input):
        current_session.get().question = 'Invented question?'
        return response('Done.')
    with patch.object(obj, '_async_process_legacy', side_effect=model):
        assert not (await obj.async_process(request('Switch off the lamp'))).continue_conversation


async def test_error_and_empty_reply_do_not_create_listening_loop(hass):
    obj, rt = agent(hass, {'conversation_sessions': True})
    error = response()
    error.response.async_set_error(intent.IntentResponseErrorCode.UNKNOWN, 'Offline')
    with patch.object(obj, '_async_process_legacy', AsyncMock(side_effect=[error, response('')])):
        for text in ['Check system health', 'What is wrong?']:
            assert not (await obj.async_process(request(text))).continue_conversation
    assert rt.sessions.describe()[0]['listen_reason'] == 'error_or_empty_reply'


async def test_ssml_question_controls_listening(hass):
    obj, _ = agent(hass, {'conversation_sessions': True})
    reply = response()
    reply.response.speech.clear()
    reply.response.async_set_speech('<speak>Which room?</speak>', speech_type='ssml')
    with patch.object(obj, '_async_process_legacy', AsyncMock(return_value=reply)):
        assert (await obj.async_process(request('Turn off the light'))).continue_conversation


async def test_tool_tracks_corrections_and_cannot_open_command_discussion(hass):
    from tests.test_phase3 import _setup
    entry = await _setup(hass, conversation_sessions=True)
    sessions = entry.runtime_data.sessions
    session = sessions.get(request('Turn off the kitchen light'))
    sessions.begin(session, 'Turn off the kitchen light')
    session.running = True
    hass.states.async_set('light.landing', 'on')
    token = current_session.set(session)
    ctx = llm.LLMContext(platform='test', context=Context(), language='en', assistant='conversation', device_id=None)
    try:
        await SessionTool().async_call(hass, llm.ToolInput(tool_name='sentient_session', tool_args={
            'active': True, 'goal': 'Turn off the landing light', 'entities': ['light.landing'],
            'missing_detail': '', 'task_state': 'completed',
        }), ctx)
    finally:
        current_session.reset(token)
    assert not session.active
    assert session.goal == 'Turn off the landing light'
    assert session.entities == ['light.landing']
    assert session.task_state == 'completed'


async def test_discussion_confirmation_is_separate_and_keeps_followup(hass):
    from pytest_homeassistant_custom_component.common import async_mock_service
    obj, rt = agent(hass, {'conversation_sessions': True, 'guarded_control': True, 'voice_agent': 'conversation.ai'})
    hass.states.async_set('lock.front', 'locked')
    calls = async_mock_service(hass, 'lock', 'unlock')
    async def model(_agent, user_input):
        if user_input.text.startswith('Unlock'):
            await rt.control.propose(current_session.get(), [{'entity_id': 'lock.front', 'action': 'unlock'}], user_input.context)
        return response('The front door is locked.')
    with patch.object(obj, '_guarded_configuration', return_value=True), patch.object(obj, '_converse', side_effect=model) as ai, patch('custom_components.sentient.control.async_should_expose', return_value=True):
        assert (await obj.async_process(request('Is the house secure?'))).continue_conversation
        pending = await obj.async_process(request('Unlock the front door'))
        assert pending.response.speech['plain']['speech'] == 'Shall I unlock front?'
        assert not calls
        result = await obj.async_process(request('yes'))
        assert len(calls) == 1 and ai.await_count == 2
        assert result.continue_conversation
        assert "anything else" in result.response.speech['plain']['speech']
        assert not (await obj.async_process(request('enough'))).continue_conversation


async def test_user_ending_clears_pending_even_if_agent_configuration_changed(hass):
    obj, rt = agent(hass, {'conversation_sessions': True, 'guarded_control': True})
    session = rt.sessions.get(request('start'))
    session.pending = {'summary': 'unlock'}
    with patch.object(obj, '_guarded_configuration', return_value=False):
        result = await obj.async_process(request('enough'))
    assert not result.continue_conversation
    assert session.closed and session.pending is None


async def test_device_stop_is_not_conversation_ending(hass):
    obj, _ = agent(hass, {'conversation_sessions': True})
    with patch.object(obj, '_async_process_legacy', AsyncMock(return_value=response('Stop requested.'))) as model:
        result = await obj.async_process(request('Stop the vacuum'))
    model.assert_awaited_once()
    assert not result.continue_conversation
