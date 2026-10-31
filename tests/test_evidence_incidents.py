"""Uncertainty, incident correlation, retention and alert escalation."""
from datetime import timedelta
from unittest.mock import patch

from homeassistant.util import dt as dt_util

from custom_components.sentient.evidence import Evidence, describe_state
from custom_components.sentient.incidents import Incidents
from custom_components.sentient.security import check


async def test_unavailable_security_state_never_means_secure(hass):
    hass.states.async_set('binary_sensor.gate', 'off', {'device_class': 'door'})
    evidence = Evidence(hass, 'test', {})
    await evidence.async_start()
    assert check(hass)['secure'] is True
    hass.states.async_set('binary_sensor.gate', 'unavailable', {'device_class': 'door'})
    await hass.async_block_till_done()
    reading = describe_state(hass, 'binary_sensor.gate')
    assert reading['status'] == 'unknown' and reading['last_known']['state'] == 'off'
    assert check(hass)['secure'] is None
    assert "can't confirm" in check(hass)['summary']
    evidence.async_stop()


async def test_old_unchanged_event_sensor_is_not_declared_stale(hass, freezer):
    hass.states.async_set('binary_sensor.door', 'off', {'device_class': 'door'})
    freezer.tick(timedelta(hours=24))
    reading = describe_state(hass, 'binary_sensor.door')
    assert reading['report_age_seconds'] >= 86400
    assert reading['status'] == 'observed'
    assert 'does not prove staleness' in reading['note']


async def test_contacts_detections_and_clips_share_incident(hass):
    options = {'security_incidents': True, 'camera_security_entries': ['binary_sensor.gate'],
               'camera_security_sensors': ['binary_sensor.garden_person']}
    hass.states.async_set('binary_sensor.gate', 'off')
    hass.states.async_set('binary_sensor.garden_person', 'off')
    obj = Incidents(hass, 'test', options)
    await obj.async_start()
    with patch('custom_components.sentient.camsecurity.camera_for', return_value='camera.garden'):
        hass.states.async_set('binary_sensor.gate', 'on')
        hass.states.async_set('binary_sensor.garden_person', 'on')
        await hass.async_block_till_done()
    obj.add('camera.garden', 'A person walked through', 'clip', evidence='inferred', event_id='event1')
    obj.add('camera.garden', 'Duplicate summary', 'clip', evidence='inferred', event_id='event1')
    items = obj.recent('gate')
    assert len(items) == 1
    assert [e['kind'] for e in items[0]['evidence']] == ['contact', 'detection', 'clip']
    assert 'not proof' in items[0]['link']
    assert items[0]['evidence'][-1]['status'] == 'inferred'
    obj.async_stop()


async def test_alerts_repeat_only_on_escalation(hass):
    obj = Incidents(hass, 'test', {'security_incidents': True})
    with patch('custom_components.sentient.camsecurity.camera_for', side_effect=lambda h, s: s):
        first, iid = obj.alert('camera.gate', 'At the gate', 'night')
        repeated, same = obj.alert('camera.garden', 'Now at the garden', 'night')
        escalation, same2 = obj.alert('camera.shed', 'Trying to enter', 'fence')
        assert first and not repeated and escalation
        assert iid == same == same2
        assert len(obj.recent()[0]['evidence']) == 3


async def test_late_clip_attaches_to_original_incident_not_new_visit(hass, freezer):
    obj = Incidents(hass, 'test', {'security_incidents': True})
    first_time = dt_util.utcnow()
    first = obj.add('camera.gate', 'First visit', 'detection')
    freezer.tick(timedelta(minutes=5))
    second = obj.add('camera.gate', 'Second visit', 'detection')
    clip = obj.add('camera.gate', 'First visit clip', 'clip', when=first_time, event_id='one')
    assert clip['id'] == first['id'] != second['id']
    freezer.tick(timedelta(days=8))
    assert not obj.recent()


async def test_disabled_incidents_do_not_collect(hass):
    obj = Incidents(hass, 'test', {})
    assert obj.add('camera.gate', 'No collection', 'detection') is None
    assert obj.recent() == []


async def test_evidence_survives_restart_without_replacing_known_reading(hass):
    data = {'last': {'lock.front': {'state': 'locked', 'reported_at': dt_util.utcnow().isoformat()}}}
    hass.states.async_set('lock.front', 'unavailable')
    obj = Evidence(hass, 'test', {})
    from unittest.mock import AsyncMock
    with patch.object(obj._store, 'async_load', AsyncMock(return_value=data)):
        await obj.async_start()
    assert describe_state(hass, 'lock.front')['last_known']['state'] == 'locked'
    obj.async_stop()


async def test_new_incident_can_alert_within_old_sensor_cooldown(hass, freezer):
    from custom_components.sentient.camsecurity import CameraSecurity
    from types import SimpleNamespace
    from unittest.mock import AsyncMock
    incidents = Incidents(hass, 'test', {'security_incidents': True})
    rt = SimpleNamespace(incidents=incidents)
    security = CameraSecurity(hass, {}, lambda: rt)
    with patch.object(security, '_async_alert', AsyncMock()) as say, patch('custom_components.sentient.camsecurity.camera_for', return_value='camera.gate'):
        security._alert('binary_sensor.gate_person', 'At gate', 'night')
        await hass.async_block_till_done()
        freezer.tick(timedelta(minutes=5))
        security._alert('binary_sensor.gate_person', 'At gate again', 'night')
        await hass.async_block_till_done()
    assert say.await_count == 2 and len(incidents.recent()) == 2
