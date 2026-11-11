"""A busy AI service gets one more try before the backup answers."""
from unittest.mock import Mock, patch

from homeassistant.helpers import intent

from tests.test_voice_sessions_control import agent, request, response

BUSY = "Sorry, I had a problem talking to Google Generative AI: 503 Service Unavailable. This model is currently experiencing high demand."


def _error(text):
    out = response(text)
    out.response.async_set_error(intent.IntentResponseErrorCode.UNKNOWN, text)
    return out


def _agent(hass):
    obj, rt = agent(hass, {'voice_agent': 'conversation.primary'})
    rt.timeline = Mock()
    return obj


async def test_busy_ai_is_tried_once_more_and_answers(hass):
    obj = _agent(hass)
    replies = [_error(BUSY), response('It is four o clock.')]
    with patch.object(obj, '_converse', side_effect=lambda *a: replies.pop(0)) as calls, \
            patch('custom_components.sentient.conversation.RETRY_PAUSE', 0):
        result = await obj.async_process(request('What time is it?'))
    assert [c.args[0] for c in calls.call_args_list] == ['conversation.primary', 'conversation.primary']
    assert result.response.speech['plain']['speech'] == 'It is four o clock.'


async def test_still_busy_after_one_retry_goes_to_the_backup(hass):
    obj = _agent(hass)

    async def converse(agent_id, user_input):
        return _error(BUSY) if agent_id == 'conversation.primary' else response('Backup here.')

    with patch.object(obj, '_converse', side_effect=converse) as calls, patch.object(obj, 'async_write_ha_state'), \
            patch('custom_components.sentient.conversation.RETRY_PAUSE', 0):
        result = await obj.async_process(request('What time is it?'))
    assert [c.args[0] for c in calls.call_args_list] == ['conversation.primary', 'conversation.primary', obj.backup_agent]
    assert result.response.speech['plain']['speech'] == 'Backup here.'


async def test_a_raised_internal_error_is_retried_too(hass):
    obj = _agent(hass)
    calls = []

    async def converse(agent_id, user_input):
        calls.append(agent_id)
        if len(calls) == 1:
            raise RuntimeError('500 Internal Server Error. Internal error encountered.')
        return response('Fine now.')

    with patch.object(obj, '_converse', side_effect=converse), patch('custom_components.sentient.conversation.RETRY_PAUSE', 0):
        result = await obj.async_process(request('How warm is it outside?'))
    assert calls == ['conversation.primary', 'conversation.primary']
    assert result.response.speech['plain']['speech'] == 'Fine now.'


async def test_other_failures_and_commands_are_not_retried(hass):
    for text, why in (('What time is it?', 'Invalid API key'), ('Turn on the kitchen light', BUSY)):
        obj = _agent(hass)

        async def converse(agent_id, user_input, why=why):
            return _error(why) if agent_id == 'conversation.primary' else response('Backup here.')

        with patch.object(obj, '_converse', side_effect=converse) as calls, patch.object(obj, 'async_write_ha_state'), \
                patch('custom_components.sentient.conversation.RETRY_PAUSE', 0):
            await obj.async_process(request(text))
        assert [c.args[0] for c in calls.call_args_list].count('conversation.primary') == 1, text


async def test_a_reply_that_already_started_speaking_is_not_retried(hass):
    from custom_components.sentient.voice_actions import speech_attempt
    obj = _agent(hass)

    async def converse(agent_id, user_input):
        speech_attempt.get()['started'] = True
        return _error(BUSY)

    with patch.object(obj, '_converse', side_effect=converse) as calls, patch('custom_components.sentient.conversation.RETRY_PAUSE', 0):
        await obj.async_process(request('Tell the kitchen dinner is ready and what time is it?'))
    assert calls.call_count == 1
