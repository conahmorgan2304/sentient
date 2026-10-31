"""Spoken declines must not loop back into another anything-else question."""
from unittest.mock import AsyncMock, patch

import pytest

from tests.test_voice_sessions_control import agent, request, response


@pytest.mark.parametrize('ending', [
    'no thankyou', 'No, thank you.', 'No. Thank you.', 'no   thank you',
    'No thank-you!', 'NO THANKYOU?', 'No, thanks, Nova.', 'Nova, no thankyou.',
    'No', 'Nope', 'Nah', "No, I'm good", "No I’m fine thanks",
    "No I don't need anything else", "I don't need anything else thank you",
    "That's it thanks", 'All done', 'Stop now', 'Thankyou', 'No thank you that is all', "No thanks that's enough",
    "No, I'm okay", "Thank you, that's all.",
])
async def test_decline_after_anything_else_closes_without_model_call(hass, ending):
    obj, rt = agent(hass, {'conversation_sessions': True, 'persona': 'Nova'})
    with patch.object(obj, '_async_process_legacy', AsyncMock(return_value=response('All sensors are online.'))) as model:
        first = await obj.async_process(request('How is the system?'))
        assert first.continue_conversation
        result = await obj.async_process(request(ending))
    assert model.await_count == 1
    assert result.continue_conversation is False
    assert result.response.speech['plain']['speech'] == 'Okay.'
    assert rt.sessions.describe() == []
    assert rt.sessions.memory.topics == []
    assert rt.sessions.memory.diagnostics()[0]['reason'] == 'user_ended'


@pytest.mark.parametrize('answer', ['No', 'Nope'])
async def test_negative_answer_to_real_question_remains_available_to_model(hass, answer):
    obj, rt = agent(hass, {'conversation_sessions': True})
    with patch.object(obj, '_async_process_legacy', AsyncMock(side_effect=[
        response('Can you hear the kitchen speaker?'), response('Then check its volume.')
    ])) as model:
        await obj.async_process(request('Why did the gate announcement fail?'))
        await obj.async_process(request(answer))
    assert model.await_count == 2


@pytest.mark.parametrize('text', [
    'No, the landing light', 'No thanks, check the gate instead',
    'Stop the vacuum', 'No, I meant the kitchen', 'That is all the information I have about the gate',
])
async def test_corrections_and_commands_are_not_swallowed_as_endings(hass, text):
    obj, _ = agent(hass, {'conversation_sessions': True, 'persona': 'Nova'})
    with patch.object(obj, '_async_process_legacy', AsyncMock(return_value=response('Which device?'))) as model:
        await obj.async_process(request(text))
    model.assert_awaited_once()


async def test_thankyou_closes_and_discards_pending_guarded_confirmation(hass):
    obj, rt = agent(hass, {'conversation_sessions': True, 'guarded_control': True})
    session = rt.sessions.get(request('start'))
    session.pending = {'summary': 'unlock gate', 'actions': []}
    with patch.object(obj, '_converse', AsyncMock()) as model:
        result = await obj.async_process(request('No, thankyou.'))
    model.assert_not_awaited()
    assert session.pending is None and session.closed
    assert not result.continue_conversation


async def test_late_provider_flag_cannot_reopen_a_closed_session(hass):
    obj, _ = agent(hass, {'conversation_sessions': True})
    with patch.object(obj, '_async_process_legacy', AsyncMock(return_value=response('Anything else?', True))) as model:
        await obj.async_process(request('Is the house secure?'))
        result = await obj.async_process(request('no thankyou'))
    assert model.await_count == 1
    assert not result.continue_conversation


@pytest.mark.parametrize('question', [
    'Can I help with anything else?', 'Would you like any further assistance?',
    'Do you have any more questions?', 'Is there something else you need?',
])
async def test_declines_work_with_provider_worded_followups(hass, question):
    obj, _ = agent(hass, {'conversation_sessions': True})
    with patch.object(obj, '_async_process_legacy', AsyncMock(return_value=response(question))) as model:
        await obj.async_process(request('Check system health'))
        result = await obj.async_process(request('No.'))
    assert model.await_count == 1 and not result.continue_conversation
