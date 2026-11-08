"""Queued device work must recheck live conditions and keep results honest."""
import asyncio
from datetime import timedelta
from types import SimpleNamespace
from unittest.mock import AsyncMock, Mock, patch
import pytest
from homeassistant.core import Event, State, SupportsResponse
from homeassistant.exceptions import HomeAssistantError
from homeassistant.util import dt as dt_util
from custom_components.sentient.autooff import AutoOff
from custom_components.sentient.items import Items
from custom_components.sentient.rain import Rain
from custom_components.sentient import camwake


def auto(hass, entities=None, motion=None, on_minutes=1):
    rt = SimpleNamespace(party=SimpleNamespace(active=False, lights=[]), timeline=Mock(),
                         speech=SimpleNamespace(async_say=AsyncMock()))
    items = Items(hass, 'queued', 'autooff', {})
    item = items.upsert({'name': 'Room off', 'enabled': True, 'entities': entities or ['light.room'],
                         'motion': motion or [], 'on_minutes': on_minutes, 'empty_minutes': 1, 'say': 'Room off.'})
    return AutoOff(hass, items, lambda: rt), item, rt


@pytest.mark.parametrize('missing', ['unknown', 'unavailable', 'missing', 'unexpected'])
async def test_unknown_motion_is_not_an_empty_room(hass, freezer, missing):
    obj, item, rt = auto(hass, motion=['binary_sensor.one', 'binary_sensor.two'], on_minutes=0)
    hass.states.async_set('light.room', 'on')
    hass.states.async_set('binary_sensor.one', 'off')
    if missing != 'missing': hass.states.async_set('binary_sensor.two', missing)
    freezer.tick(timedelta(minutes=2))
    assert obj.due(item, dt_util.utcnow()) == []
    item['on_minutes'] = 1  # an independent explicit on-time limit still applies
    assert obj.due(item, dt_util.utcnow()) == ['light.room']


@pytest.mark.parametrize('changed', ['motion', 'disabled', 'deleted', 'stopped', 'removed'])
async def test_autooff_rechecks_queued_decision(hass, freezer, changed):
    obj, item, rt = auto(hass, motion=['binary_sensor.motion'], on_minutes=0)
    hass.states.async_set('light.room', 'on')
    hass.states.async_set('binary_sensor.motion', 'off')
    freezer.tick(timedelta(minutes=2))
    queued = obj.due(item, dt_util.utcnow())
    assert queued == ['light.room']
    if changed == 'motion': hass.states.async_set('binary_sensor.motion', 'on')
    if changed == 'disabled': item['enabled'] = False
    if changed == 'deleted': obj.items.remove(item['id'])
    if changed == 'stopped': obj.async_stop()
    if changed == 'removed': hass.states.async_remove('light.room')
    calls = []
    hass.services.async_register('light', 'turn_off', lambda call: calls.append(call))
    assert await obj.async_switch_off(item, queued) == []
    assert not calls and not obj._switched
    rt.timeline.add.assert_not_called()
    rt.speech.async_say.assert_not_awaited()


async def test_autooff_partial_failure_is_not_reported_as_all_off(hass, freezer):
    obj, item, rt = auto(hass, entities=['light.bad', 'light.good'])
    for eid in item['entities']: hass.states.async_set(eid, 'on')
    freezer.tick(timedelta(minutes=2))
    async def off(call):
        if call.data['entity_id'] == 'light.bad': raise HomeAssistantError('offline')
        hass.states.async_set('light.good', 'off')
    hass.services.async_register('light', 'turn_off', off)
    assert await obj.async_switch_off(item, item['entities']) == ['light.good']
    assert set(obj._switched) == {'light.good'}
    assert 'bad' not in rt.timeline.add.call_args.args[1]
    assert rt.timeline.add.call_args.kwargs['evidence'] == 'attempted'
    rt.speech.async_say.assert_not_awaited()


async def test_autooff_rechecks_motion_between_targets(hass, freezer):
    obj, item, rt = auto(hass, entities=['light.one', 'light.two'], motion=['binary_sensor.motion'], on_minutes=0)
    for eid in item['entities']: hass.states.async_set(eid, 'on')
    hass.states.async_set('binary_sensor.motion', 'off')
    freezer.tick(timedelta(minutes=2))
    calls = []
    async def off(call):
        calls.append(call.data['entity_id'])
        hass.states.async_set(call.data['entity_id'], 'off')
        hass.states.async_set('binary_sensor.motion', 'on')
    hass.services.async_register('light', 'turn_off', off)
    assert await obj.async_switch_off(item, item['entities']) == ['light.one']
    assert calls == ['light.one']


async def test_overlapping_autooff_does_not_send_duplicate_inflight_request(hass, freezer):
    obj, item, rt = auto(hass)
    hass.states.async_set('light.room', 'on')
    freezer.tick(timedelta(minutes=2))
    entered, release = asyncio.Event(), asyncio.Event()
    async def off(call):
        entered.set()
        await release.wait()
        hass.states.async_set('light.room', 'off')
    hass.services.async_register('light', 'turn_off', off)
    first = asyncio.create_task(obj.async_switch_off(item, ['light.room']))
    await entered.wait()
    assert await obj.async_switch_off(item, ['light.room']) == []
    release.set()
    assert await first == ['light.room']


@pytest.mark.parametrize('old_state,learn', [('on', False), ('unavailable', False), ('off', True)])
async def test_autooff_only_learns_from_real_off_to_on_transition(hass, old_state, learn):
    obj, item, rt = auto(hass, motion=['binary_sensor.motion'])
    obj._switched['light.room'] = (dt_util.utcnow(), item['id'])
    obj._back_on(Event('state_changed', {'old_state': State('light.room', old_state), 'new_state': State('light.room', 'on')}))
    assert item['empty_minutes'] == (6 if learn else 1)


def rain(hass):
    rt = SimpleNamespace(speech=SimpleNamespace(async_say=AsyncMock(return_value=SimpleNamespace(spoken=True))))
    obj = Rain(hass, {'rain_warnings': True, 'briefing_weather': 'weather.home'}, lambda: rt)
    hass.states.async_set('weather.home', 'sunny')
    hass.states.async_set('binary_sensor.window', 'on', {'device_class': 'window'})
    return obj, rt


def forecast(chance=90, when=None):
    return {'weather.home': {'forecast': [{'datetime': when or (dt_util.utcnow() + timedelta(minutes=30)).isoformat(),
                                          'condition': 'rainy', 'precipitation_probability': chance}]}}


@pytest.mark.parametrize('change', ['closed', 'disabled', 'stopped'])
async def test_rain_rechecks_after_forecast_returns(hass, change):
    obj, rt = rain(hass)
    async def get(call):
        if change == 'closed': hass.states.async_set('binary_sensor.window', 'off')
        if change == 'disabled': obj.options = {**obj.options, 'rain_warnings': False}
        if change == 'stopped': obj.async_stop()
        return forecast()
    hass.services.async_register('weather', 'get_forecasts', get, supports_response=SupportsResponse.ONLY)
    await obj.async_check()
    rt.speech.async_say.assert_not_awaited()


@pytest.mark.parametrize('chance,expected', [('90', True), ('nan', False), ('bad', False), (101, False), (-1, False), (None, True)])
async def test_rain_validates_forecast_probabilities(hass, chance, expected):
    obj, rt = rain(hass)
    async def get(call): return forecast(chance)
    hass.services.async_register('weather', 'get_forecasts', get, supports_response=SupportsResponse.ONLY)
    assert await obj.async_rain_due() is expected


@pytest.mark.parametrize('when', ['naive', 'past'])
async def test_rain_ignores_ambiguous_or_past_forecast(hass, when):
    obj, rt = rain(hass)
    stamp = dt_util.utcnow().replace(tzinfo=None).isoformat() if when == 'naive' else (dt_util.utcnow() - timedelta(minutes=30)).isoformat()
    async def get(call): return forecast(when=stamp)
    hass.services.async_register('weather', 'get_forecasts', get, supports_response=SupportsResponse.ONLY)
    assert await obj.async_rain_due() is False


async def test_rain_does_not_overlap_forecast_requests(hass):
    obj, rt = rain(hass)
    entered, release = asyncio.Event(), asyncio.Event()
    count = 0
    async def get(call):
        nonlocal count
        count += 1
        entered.set()
        await release.wait()
        return forecast()
    hass.services.async_register('weather', 'get_forecasts', get, supports_response=SupportsResponse.ONLY)
    first = asyncio.create_task(obj.async_check())
    await entered.wait()
    assert 'skipped' in await obj.async_check()
    release.set()
    await first
    assert count == 1 and rt.speech.async_say.await_count == 1


WAKE = {'camera_wake': [{'action': 'button.press', 'target': {'entity_id': 'button.wake'}}], 'camera_wake_seconds': 0}


async def test_simultaneous_camera_requests_wait_for_shared_wake(hass):
    entered, release = asyncio.Event(), asyncio.Event()
    async def wake(*a, **k):
        entered.set()
        await release.wait()
    with patch('custom_components.sentient.shortcuts.async_run_actions', side_effect=wake) as actions:
        first = asyncio.create_task(camwake.async_wake(hass, WAKE, 'camera.garden'))
        await entered.wait()
        second = asyncio.create_task(camwake.async_wake(hass, WAKE, 'camera.garden'))
        await asyncio.sleep(0)
        assert not second.done()
        release.set()
        assert await asyncio.gather(first, second) == [True, False]
    assert actions.await_count == 1
    camwake.clear(hass)


async def test_failed_camera_wake_is_not_cached_as_success(hass):
    with patch('custom_components.sentient.shortcuts.async_run_actions', new=AsyncMock(side_effect=[HomeAssistantError('offline'), None])) as actions:
        with pytest.raises(HomeAssistantError, match='freshness is unconfirmed'):
            await camwake.async_wake(hass, WAKE, 'camera.garden')
        assert await camwake.async_wake(hass, WAKE, 'camera.garden') is True
    assert actions.await_count == 2
    camwake.clear(hass)


async def test_cancelling_one_camera_caller_preserves_shared_wake(hass):
    entered, release = asyncio.Event(), asyncio.Event()
    async def wake(*a, **k):
        entered.set()
        await release.wait()
    with patch('custom_components.sentient.shortcuts.async_run_actions', side_effect=wake) as actions:
        first = asyncio.create_task(camwake.async_wake(hass, WAKE, 'camera.garden'))
        await entered.wait()
        second = asyncio.create_task(camwake.async_wake(hass, WAKE, 'camera.garden'))
        await asyncio.sleep(0)
        first.cancel()
        with pytest.raises(asyncio.CancelledError): await first
        release.set()
        assert await second is False
        assert await camwake.async_wake(hass, WAKE, 'camera.garden') is False
    assert actions.await_count == 1
    camwake.clear(hass)


async def test_camera_wake_configuration_change_invalidates_success(hass):
    with patch('custom_components.sentient.shortcuts.async_run_actions', new=AsyncMock()) as actions:
        assert await camwake.async_wake(hass, WAKE, 'camera.garden')
        assert await camwake.async_wake(hass, WAKE, 'camera.garden') is False
        assert await camwake.async_wake(hass, {**WAKE, 'camera_wake': [{'action': 'button.press', 'target': {'entity_id': 'button.other'}}]}, 'camera.garden')
    assert actions.await_count == 2
    camwake.clear(hass)


async def test_camera_clear_cancels_pending_wake(hass):
    entered = asyncio.Event()
    async def wake(*a, **k):
        entered.set()
        await asyncio.Event().wait()
    with patch('custom_components.sentient.shortcuts.async_run_actions', side_effect=wake):
        task = asyncio.create_task(camwake.async_wake(hass, WAKE, 'camera.garden'))
        await entered.wait()
        camwake.clear(hass)
        with pytest.raises(asyncio.CancelledError): await task


async def test_failed_wake_does_not_suppress_a_sensor_security_alert(hass):
    from custom_components.sentient.camsecurity import CameraSecurity
    camera = CameraSecurity(hass, WAKE, lambda: SimpleNamespace())
    with patch('custom_components.sentient.camwake.async_wake', new=AsyncMock(side_effect=HomeAssistantError('offline'))):
        assert await camera._describe('camera.garden') == (None, None)


async def test_autooff_timeout_does_not_claim_success(hass):
    obj, item, rt = auto(hass)
    hass.states.async_set('light.room', 'on')
    async def off(call): await asyncio.Event().wait()
    hass.services.async_register('light', 'turn_off', off)
    with patch('custom_components.sentient.autooff.REQUEST_TIMEOUT', .01):
        assert await obj.async_switch_off(item, ['light.room'], force=True) == []
    assert not obj._busy and not obj._switched
    rt.timeline.add.assert_not_called()
    rt.speech.async_say.assert_not_awaited()


async def test_forecast_timeout_returns_without_speaking(hass):
    obj, rt = rain(hass)
    async def get(call): await asyncio.Event().wait()
    hass.services.async_register('weather', 'get_forecasts', get, supports_response=SupportsResponse.ONLY)
    with patch('custom_components.sentient.rain.FORECAST_TIMEOUT', .01):
        assert (await obj.async_check())['rain'] is False
    assert not obj._lock.locked()
    rt.speech.async_say.assert_not_awaited()


async def test_camera_timeout_is_not_cached(hass):
    async def slow(*a, **k): await asyncio.Event().wait()
    with patch('custom_components.sentient.shortcuts.async_run_actions', side_effect=slow), patch('custom_components.sentient.camwake.WAKE_TIMEOUT', .01):
        with pytest.raises(HomeAssistantError, match='freshness is unconfirmed'):
            await camwake.async_wake(hass, WAKE, 'camera.garden')
    with patch('custom_components.sentient.shortcuts.async_run_actions', new=AsyncMock()):
        assert await camwake.async_wake(hass, WAKE, 'camera.garden')
    camwake.clear(hass)


async def test_failed_camera_wake_retains_plain_sensor_alert(hass):
    from custom_components.sentient.camsecurity import CameraSecurity
    from custom_components.sentient.const import CONF_CAMSEC_DESCRIBE
    rt = SimpleNamespace(incidents=None, timeline=Mock(), watches=SimpleNamespace(spend=lambda *a: True),
                         clips=None, speech=SimpleNamespace(quiet_hours_now=lambda: False))
    camera = CameraSecurity(hass, {**WAKE, CONF_CAMSEC_DESCRIBE: True}, lambda: rt)
    with patch('custom_components.sentient.camsecurity.camera_for', return_value='camera.garden'), patch('custom_components.sentient.camwake.async_wake', new=AsyncMock(side_effect=HomeAssistantError('offline'))):
        await camera._async_alert('binary_sensor.person', 'Person detected', incident_id=None, reason='night')
    assert rt.timeline.add.call_args.args[0] == 'security'
    assert 'Person detected' in rt.timeline.add.call_args.args[1]


async def test_manual_autooff_test_still_works_without_waiting_for_timer(hass):
    obj, item, rt = auto(hass)
    hass.states.async_set('light.room', 'on')
    async def off(call): hass.states.async_set('light.room', 'off')
    hass.services.async_register('light', 'turn_off', off)
    assert obj.due(item, dt_util.utcnow()) == []
    assert await obj.async_switch_off(item, ['light.room'], force=True) == ['light.room']


async def test_manual_autooff_never_overrides_never_touch(hass):
    obj, item, rt = auto(hass)
    hass.states.async_set('light.room', 'on')
    with patch('custom_components.sentient.never.never_list', return_value={'light.room'}):
        assert await obj.async_switch_off(item, ['light.room'], force=True) == []
    assert not obj._switched
    rt.timeline.add.assert_not_called()


async def test_shared_camera_wake_includes_stabilisation_delay(hass):
    original_sleep = asyncio.sleep
    settling, release = asyncio.Event(), asyncio.Event()
    async def delay(seconds):
        if seconds:
            settling.set()
            await release.wait()
        else:
            await original_sleep(0)
    with patch('custom_components.sentient.shortcuts.async_run_actions', new=AsyncMock()), patch('custom_components.sentient.camwake.asyncio.sleep', side_effect=delay):
        first = asyncio.create_task(camwake.async_wake(hass, {**WAKE, 'camera_wake_seconds': 3}, 'camera.garden'))
        await settling.wait()
        second = asyncio.create_task(camwake.async_wake(hass, {**WAKE, 'camera_wake_seconds': 3}, 'camera.garden'))
        await original_sleep(0)
        assert not second.done()
        release.set()
        assert await asyncio.gather(first, second) == [True, False]
    camwake.clear(hass)


async def test_camera_success_cache_is_bounded(hass):
    with patch('custom_components.sentient.shortcuts.async_run_actions', new=AsyncMock()):
        for i in range(camwake.CACHE_LIMIT + 1):
            await camwake.async_wake(hass, WAKE, f'camera.item_{i}')
    assert len(hass.data[camwake._CACHE]) == camwake.CACHE_LIMIT
    camwake.clear(hass)


@pytest.mark.parametrize('payload', [None, [], {'weather.home': {'forecast': 'invalid'}}])
async def test_malformed_forecast_payload_is_skipped(hass, payload):
    obj, rt = rain(hass)
    async def get(call): return payload
    hass.services.async_register('weather', 'get_forecasts', get, supports_response=SupportsResponse.ONLY)
    assert await obj.async_rain_due() is False
