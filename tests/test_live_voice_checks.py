"""Live checks observe real agent results; delivery receipts describe each target."""
import asyncio
from types import SimpleNamespace
from unittest.mock import patch

import pytest
from homeassistant.core import Context
from homeassistant.exceptions import HomeAssistantError
from homeassistant.helpers import intent

from custom_components.sentient.voice_checks import VoiceChecks
from custom_components.sentient.speech import SpeechRouter
from custom_components.sentient.selftest import SelfTest


def turn(text, listening, device='one', reply='Anything else?'):
    response = intent.IntentResponse(language='en')
    response.async_set_speech(reply)
    return (SimpleNamespace(text=text, device_id=device, satellite_id=None,
                            context=Context(user_id='owner'), agent_id='sentient', language='en'),
            SimpleNamespace(response=response, continue_conversation=listening))


@pytest.mark.parametrize('closing,passed', [(False, True), (True, False)])
async def test_live_check_requires_same_owner_and_explicit_ending(hass, closing, passed):
    checks = VoiceChecks(hass)
    run = checks.start()
    checks.observe(*turn('Turn on the light', False))
    assert checks.status()['steps'] == []
    checks.observe(*turn('How is the system?', True))
    with pytest.raises(ValueError):
        checks.confirm_heard(run['id'], True)
    checks.observe(*turn('No thankyou', False, device='someone_else'))
    assert len(checks.status()['steps']) == 1
    checks.observe(*turn('No thankyou', closing, reply='You are welcome.'))
    assert checks.status()['status'] == ('passed_agent_checks' if passed else 'failed_agent_checks')
    assert checks.status()['heard_replies'] is None
    assert checks.confirm_heard(run['id'], False)['heard_replies'] is False
    checks.start()
    with pytest.raises(ValueError):
        checks.confirm_heard(run['id'], True)
    checks.stop()
    assert checks.status() is None


async def test_live_check_expires_and_returns_copies(hass, freezer):
    checks = VoiceChecks(hass)
    run = checks.start()
    run['steps'].append('outside mutation')
    assert not checks.status()['steps']
    freezer.tick(301)
    checks.observe(*turn('How is the system?', True))
    assert checks.status()['status'] == 'expired'
    assert not checks.status()['steps']


async def test_empty_reply_cannot_pass_live_check(hass):
    checks = VoiceChecks(hass)
    checks.start()
    checks.observe(*turn('How is the system?', True, reply=''))
    checks.observe(*turn('No thankyou', False, reply=''))
    assert checks.status()['status'] == 'failed_agent_checks'


async def test_partial_delivery_retry_skips_successful_speaker(hass):
    speakers = ['assist_satellite.first', 'assist_satellite.second']
    router = SpeechRouter(hass, {'speakers': speakers, 'quiet_start': '00:00', 'quiet_end': '00:00'})
    calls = []
    fail = True
    async def announce(call):
        calls.extend(call.data['entity_id'])
        if call.data['entity_id'] == [speakers[1]] and fail:
            raise HomeAssistantError('Disconnected')
    hass.services.async_register('assist_satellite', 'announce', announce)
    result = await router.async_say('The door is open', targets=speakers)
    assert result.reason == 'partial' and result.speakers == speakers[:1]
    assert [r['status'] for r in result.deliveries] == ['accepted', 'failed']
    fail = False
    result = await router.async_say('The door is open', targets=speakers)
    assert result.speakers == speakers[1:]
    assert calls == [*speakers, speakers[1]]
    assert not router.failures
    assert (await router.async_say('The door is open', targets=speakers)).reason == 'duplicate'
    await router.async_say('The door is open', targets=speakers, source='critical', priority='urgent')
    assert calls[-2:] == speakers


async def test_delivery_timeout_does_not_hide_other_destinations(hass):
    router = SpeechRouter(hass, {})
    async def announce(call):
        if call.data['entity_id'] == ['assist_satellite.slow']:
            await asyncio.Event().wait()
    hass.services.async_register('assist_satellite', 'announce', announce)
    with patch('custom_components.sentient.speech.DELIVERY_TIMEOUT', 0.01):
        results = await router._speak('Hello', ['assist_satellite.ready', 'assist_satellite.slow', 'media_player.unconfigured'])
    assert [r['status'] for r in results] == ['accepted', 'timed_out', 'unroutable']
    assert 'unknown' in results[1]['error']
    assert len(router.failures) == 2
    assert len(router.deliveries) == 1


@pytest.mark.parametrize('value', ['nonsense', None, 'nan', 'inf'])
async def test_selftest_skips_unreliable_weather_and_absent_openings(hass, value):
    hass.states.async_set('weather.home', 'sunny', {'temperature': value})
    runner = SelfTest(hass, 'test', {}, lambda: None)
    assert [q.key for q in runner.questions()] == ['time']


@pytest.mark.parametrize('state', ['unknown', 'unavailable', 'arming', 'pending', 'triggered'])
async def test_selftest_does_not_expect_disarmed_from_unsettled_alarm(hass, state):
    hass.states.async_set('alarm_control_panel.home', state)
    hass.states.async_set('binary_sensor.door', 'unavailable', {'device_class': 'door'})
    runner = SelfTest(hass, 'test', {}, lambda: None)
    assert [q.key for q in runner.questions()] == ['time']


async def test_real_agent_hook_closes_locally_and_guest_mode_does_not_record(hass):
    from unittest.mock import AsyncMock
    from tests.test_voice_sessions_control import agent, request, response
    obj, rt = agent(hass, {'conversation_sessions': True})
    rt.voice_checks = VoiceChecks(hass)
    rt.voice_checks.start()
    with patch.object(obj, '_async_process_legacy', AsyncMock(return_value=response('All services are available.'))) as model:
        first = await obj.async_process(request('How is the system?'))
        last = await obj.async_process(request('No thankyou', cid=first.conversation_id))
    assert model.await_count == 1
    assert first.continue_conversation and not last.continue_conversation
    assert rt.voice_checks.status()['status'] == 'passed_agent_checks'
    rt.voice_checks.start()
    rt.speech = SimpleNamespace(guest_mode=True)
    with patch.object(obj, '_async_process_legacy', AsyncMock(return_value=response('Ready.'))):
        await obj.async_process(request('How is the system?', cid='guest'))
    assert not rt.voice_checks.status()['steps']


async def test_live_check_endpoint_disabled_guest_and_manual_confirmation(hass, hass_ws_client):
    from tests.test_websocket import _setup
    from custom_components.sentient.sensor import MindSensor
    entry = await _setup(hass)
    ws = await hass_ws_client(hass)
    await ws.send_json({'id': 1, 'type': 'sentient/voice_check', 'action': 'start'})
    assert not (await ws.receive_json())['success']
    entry.runtime_data.options['conversation_sessions'] = True
    await ws.send_json({'id': 2, 'type': 'sentient/voice_check', 'action': 'start'})
    result = await ws.receive_json()
    assert result['success'] and result['result']['status'] == 'waiting_for_question'
    await ws.send_json({'id': 3, 'type': 'sentient/voice_check', 'action': 'heard', 'run_id': result['result']['id']})
    assert not (await ws.receive_json())['success']
    entry.runtime_data.speech.guest_mode = True
    await ws.send_json({'id': 4, 'type': 'sentient/voice_check'})
    assert (await ws.receive_json())['error']['code'] == 'private'
    sensor = MindSensor(entry)
    assert sensor.extra_state_attributes['voice_check'] is None
    assert sensor.extra_state_attributes['speech_deliveries'] == []
    assert {'voice_check', 'speech_deliveries'} <= sensor._unrecorded_attributes


async def test_selftest_rejects_not_armed_for_armed_alarm(hass):
    hass.states.async_set('alarm_control_panel.home', 'armed_home')
    runner = SelfTest(hass, 'test', {}, lambda: None)
    question = next(q for q in runner.questions() if q.key == 'alarm')
    assert not question.check('The alarm is not armed.')[0]
    assert question.check('The alarm is armed.')[0]


async def test_disabled_voice_check_does_not_start_or_observe(hass):
    checks = VoiceChecks(hass, {'live_voice_check': False})
    with pytest.raises(ValueError, match='disabled'):
        checks.start()
    checks.observe(*turn('How is the system?', True))
    assert checks.status() is None


async def test_disabled_delivery_history_keeps_delivery_and_failure_detection(hass):
    from pytest_homeassistant_custom_component.common import async_mock_service
    router = SpeechRouter(hass, {'speech_delivery_history': False})
    async_mock_service(hass, 'assist_satellite', 'announce')
    result = await router._speak('Hello', ['assist_satellite.ready', 'media_player.unconfigured'])
    assert [r['status'] for r in result] == ['accepted', 'unroutable']
    assert not router.deliveries
    assert 'media_player.unconfigured' in router.failures


async def test_diagnostic_toggles_roundtrip_clear_and_reenable(hass, hass_ws_client):
    from tests.test_websocket import _setup
    from custom_components.sentient.sensor import MindSensor
    entry = await _setup(hass)
    rt = entry.runtime_data
    rt.voice_checks.start()
    rt.speech.deliveries.append({'message': 'old'})
    ws = await hass_ws_client(hass)
    await ws.send_json({'id': 1, 'type': 'sentient/settings'})
    settings = (await ws.receive_json())['result']
    voice = next(g for g in settings['groups'] if g['id'] == 'voice')
    assert {'live_voice_check', 'speech_delivery_history'} <= {f['name'] for f in voice['schema']}
    await ws.send_json({'id': 2, 'type': 'sentient/settings/save', 'group': 'voice',
                       'data': {'live_voice_check': False, 'speech_delivery_history': False, 'conversation_sessions': True}})
    assert (await ws.receive_json())['success']
    await hass.async_block_till_done()
    assert rt.voice_checks.status() is None and not rt.speech.deliveries
    attrs = MindSensor(entry).extra_state_attributes
    assert attrs['live_voice_check_enabled'] is False
    assert attrs['voice_check'] is None and attrs['speech_deliveries'] == []
    await ws.send_json({'id': 3, 'type': 'sentient/voice_check', 'action': 'start'})
    assert (await ws.receive_json())['error']['code'] == 'disabled'
    await ws.send_json({'id': 4, 'type': 'sentient/settings/save', 'group': 'voice',
                       'data': {'live_voice_check': True, 'speech_delivery_history': True, 'conversation_sessions': True}})
    assert (await ws.receive_json())['success']
    await hass.async_block_till_done()
    await ws.send_json({'id': 5, 'type': 'sentient/voice_check', 'action': 'start'})
    assert (await ws.receive_json())['success']
    assert rt.speech.options['speech_delivery_history'] is True
    assert MindSensor(entry).extra_state_attributes['live_voice_check_enabled'] is True
