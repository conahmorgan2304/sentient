"""Regression cases found during the cross-component reliability review."""
import asyncio
from datetime import timedelta
from types import SimpleNamespace
from unittest.mock import AsyncMock, Mock, patch
import pytest
from homeassistant.core import State
from homeassistant.util import dt as dt_util
from custom_components.sentient.const import DOMAIN


@pytest.mark.parametrize('value', ['nan', 'NaN', 'inf', '-inf', 'unavailable', 'not a number'])
async def test_invalid_sensor_cannot_satisfy_numeric_watch_or_offer(hass, value):
    from custom_components.sentient.watches import Watch, WatchManager
    from custom_components.sentient.offers import Offers
    watch = Watch('temperature rises', 'state', 'sensor.temperature', (dt_util.utcnow() + timedelta(hours=1)).isoformat(), above=20)
    assert not WatchManager._state_met(watch, State('sensor.temperature', value))
    hass.states.async_set('sensor.temperature', value)
    rt = SimpleNamespace(speech=SimpleNamespace(guest_mode=False, hushed=lambda: False, quiet_now=lambda: False),
                         feedback=SimpleNamespace(offers_blocked=lambda *args: False))
    offers = Offers(hass, Mock(), lambda: rt)
    assert not offers.ready({'id': 'test', 'below_entity': 'sensor.temperature', 'below': 18})


@pytest.mark.parametrize('value', ['nan', 'inf', '-inf', '-1', '101'])
async def test_invalid_battery_cannot_crash_health_or_hide_valid_problem(hass, value):
    from tests.test_reasons import _setup
    entry = await _setup(hass)
    hass.states.async_set('sensor.bad_battery', value, {'device_class': 'battery'})
    hass.states.async_set('sensor.low_battery', '5', {'device_class': 'battery'})
    rows = entry.runtime_data.health.async_check()
    assert any(r.get('entity_id') == 'sensor.low_battery' for r in rows)
    assert not any(r.get('entity_id') == 'sensor.bad_battery' for r in rows)


@pytest.mark.parametrize('mode', ['disabled', 'ignored'])
async def test_safety_repeat_stops_after_setting_changes(hass, mode):
    from custom_components.sentient.critical import CriticalAlerts
    rt = SimpleNamespace(timeline=Mock())
    options = {}
    engine = CriticalAlerts(hass, options, lambda: rt)
    hass.states.async_set('binary_sensor.smoke', 'on', {'device_class': 'smoke'})
    with patch.object(engine, '_say', new=AsyncMock()) as say:
        engine._raise('binary_sensor.smoke', 2)
        await hass.async_block_till_done()
        if mode == 'disabled': options['critical_alerts'] = False
        else: options['critical_ignore'] = ['binary_sensor.smoke']
        engine._raise('binary_sensor.smoke', 1)
        await hass.async_block_till_done()
    assert say.await_count == 1
    engine.async_stop()


async def test_exposure_failure_is_not_permission(hass):
    from custom_components.sentient.llm_api import _exposed
    with patch('homeassistant.components.homeassistant.exposed_entities.async_should_expose', side_effect=RuntimeError('not ready')):
        assert _exposed(hass, 'sensor.private') is False


@pytest.mark.parametrize('failure', ['error', 'timeout'])
async def test_speaker_doctor_continues_after_bad_destination(hass, hass_ws_client, failure):
    from tests.test_reasons import _setup
    entry = await _setup(hass)
    rt = entry.runtime_data
    rt.options['speakers'] = ['assist_satellite.bad', 'assist_satellite.missing', 'assist_satellite.good']
    hass.states.async_set('assist_satellite.bad', 'idle')
    hass.states.async_set('assist_satellite.good', 'idle')
    async def read(text, speaker):
        if speaker.endswith('bad'):
            if failure == 'timeout': await asyncio.Event().wait()
            raise RuntimeError('speaker failed')
        return True
    ws = await hass_ws_client(hass)
    with patch.object(rt.speech, 'async_read_aloud', side_effect=read) as read_mock, patch('custom_components.sentient.websocket.SPEAKER_TEST_TIMEOUT', .01):
        await ws.send_json({'id': 1, 'type': 'sentient/doctor/speakers'})
        result = await ws.receive_json()
    assert result['success']
    rows = result['result']['results']
    assert [r['ok'] for r in rows] == [False, False, True]
    assert rows[0]['error'] and 'no longer exists' in rows[1]['error']
    assert read_mock.await_count == 2


async def test_failed_backup_replacement_preserves_previous_file(hass, tmp_path):
    from custom_components.sentient.backup import AutoBackup
    hass.config.config_dir = str(tmp_path)
    obj = AutoBackup(hass, SimpleNamespace(runtime_data=SimpleNamespace(timeline=Mock())), {})
    with patch('custom_components.sentient.backup.export_all', return_value={'sentient_backup': 1, 'version': 'original'}):
        name = await obj.async_save()
    previous = (obj.folder / name).read_text()
    with patch('custom_components.sentient.backup.export_all', return_value={'sentient_backup': 1, 'version': 'replacement'}), patch('os.replace', side_effect=OSError('disk error')):
        with pytest.raises(OSError):
            await obj.async_save()
    assert (obj.folder / name).read_text() == previous
    assert not list(obj.folder.glob('*.tmp'))


@pytest.mark.parametrize('mode', ['guest', 'forget', 'disabled', 'unload'])
async def test_late_background_remark_respects_privacy_and_settings(hass, mode):
    from tests.test_notice import _setup
    from custom_components.sentient.notice import Change
    from custom_components.sentient.privacy import forget_recent
    entry = await _setup(hass)
    rt = entry.runtime_data
    change = Change('sensor.washer', 'Private washing', None, 'running', 'finished', '', dt_util.utcnow())
    async def decide(*args):
        if mode == 'guest': rt.speech.guest_mode = True
        elif mode == 'forget': forget_recent(rt)
        elif mode == 'unload': rt.noticer.async_stop()
        else: rt.noticer.options = {**rt.noticer.options, 'chattiness': 'off'}
        return {'speak': True, 'message': 'Private washing is finished.'}
    with patch.object(rt.noticer, '_async_ask_ai', side_effect=decide), patch.object(rt.speech, 'async_say', new=AsyncMock()) as say:
        result = await rt.noticer.async_consider([change])
    say.assert_not_awaited()
    assert result.get('message') is None
    if mode == 'forget':
        assert not rt.noticer.decisions
        assert not result['changes']


async def test_guest_mode_blocks_direct_automation_remark(hass):
    from tests.test_notice import _setup
    entry = await _setup(hass)
    rt = entry.runtime_data
    rt.speech.guest_mode = True
    with patch.object(rt.speech, 'async_say', new=AsyncMock()) as say:
        result = await rt.noticer.async_notice('Private remark', decide=False)
    assert result['result'] == 'guest_mode'
    say.assert_not_awaited()


@pytest.mark.parametrize('decision', [
    {'speak': 'false', 'message': 'A remark'},
    {'speak': 'true', 'message': 'A remark'},
    {'speak': True, 'message': 123},
    {'speak': True, 'message': None},
])
async def test_malformed_noticing_reply_does_not_speak_or_crash(hass, decision):
    from tests.test_notice import _setup
    from custom_components.sentient.notice import Change
    entry = await _setup(hass)
    rt = entry.runtime_data
    change = Change('sensor.washer', 'Washing', None, 'running', 'finished', '', dt_util.utcnow())
    with patch.object(rt.noticer, '_async_ask_ai', new=AsyncMock(return_value=decision)), patch.object(rt.speech, 'async_say', new=AsyncMock()) as say, patch.object(rt.speech, 'async_ask', new=AsyncMock()) as ask:
        result = await rt.noticer.async_consider([change])
    assert result['result'] == 'stayed quiet'
    say.assert_not_awaited()
    ask.assert_not_awaited()


async def test_text_false_cannot_open_the_microphone(hass):
    from tests.test_notice import _setup
    from custom_components.sentient.notice import Change
    entry = await _setup(hass)
    rt = entry.runtime_data
    change = Change('sensor.washer', 'Washing', None, 'running', 'finished', '', dt_util.utcnow())
    decision = {'speak': True, 'message': 'The washing is finished.', 'question': 'false'}
    with patch.object(rt.noticer, '_async_ask_ai', new=AsyncMock(return_value=decision)), patch.object(rt.speech, 'async_say', new=AsyncMock(return_value=SimpleNamespace(reason='spoken'))) as say, patch.object(rt.speech, 'async_ask', new=AsyncMock()) as ask:
        await rt.noticer.async_consider([change])
    say.assert_awaited_once()
    ask.assert_not_awaited()


@pytest.mark.parametrize('mode', ['forget', 'unload'])
async def test_late_ai_failure_cannot_republish_forgotten_observations(hass, mode):
    from homeassistant.exceptions import HomeAssistantError
    from tests.test_notice import _setup
    from custom_components.sentient.notice import Change
    from custom_components.sentient.privacy import forget_recent
    entry = await _setup(hass)
    rt = entry.runtime_data
    change = Change('sensor.washer', 'Private washing', None, 'running', 'finished', '', dt_util.utcnow())
    async def fail(*args):
        if mode == 'forget': forget_recent(rt)
        else: rt.noticer.async_stop()
        raise HomeAssistantError('AI offline')
    with patch.object(rt.noticer, '_async_ask_ai', side_effect=fail):
        result = await rt.noticer.async_consider([change])
    assert result['result'] == 'cancelled'
    assert result['changes'] == [] and 'message' not in result


@pytest.mark.parametrize('mode', ['disabled', 'unloaded'])
async def test_queued_safety_speech_rechecks_disable_before_starting(hass, mode):
    from custom_components.sentient.critical import CriticalAlerts
    options = {}
    engine = CriticalAlerts(hass, options, lambda: SimpleNamespace(timeline=Mock()))
    hass.states.async_set('binary_sensor.smoke', 'on', {'device_class': 'smoke'})
    ready = asyncio.Event()
    original = engine._say_if_active
    async def queued(*args):
        await ready.wait()
        await original(*args)
    with patch.object(engine, '_say', new=AsyncMock()) as say, patch.object(engine, '_say_if_active', side_effect=queued):
        engine._raise('binary_sensor.smoke', 2)
        if mode == 'disabled': options['critical_alerts'] = False
        else: engine.async_stop()
        ready.set()
        await hass.async_block_till_done()
    say.assert_not_awaited()
    engine.async_stop()
