"""Cross-feature audit regressions for truthful reports and changes during asynchronous work."""
from datetime import timedelta
from unittest.mock import AsyncMock, Mock, patch

import pytest
from homeassistant.core import Context
from homeassistant.exceptions import HomeAssistantError
from homeassistant.util import dt as dt_util
from pytest_homeassistant_custom_component.common import async_mock_service

from custom_components.sentient.control import Control, snapshot, target_reached
from custom_components.sentient.security import check
from custom_components.sentient.speech import SpeechRouter
from custom_components.sentient.incidents import Incidents
from tests.test_voice_sessions_control import active


@pytest.mark.parametrize('domain,state,attrs', [
    ('lock', 'locking', {}), ('lock', 'unlocking', {}),
    ('cover', 'closing', {'device_class': 'garage'}),
    ('alarm_control_panel', 'arming', {}),
])
async def test_moving_security_devices_do_not_report_secure(hass, domain, state, attrs):
    hass.states.async_set(f'{domain}.entry', state, attrs)
    out = check(hass)
    assert out['secure'] is None
    assert out['unknown']
    assert "Everything's shut" not in out['summary']


@pytest.mark.parametrize('state', ['triggered', 'pending'])
async def test_alarm_alert_not_reported_off_or_secure(hass, state):
    hass.states.async_set('lock.front', 'locked')
    hass.states.async_set('alarm_control_panel.home', state)
    out = check(hass)
    assert out['secure'] is False
    assert f"L'alarme is {state}" in out['summary']
    assert 'L\'alarme is off' not in out['summary']


async def test_alarm_alone_cannot_confirm_openings_or_locks(hass):
    hass.states.async_set('alarm_control_panel.home', 'armed_home')
    out = check(hass)
    assert out['secure'] is None
    assert 'No door' in out['summary'] and 'set (home)' in out['summary']


async def test_missing_reading_does_not_hide_known_open_door(hass):
    hass.states.async_set('binary_sensor.gate', 'on', {'device_class': 'door', 'friendly_name': 'Gate'})
    hass.states.async_set('lock.front', 'unavailable')
    out = check(hass)
    assert out['secure'] is False
    assert 'Gate is open' in out['summary'] and 'No settled reading' in out['summary']


async def test_speech_without_delivery_route_fails_instead_of_claiming_success(hass):
    speech = SpeechRouter(hass, {})
    with patch.object(speech, '_remember_said') as remember:
        with pytest.raises(HomeAssistantError, match='No usable'):
            await speech._speak('Gate is open', ['media_player.kitchen'])
    remember.assert_not_called()
    assert speech.failures


async def test_batch_revalidates_next_device_after_first_service(hass):
    _, session = active()
    control = Control(hass, {}, Mock())
    for eid in ['light.first', 'light.second']:
        hass.states.async_set(eid, 'on')
    calls = []
    async def off(call):
        calls.append(call.data['entity_id'])
        hass.states.async_set('light.first', 'off')
        hass.states.async_remove('light.second')
    hass.services.async_register('light', 'turn_off', off)
    with patch('custom_components.sentient.control.async_should_expose', return_value=True):
        out = await control.propose(session, [
            {'entity_id': 'light.first', 'action': 'turn_off'},
            {'entity_id': 'light.second', 'action': 'turn_off'}], Context())
    assert calls == ['light.first']
    assert [a['status'] for a in out['actions']] == ['verified', 'failed']


async def test_confirmed_batch_stops_if_later_target_changes(hass):
    sessions, session = active()
    control = Control(hass, {}, Mock())
    for eid in ['lock.first', 'lock.second']:
        hass.states.async_set(eid, 'locked')
    calls = []
    async def unlock(call):
        calls.append(call.data['entity_id'])
        hass.states.async_set('lock.first', 'unlocked')
        hass.states.async_set('lock.second', 'unlocked')
    hass.services.async_register('lock', 'unlock', unlock)
    with patch('custom_components.sentient.control.async_should_expose', return_value=True):
        await control.propose(session, [{'entity_id': e, 'action': 'unlock'} for e in ['lock.first', 'lock.second']], Context())
        session.turn += 1
        out = await control.confirm(session, Context())
    assert calls == ['lock.first']
    assert out['actions'][1]['status'] == 'skipped'


async def test_undo_rechecks_manual_change_during_permission_lookup(hass):
    _, session = active()
    control = Control(hass, {'voice_undo': True}, Mock())
    hass.states.async_set('light.lamp', 'off')
    before = snapshot(hass.states.get('light.lamp'))
    hass.states.async_set('light.lamp', 'on')
    session.undo = [{'entity_id': 'light.lamp', 'before': before,
                     'after': snapshot(hass.states.get('light.lamp')),
                     'expires': dt_util.utcnow() + timedelta(minutes=1)}]
    calls = async_mock_service(hass, 'light', 'turn_off')
    async def validate(*args):
        hass.states.async_set('light.lamp', 'on', {'brightness': 123})
    with patch.object(control, 'validate', side_effect=validate):
        out = await control.undo(session, Context())
    assert not calls and out['undo'][0]['status'] == 'skipped'


async def test_reload_during_validation_prevents_dispatch(hass):
    _, session = active()
    control = Control(hass, {}, Mock())
    hass.states.async_set('light.lamp', 'on')
    calls = async_mock_service(hass, 'light', 'turn_off')
    async def validate(*args):
        session.closed = True
    with patch.object(control, 'validate', side_effect=validate):
        out = await control.execute(session, [{'entity_id': 'light.lamp', 'action': 'turn_off'}], Context())
    assert not calls and out['actions'][0]['status'] == 'skipped'


@pytest.mark.parametrize('brightness', [None, 'bad', float('nan')])
async def test_invalid_device_brightness_is_not_verified(hass, brightness):
    hass.states.async_set('light.lamp', 'on', {'brightness': brightness})
    assert not target_reached(hass.states.get('light.lamp'), {'action': 'turn_on', 'brightness_pct': 50})


@pytest.mark.parametrize('temperature', [None, 'unknown', float('nan')])
async def test_unknown_prior_temperature_requires_confirmation(hass, temperature):
    hass.states.async_set('climate.room', 'heat', {'temperature': temperature})
    assert Control(hass, {}, Mock()).needs_confirmation([
        {'entity_id': 'climate.room', 'action': 'set_temperature', 'temperature': 20}])


async def test_backup_never_overwrites_a_partial_name_or_alias(hass):
    from tests.test_phase3 import _setup
    from custom_components.sentient.backup import import_all
    entry = await _setup(hass)
    shortcuts = entry.runtime_data.shortcuts
    original = shortcuts.upsert({'name': 'Movie night', 'aliases': 'cinema', 'say': 'Original'})
    import_all(hass, entry, {'sentient_backup': 1, 'routines': [{'name': 'Movie', 'say': 'Imported'}]})
    assert shortcuts.items[original.id].name == 'Movie night'
    assert len(shortcuts.items) == 2
    import_all(hass, entry, {'sentient_backup': 1, 'routines': [{'name': 'cinema', 'say': 'Wrong'}]})
    assert shortcuts.items[original.id].say == 'Original'
    import_all(hass, entry, {'sentient_backup': 1, 'routines': [{'name': 'Movie', 'say': 'Updated'}]})
    assert len(shortcuts.items) == 2


async def test_clip_dedup_survives_ambiguous_temporal_matches(hass):
    incidents = Incidents(hass, 'audit', {'security_incidents': True})
    first = incidents.add('camera.gate', 'First clip', 'clip', event_id='abc')
    # A distinct nearby episode makes temporal matching ambiguous.
    from copy import deepcopy
    other = deepcopy(first)
    other['id'] = 'other'
    other['evidence'] = []
    incidents.items.append(other)
    result = incidents.add('camera.gate', 'Repeated clip', 'clip', event_id='abc')
    assert result['id'] == first['id'] and len(incidents.items) == 2


async def test_delayed_incidents_stay_sorted_by_event_time(hass, freezer):
    incidents = Incidents(hass, 'audit', {'security_incidents': True})
    old = dt_util.utcnow()
    first = incidents.add('camera.gate', 'First visit', 'detection')
    freezer.tick(timedelta(minutes=10))
    newest = incidents.add('camera.gate', 'New visit', 'detection')
    late = incidents.add('camera.shed', 'Old clip', 'clip', when=old)
    assert late['id'] != first['id']
    assert incidents.recent()[0]['id'] == newest['id']
    assert incidents.add('camera.shed', 'Expired clip', 'clip', when=old - timedelta(days=8)) is None


async def test_downloaded_diagnostics_redact_additional_personal_options(hass):
    from tests.test_phase3 import _setup
    from custom_components.sentient.diagnostics import async_get_config_entry_diagnostics
    entry = await _setup(hass, personality_notes='Private household notes',
                         style_notes='Private communication preferences',
                         known_cars='Private name: private car',
                         face_people='Private household names')
    data = await async_get_config_entry_diagnostics(hass, entry)
    for key in ('personality_notes', 'style_notes', 'known_cars', 'face_people'):
        assert data['options'][key] == '**REDACTED**'


@pytest.mark.parametrize('grouped', [True, False])
async def test_rejected_camera_alert_does_not_suppress_next_real_detection(hass, grouped):
    from types import SimpleNamespace
    from custom_components.sentient.camsecurity import CameraSecurity
    incidents = Incidents(hass, 'audit', {'security_incidents': grouped})
    rt = SimpleNamespace(incidents=incidents, timeline=Mock(), watches=Mock(), clips=None,
                         speech=Mock())
    rt.speech.quiet_hours_now.return_value = False
    camera = CameraSecurity(hass, {'camera_security_describe': True}, lambda: rt)
    from custom_components.sentient.const import CONF_CAMSEC_DESCRIBE
    camera.options[CONF_CAMSEC_DESCRIBE] = True
    with patch('custom_components.sentient.camsecurity.camera_for', return_value='camera.gate'), patch.object(camera, '_describe', AsyncMock(side_effect=[(False, None), (True, 'A person')])):
        camera._alert('binary_sensor.person', 'Person at gate', 'night')
        await hass.async_block_till_done()
        if grouped:
            assert incidents.recent()[0]['alert_level'] == 0
        camera._alert('binary_sensor.person', 'Person at gate', 'night')
        await hass.async_block_till_done()
    if grouped:
        assert incidents.recent()[0]['alert_level'] == 1
    assert any(call.args[0] == 'security' for call in rt.timeline.add.call_args_list)
    assert not incidents._pending_alerts and not camera._pending_alert_keys


async def test_jammed_lock_report_does_not_invent_unlocked_state(hass):
    hass.states.async_set('lock.front', 'jammed', {'friendly_name': 'Front lock'})
    out = check(hass)
    assert out['secure'] is False
    assert 'Front lock is jammed' in out['summary']
    assert 'is unlocked' not in out['summary']


async def test_multiple_alarms_report_individual_states(hass):
    hass.states.async_set('lock.front', 'locked')
    hass.states.async_set('alarm_control_panel.home', 'armed_home', {'friendly_name': 'House alarm'})
    hass.states.async_set('alarm_control_panel.shed', 'triggered', {'friendly_name': 'Shed alarm'})
    out = check(hass)
    assert out['secure'] is False
    assert 'House alarm is set (home)' in out['summary']
    assert 'Shed alarm is triggered' in out['summary']
