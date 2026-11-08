"""Voice follow-through must stay current and report uncertainty honestly."""
from types import SimpleNamespace
from unittest.mock import AsyncMock, Mock, patch

import pytest
from homeassistant.core import State
from custom_components.sentient.followthrough import FollowThrough, worked, expected


@pytest.fixture
def engine(hass):
    speech = SimpleNamespace(options={}, _privacy_generation=0, _guest_generation=0, guest_mode=False, async_say=AsyncMock())
    obj = FollowThrough(hass, speech, Mock())
    hass.states.async_set('light.test', 'off', {'friendly_name':'Test light'})
    return obj


def pending(obj):
    row = {'want': ('on',None), 'satellite':'assist_satellite.test', 'reached':False,
           'stamp':obj._stamp(), 'name':'Test light', 'cancel':Mock()}
    obj._pending['light.test'] = row
    return row


async def test_unload_cancels_pending_timers_and_voice_context(engine):
    row = pending(engine)
    engine._voice = ('speaker', None)
    engine.async_stop()
    row['cancel'].assert_called_once()
    assert not engine._pending and engine._voice is None
    engine._check('light.test', row, True)
    engine.speech.async_say.assert_not_awaited()


@pytest.mark.parametrize('change', ['privacy', 'guest', 'settings', 'cleared', 'stopped'])
async def test_queued_check_cannot_report_after_context_changes(engine, change):
    row = pending(engine)
    if change == 'privacy': engine.speech._privacy_generation += 1
    if change == 'guest': engine.speech._guest_generation += 1
    if change == 'settings': engine.speech.options = dict(engine.speech.options)
    if change == 'cleared': engine.clear()
    if change == 'stopped': engine.async_stop()
    engine._check('light.test', row, True)
    await engine.hass.async_block_till_done()
    engine.speech.async_say.assert_not_awaited()
    engine.timeline.add.assert_not_called()


async def test_device_recovery_before_report_suppresses_stale_warning(engine):
    row = pending(engine)
    old = engine.hass.states.get('light.test')
    engine.hass.states.async_set('light.test', 'on')
    await engine._async_report([old], row['want'], row['satellite'], 'light.test', row)
    engine.speech.async_say.assert_not_awaited()
    engine.timeline.add.assert_not_called()


async def test_new_request_before_report_invalidates_old_snapshot(engine):
    old = pending(engine)
    pending(engine)
    await engine._async_report([engine.hass.states.get('light.test')], old['want'], old['satellite'], 'light.test', old)
    engine.speech.async_say.assert_not_awaited()


async def test_duplicate_check_does_not_schedule_duplicate_report(engine):
    row = pending(engine)
    engine._check('light.test', row, True)
    engine._check('light.test', row, True)
    await engine.hass.async_block_till_done()
    engine.speech.async_say.assert_awaited_once()
    assert not engine._pending


async def test_offline_report_does_not_claim_proven_physical_failure(engine):
    row = pending(engine)
    engine.hass.states.async_set('light.test', 'unavailable', {'friendly_name':'Test light'})
    engine._check('light.test', row, True)
    await engine.hass.async_block_till_done()
    message = engine.speech.async_say.call_args.args[0]
    assert "couldn't confirm" in message and "isn't on" not in message
    assert engine.timeline.add.call_args.args[1].startswith('Unconfirmed voice request:')


@pytest.mark.parametrize('value', ['nan', 'inf', '-inf', 'not a number', None])
def test_bad_temperature_requests_are_not_registered(value):
    assert expected('climate', 'set_temperature', {'temperature':value}) is None


@pytest.mark.parametrize('state,position,desired,result', [('opening',50,'open',False), ('closing',50,'closed',False),
    ('open',50,'open',False), ('open',100,'open',True), ('closed',0,'closed',True)])
def test_cover_movement_or_partial_position_is_not_completion(state, position, desired, result):
    assert worked(State('cover.test', state, {'current_position':position}), ('state',desired)) is result


def test_nonfinite_temperature_feedback_is_unknown():
    assert worked(State('climate.test','heat',{'temperature':'nan'}), ('temperature',20)) is None


async def test_weekly_report_keeps_unconfirmed_requests_separate_from_proven_failures(hass):
    from tests.test_followthrough import _setup
    from custom_components.sentient.weekly import build, summary
    entry = await _setup(hass)
    entry.runtime_data.timeline.add('health', 'Unconfirmed voice request: Lamp on')
    report = build(entry.runtime_data)
    assert report['unconfirmed_requests'] == 1
    assert not any("didn't happen" in item for item in report.get('got_wrong', []))
    assert 'Unconfirmed: 1 voice request' in summary(report)
