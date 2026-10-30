"""Regressions for streamed media, rule validation, routine selection and queued speech."""
import asyncio
from types import SimpleNamespace
from unittest.mock import AsyncMock, MagicMock, Mock, patch

from aiohttp import StreamReader
import pytest
from homeassistant.exceptions import HomeAssistantError
from pytest_homeassistant_custom_component.common import async_mock_service

from custom_components.sentient.clipwatch import ClipWatch
from custom_components.sentient.clips import Clips
from custom_components.sentient.rules import Rules, safe_actions
from custom_components.sentient.shortcuts import Shortcuts, Shortcut
from custom_components.sentient.speech import SpeechRouter


@pytest.mark.parametrize('kind', ['clips', 'clipwatch'])
@pytest.mark.parametrize('oversized', [False, True])
async def test_media_reads_until_eof_and_enforces_limit(hass, tmp_path, kind, oversized):
    hass.config.media_dirs = {'local': str(tmp_path)}
    stream = StreamReader(Mock(_reading_paused=False), limit=65536)
    stream.feed_data(b'first')
    response = MagicMock(status=200, content=stream)
    response.__aenter__.return_value = response
    session = Mock()
    session.get.return_value = response
    module = f'custom_components.sentient.{kind}'
    if kind == 'clips':
        obj = Clips(hass, {}, lambda: None)
        operation = lambda: obj.async_save_clip('event1', 'camera.gate')
        url_patch = patch.object(obj, 'frigate_url', return_value='http://frigate')
        filename = 'clip_gate.mp4'
    else:
        obj = ClipWatch(hass, 'test', {}, lambda: None)
        operation = lambda: obj._download('/clip.mp4', 'clipwatch_gate.mp4')
        url_patch = patch(f'{module}.frigate_url', return_value='http://frigate')
        filename = 'clipwatch_gate.mp4'
    with url_patch, patch(f'{module}.async_get_clientsession', return_value=session), patch(f'{module}.MAX_BYTES', 12):
        task = asyncio.create_task(operation())
        await asyncio.sleep(0)
        try:
            assert not task.done(), 'Must wait for the rest of the response'
        finally:
            stream.feed_data(b'0123456789' if oversized else b'second')
            stream.feed_eof()
            result = await task
    path = tmp_path / 'sentient' / filename
    if oversized:
        assert result is None and not path.exists()
    else:
        assert result.endswith(filename)
        assert path.read_bytes() == b'firstsecond'
    response.__aexit__.assert_awaited_once()


async def test_missing_clip_is_retried_as_original_codec(hass):
    obj = ClipWatch(hass, 'test', {}, lambda: None)
    with patch.object(obj, '_download', AsyncMock(side_effect=[None, 'media-source://good'])) as download:
        assert await obj._video('gate', 1, 10, 'test.mp4') is None
        assert 'gate' not in obj.hevc
        assert await obj._video('gate', 1, 10, 'test.mp4') == 'media-source://good'
        assert all(c.args[0].endswith('/clip.mp4') for c in download.await_args_list)


@pytest.mark.parametrize('name', ['', '!!!', 'good', 'night'])
async def test_routine_lookup_rejects_empty_partial_or_ambiguous(hass, name):
    obj = Shortcuts(hass, 'test')
    obj.items = {'one': Shortcut(name='Good night'), 'two': Shortcut(name='Good morning')}
    # "night" uniquely identifies Good night and is allowed; make it ambiguous.
    obj.items['three'] = Shortcut(name='Night lights')
    assert obj.find(name) is None


async def test_routine_lookup_keeps_exact_alias_and_unambiguous_phrase(hass):
    obj = Shortcuts(hass, 'test')
    one = Shortcut(name='Goodnight', aliases='bedtime')
    obj.items[one.id] = one
    assert obj.find('goodnight routine') is one
    assert obj.find('bedtime') is one
    assert obj.find(one.id) is one
    assert (await obj.async_run(''))['error']
    assert one.last_run is None
    with pytest.raises(ValueError):
        obj.upsert({'name': '!!!'})
    with pytest.raises(ValueError):
        obj.upsert({'name': 'Evening', 'aliases': 'bedtime'})


BAD = {'action': 'lock.unlock', 'target': {'entity_id': 'lock.front_door'}}
@pytest.mark.parametrize('action', [
    BAD,
    {'if': [{'condition': 'template', 'value_template': '{{ true }}'}], 'then': [BAD]},
    {'if': [], 'then': [], 'else': [BAD]},
    {'choose': [{'conditions': [], 'sequence': [BAD]}]},
    {'choose': [], 'default': [BAD]},
    {'repeat': {'count': 2, 'sequence': [BAD]}},
    {'parallel': [{'sequence': [BAD]}]},
    {'action': 'script.unlock_house'},
    {'action': 'scene.turn_on'},
    {'action': 'sentient.run_routine'},
    {'action': '{{ unsafe_service }}'},
    {'device_id': 'device', 'domain': 'lock', 'type': 'unlock'},
    {'event': 'run_unrestricted_action'},
])
def test_rule_restrictions_cover_nested_and_indirect_actions(action):
    assert safe_actions([action]) is not None


def test_rule_restrictions_allow_nested_explicit_lighting():
    assert safe_actions([{'choose': [{'conditions': [], 'sequence': [
        {'repeat': {'count': 2, 'sequence': [{'action': 'light.turn_on', 'target': {'entity_id': 'light.kitchen'}}]}}
    ]}], 'default': [{'delay': '00:00:01'}]}]) is None


async def test_existing_unsafe_rule_is_not_executed(hass):
    rt = SimpleNamespace(timeline=Mock())
    rules = Rules(hass, Mock(), lambda: rt)
    with patch('custom_components.sentient.shortcuts.async_run_actions', new_callable=AsyncMock) as run:
        await rules.async_run({'id': 'old', 'name': 'Old rule', 'actions': [{'sequence': [BAD]}]})
        run.assert_not_awaited()
    rt.timeline.add.assert_not_called()


@pytest.mark.parametrize('ask', [False, True])
async def test_queued_duplicate_spoken_once(hass, ask):
    hass.states.async_set('assist_satellite.kitchen', 'idle', {'supported_features': 3})
    speech = SpeechRouter(hass, {'quiet_start': '00:00:00', 'quiet_end': '00:00:00'})
    service = 'start_conversation' if ask else 'announce'
    said = async_mock_service(hass, 'assist_satellite', service)
    method = speech.async_ask if ask else speech.async_say
    await speech._lock.acquire()
    tasks = [asyncio.create_task(method('Someone is at the gate.', targets=['assist_satellite.kitchen'])) for _ in range(2)]
    await asyncio.sleep(0)
    speech._lock.release()
    results = await asyncio.gather(*tasks)
    assert len(said) == 1
    assert [r.spoken for r in results] == [True, False]
    assert results[1].reason == 'duplicate'


async def test_failed_speech_can_be_retried_and_critical_repeats(hass):
    hass.states.async_set('assist_satellite.kitchen', 'idle')
    speech = SpeechRouter(hass, {'quiet_start': '00:00:00', 'quiet_end': '00:00:00'})
    with patch.object(speech, '_speak', AsyncMock(side_effect=[HomeAssistantError('offline'), None, None])) as speak:
        with pytest.raises(HomeAssistantError):
            await speech.async_say('Alert', 'urgent', ['assist_satellite.kitchen'], source='critical')
        assert 'alert' not in speech._recent
        for _ in range(2):
            assert (await speech.async_say('Alert', 'urgent', ['assist_satellite.kitchen'], source='critical')).spoken
        assert speak.await_count == 3


async def test_rules_editor_rejects_unsafe_nested_action(hass, hass_ws_client):
    from tests.test_phase3 import _setup

    entry = await _setup(hass, rules=True)
    ws = await hass_ws_client(hass)
    await ws.send_json({'id': 1, 'type': 'sentient/items/save', 'kind': 'rules',
                       'item': {'name': 'Unsafe', 'when_entity': 'binary_sensor.door',
                                'actions': [{'repeat': {'count': 1, 'sequence': [BAD]}}]}})
    response = await ws.receive_json()
    assert not response['success']
    assert response['error']['code'] == 'invalid'
    assert not entry.runtime_data.lists['rules'].listing()
