"""A slow speaker must not become an AI crash or replay through backup."""
import asyncio
from types import SimpleNamespace
from unittest.mock import AsyncMock, patch
import pytest
from homeassistant.exceptions import HomeAssistantError
from custom_components.sentient.llm_api import AnnounceTool
from custom_components.sentient.speech import SpeechRouter
from custom_components.sentient.delivery import delivered
from custom_components.sentient.voice_actions import speech_attempt
from tests.test_voice_sessions_control import agent, request


@pytest.mark.parametrize('timeout', [True, False])
async def test_tool_returns_delivery_problem(hass, timeout):
    async def say(*args, **kwargs):
        if timeout:
            await asyncio.Event().wait()
        raise HomeAssistantError('Echo unavailable')
    rt = SimpleNamespace(speech=SimpleNamespace(async_say=say))
    with patch('custom_components.sentient.llm_api._runtime', return_value=rt), patch('custom_components.sentient.llm_api.ANNOUNCE_TIMEOUT', .01):
        result = await AnnounceTool().async_call(hass, SimpleNamespace(tool_args={'message': 'Test'}), None)
    assert result['reason'] == ('delivery_timeout' if timeout else 'delivery_failed')
    assert result['spoken'] is (None if timeout else False)


async def test_speech_attempt_not_replayed_after_model_failure(hass):
    obj, rt = agent(hass, {'voice_agent': 'conversation.primary'})
    async def model(*args):
        speech_attempt.get()['started'] = True
        raise TimeoutError()
    with patch.object(obj, '_converse', side_effect=model) as converse:
        result = await obj.async_process(request('Test the kitchen Echo'))
    assert converse.call_count == 1
    assert "haven't retried" in result.response.speech['plain']['speech']
    assert speech_attempt.get() is None


async def test_cancelled_volume_preparation_restores_original_level(hass):
    calls = []
    entered = asyncio.Event()
    async def set_volume(hass, control, level):
        calls.append(level)
        if len(calls) == 1:
            entered.set()
            await asyncio.Event().wait()
    async def speak():
        async with delivered(hass, ['media_player.echo'], .8, None):
            pytest.fail('Cancelled preparation must not start playback')
    with patch('custom_components.sentient.delivery.volume_control', return_value=('media_player', 'media_player.echo')), patch('custom_components.sentient.delivery._level', return_value=.3), patch('custom_components.sentient.delivery._set', side_effect=set_volume):
        task = asyncio.create_task(speak())
        await entered.wait()
        task.cancel()
        with pytest.raises(asyncio.CancelledError):
            await task
    assert calls == [.8, .3]


async def test_cancelled_delivery_preserves_accepted_targets(hass):
    router = SpeechRouter(hass, {})
    slow_started = asyncio.Event()
    async def announce(call):
        if call.data['entity_id'] == ['assist_satellite.slow']:
            slow_started.set()
            await asyncio.Event().wait()
    hass.services.async_register('assist_satellite', 'announce', announce)
    task = asyncio.create_task(router._speak('Test', ['assist_satellite.ready', 'assist_satellite.slow']))
    await slow_started.wait()
    await asyncio.sleep(0)
    task.cancel()
    with pytest.raises(asyncio.CancelledError):
        await task
    assert ('test', 'assist_satellite.ready') in router._delivered_recent
    assert [r['status'] for r in router.deliveries[0]['results']] == ['accepted', 'cancelled']


async def test_backup_agent_cannot_wait_indefinitely(hass):
    from unittest.mock import Mock
    obj, rt = agent(hass, {'voice_agent': 'conversation.primary'})
    rt.timeline = Mock()
    async def converse(agent_id, user_input):
        if agent_id == 'conversation.primary':
            raise RuntimeError('offline')
        await asyncio.Event().wait()
    with patch.object(obj, '_converse', side_effect=converse) as calls, patch.object(obj, 'async_write_ha_state'), patch('custom_components.sentient.conversation.BACKUP_TIMEOUT', .01):
        result = await obj.async_process(request('What time is it?'))
    assert calls.call_count == 2
    assert "isn't answering" in result.response.speech['plain']['speech']


async def test_normal_speaker_error_returns_to_model_without_backup(hass):
    obj, rt = agent(hass, {'voice_agent': 'conversation.primary'})
    from tests.test_voice_sessions_control import response
    rt.speech = SimpleNamespace(async_say=AsyncMock(side_effect=HomeAssistantError('Echo offline')))
    async def converse(*args):
        with patch('custom_components.sentient.llm_api._runtime', return_value=rt):
            result = await AnnounceTool().async_call(hass, SimpleNamespace(tool_args={'message': 'Test'}), None)
        assert result['reason'] == 'delivery_failed'
        return response('The Echo is offline.')
    with patch.object(obj, '_converse', side_effect=converse) as calls:
        result = await obj.async_process(request('Test the kitchen Echo'))
    assert calls.call_count == 1
    assert 'offline' in result.response.speech['plain']['speech']
