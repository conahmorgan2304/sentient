"""A busy AI service gets one more try before the backup answers."""
from unittest.mock import Mock, patch

import pytest
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


async def test_an_empty_earlier_reply_is_mended_and_asked_again(hass):
    """19 Nov 18:31: "Uh, no thank you." failed with "content parts are required" and went to the backup."""
    from homeassistant.components import conversation
    from homeassistant.components.conversation.chat_log import DATA_CHAT_LOGS

    obj = _agent(hass)
    req = request('Uh, no thank you.')
    log = Mock()
    log.content = [conversation.SystemContent(content="prompt"), conversation.UserContent(content="check the gate"),
                   conversation.AssistantContent(agent_id="conversation.primary", content=None),
                   conversation.AssistantContent(agent_id="conversation.primary", content="It's clear.")]
    hass.data[DATA_CHAT_LOGS] = {req.conversation_id: log}
    replies = [_error("Error: <class 'ValueError'> content parts are required."), response('Okay.')]
    with patch.object(obj, '_converse', side_effect=lambda *a: replies.pop(0)) as calls:
        result = await obj.async_process(req)
    assert [c.args[0] for c in calls.call_args_list] == ['conversation.primary', 'conversation.primary']
    assert result.response.speech['plain']['speech'] == 'Okay.'
    assert log.content[2].content == " " and log.content[3].content == "It's clear."


def test_nothing_to_mend_means_no_retry(hass):
    from custom_components.sentient.conversation import repair_chat_log

    assert repair_chat_log(hass, None) is False
    assert repair_chat_log(hass, "not-a-conversation") is False


async def test_an_ai_backup_that_fails_too_hands_on_to_home_assistants_own(hass):
    obj, rt = agent(hass, {'voice_agent': 'conversation.primary', 'backup_agent': 'conversation.second_ai'})
    rt.timeline = Mock()

    async def converse(agent_id, user_input):
        if agent_id == 'conversation.home_assistant':
            return response('Turned off the kitchen light.')
        raise RuntimeError('no internet')

    with patch.object(obj, '_converse', side_effect=converse) as calls, patch.object(obj, 'async_write_ha_state'):
        result = await obj.async_process(request('Turn the kitchen light off'))
    assert [c.args[0] for c in calls.call_args_list][-2:] == ['conversation.second_ai', 'conversation.home_assistant']
    assert result.response.speech['plain']['speech'] == 'Turned off the kitchen light.'


async def test_provider_and_backup_work_without_chat_log_subscriptions(hass, monkeypatch):
    from homeassistant.components.conversation import chat_log

    monkeypatch.delattr(chat_log, "async_subscribe_chat_logs", raising=False)
    obj = _agent(hass)
    with patch.object(obj, "_converse", side_effect=[_error("Invalid API key"), response("Backup here.")]) as calls, \
            patch.object(obj, "async_write_ha_state"):
        result = await obj.async_process(request("Tell me a joke"))
    assert [call.args[0] for call in calls.call_args_list] == ["conversation.primary", obj.backup_agent]
    assert result.response.speech["plain"]["speech"] == "Backup here."


@pytest.mark.parametrize("silence", ["   ", None])
async def test_a_silent_primary_uses_the_backup_for_an_information_request(hass, silence):
    obj = _agent(hass)
    silent = response("")
    silent.response.speech["plain"]["speech"] = silence
    with patch.object(obj, "_converse", side_effect=[silent, response("Backup here.")]) as calls, \
            patch.object(obj, "async_write_ha_state"):
        result = await obj.async_process(request("Tell me a joke"))
    assert [call.args[0] for call in calls.call_args_list] == ["conversation.primary", obj.backup_agent]
    assert result.response.speech["plain"]["speech"] == "Backup here."


async def test_a_silent_primary_does_not_replay_a_device_command(hass):
    obj = _agent(hass)
    with patch.object(obj, "_converse", return_value=response("")) as provider:
        result = await obj.async_process(request("Turn the light off"))
    provider.assert_awaited_once()
    assert result.response.response_type == intent.IntentResponseType.ERROR
    assert "haven't sent it to the backup" in result.response.speech["plain"]["speech"]


async def test_two_silent_agents_return_a_spoken_error(hass):
    from custom_components.sentient.conversation import NOT_ANSWERING

    obj = _agent(hass)
    with patch.object(obj, "_converse", side_effect=[response(""), response(" ")]) as provider, \
            patch.object(obj, "async_write_ha_state"):
        result = await obj.async_process(request("Tell me a joke"))
    assert provider.await_count == 2
    assert result.response.response_type == intent.IntentResponseType.ERROR
    assert result.response.speech["plain"]["speech"] == NOT_ANSWERING


async def test_a_spoken_handoff_keeps_the_authoritative_reply(hass):
    from custom_components.sentient.focus import turn_flags

    obj = _agent(hass)

    async def converse(_agent, _input):
        turn_flags.get()["hand_off"] = "I started reading it on the tablet."
        return response("")

    with patch.object(obj, "_converse", side_effect=converse) as provider:
        result = await obj.async_process(request("Read me the book"))
    provider.assert_awaited_once()
    assert result.response.speech["plain"]["speech"] == "I started reading it on the tablet."


async def test_a_reported_device_action_survives_the_provider_failing_afterwards(hass, monkeypatch):
    from homeassistant.components.conversation import chat_log

    obj = _agent(hass)
    callbacks = []

    def subscribe(_hass, callback):
        callbacks.append(callback)
        return lambda: callbacks.remove(callback)

    async def converse(_agent, _input):
        for observer in callbacks:
            observer("voice", "content_added", {"content": {
                "role": "tool_result", "tool_name": "HassTurnOn", "tool_result": {
                    "response_type": "action_done", "data": {"success": [{"name": "Heater"}]},
                },
            }})
        raise RuntimeError("503 Service Unavailable")

    monkeypatch.setattr(chat_log, "async_subscribe_chat_logs", subscribe, raising=False)
    with patch.object(obj, "_converse", side_effect=converse) as provider, \
            patch.object(obj, "async_write_ha_state"), patch("custom_components.sentient.conversation.RETRY_PAUSE", 0):
        result = await obj.async_process(request("I’m cold"))
    provider.assert_awaited_once()
    assert result.response.response_type == intent.IntentResponseType.ERROR
    assert "haven't sent it to the backup" in result.response.speech["plain"]["speech"]
