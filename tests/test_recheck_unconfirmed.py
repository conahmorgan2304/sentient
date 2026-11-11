"""A guarded change that hadn't reported back by the reply is looked at once more (setting on by default)."""
from datetime import timedelta
from unittest.mock import Mock, patch

from homeassistant.core import Context
from homeassistant.util import dt as dt_util
from pytest_homeassistant_custom_component.common import async_fire_time_changed, async_mock_service

from custom_components.sentient.control import RECHECK_AFTER, Control
from tests.test_voice_sessions_control import active


async def _slow_lamp(hass, options):
    _, session = active()
    hass.states.async_set('light.lamp', 'off')
    calls = async_mock_service(hass, 'light', 'turn_on')
    timeline = Mock()
    control = Control(hass, options, timeline)
    with patch('custom_components.sentient.control.async_should_expose', return_value=True):
        result = await control.propose(session, [{'entity_id': 'light.lamp', 'action': 'turn_on'}], Context())
    assert result['actions'][0]['status'] == 'attempted' and result['assessment']['state'] == 'unverified'
    return session, result, calls, timeline


async def test_a_slow_device_is_confirmed_later_without_sending_again(hass):
    session, result, calls, timeline = await _slow_lamp(hass, {})
    hass.states.async_set('light.lamp', 'on')  # the lamp reports in after the reply
    async_fire_time_changed(hass, dt_util.utcnow() + timedelta(seconds=RECHECK_AFTER + 1))
    await hass.async_block_till_done()
    assert result['actions'][0]['status'] == 'verified' and result['actions'][0]['verified_later']
    assert result['assessment']['state'] == 'reported_complete'
    assert len(calls) == 1  # never sent twice
    assert any(c.args[1].startswith('Confirmed later') for c in timeline.add.call_args_list)


async def test_still_silent_is_noted_and_never_called_done(hass):
    session, result, _calls, _timeline = await _slow_lamp(hass, {})
    async_fire_time_changed(hass, dt_util.utcnow() + timedelta(seconds=RECHECK_AFTER + 1))
    await hass.async_block_till_done()
    assert result['actions'][0]['status'] == 'attempted'
    assert 'still not reported' in result['actions'][0]['reason']


async def test_switched_off_or_forgotten_nothing_is_rechecked(hass):
    session, result, _calls, _timeline = await _slow_lamp(hass, {'recheck_unconfirmed': False})
    hass.states.async_set('light.lamp', 'on')
    async_fire_time_changed(hass, dt_util.utcnow() + timedelta(seconds=RECHECK_AFTER + 1))
    await hass.async_block_till_done()
    assert result['actions'][0]['status'] == 'attempted' and 'rechecked_at' not in result

    session, result, _calls, _timeline = await _slow_lamp(hass, {})
    session.forgotten = True  # "forget that" must not have a record quietly updated afterwards
    hass.states.async_set('light.lamp', 'on')
    async_fire_time_changed(hass, dt_util.utcnow() + timedelta(seconds=RECHECK_AFTER + 1))
    await hass.async_block_till_done()
    assert 'rechecked_at' not in result
