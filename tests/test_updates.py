"""Optional release checks stay bounded, cached and read-only."""
import asyncio
from unittest.mock import patch
import pytest
from custom_components.sentient.updates import UpdateChecker, RELEASE_URL, version_tuple


async def test_disabled_checker_never_contacts_github(hass, aioclient_mock):
    checker = UpdateChecker(hass)
    checker.installed = '0.67.0'
    checker.configure({})
    assert not (await checker.async_check())['enabled']
    assert aioclient_mock.call_count == 0


@pytest.mark.parametrize('tag,available', [('v0.68.0', True), ('v0.67.0', False), ('v0.66.9', False), ('v0.100.0', True)])
async def test_release_comparison_and_manual_cache(hass, aioclient_mock, tag, available):
    aioclient_mock.get(RELEASE_URL, json={'tag_name': tag, 'draft': False, 'prerelease': False})
    checker = UpdateChecker(hass)
    checker.installed = '0.67.0'
    checker.configure({'update_checker': True})
    out = await checker.async_check()
    assert out['update_available'] is available
    assert out['last_checked'] and not out['checking']
    await checker.async_check()
    assert aioclient_mock.call_count == 1
    checker.stop()
    assert checker.status()['latest_version'] is None


@pytest.mark.parametrize('payload', [{'tag_name': 'garbage'}, {'tag_name': 'v0.68.0-beta1'}, {'tag_name': 'v0.68.0', 'prerelease': True}])
async def test_bad_release_is_not_reported_up_to_date(hass, aioclient_mock, payload):
    aioclient_mock.get(RELEASE_URL, json=payload)
    checker = UpdateChecker(hass)
    checker.installed = '0.67.0'
    checker.configure({'update_checker': True})
    out = await checker.async_check()
    assert out['error'] and out['update_available'] is None
    checker.stop()


async def test_rate_limit_backoff(hass, aioclient_mock):
    aioclient_mock.get(RELEASE_URL, status=403)
    checker = UpdateChecker(hass)
    checker.installed = '0.67.0'
    checker.configure({'update_checker': True})
    out = await checker.async_check()
    assert 'one hour' in out['error']
    assert checker._next_check > asyncio.get_running_loop().time() + 3500
    await checker.async_check()
    assert aioclient_mock.call_count == 1
    checker.stop()


async def test_disabling_cancels_inflight_check_and_timer(hass):
    checker = UpdateChecker(hass)
    entered = asyncio.Event()
    async def stall(generation):
        entered.set()
        await asyncio.Event().wait()
    with patch.object(checker, '_fetch', side_effect=stall):
        checker.configure({'update_checker': True})
        await entered.wait()
        task = checker._task
        checker.configure({'update_checker': False})
        await asyncio.sleep(0)
    assert task.cancelled()
    assert checker._unsub is None and not checker.status()['enabled']


async def test_settings_and_check_endpoint(hass, hass_ws_client, aioclient_mock):
    from tests.test_websocket import _setup
    aioclient_mock.get(RELEASE_URL, json={'tag_name': 'v99.0.0'})
    entry = await _setup(hass)
    ws = await hass_ws_client(hass)
    await ws.send_json({'id': 1, 'type': 'sentient/update_check'})
    assert (await ws.receive_json())['error']['code'] == 'disabled'
    await ws.send_json({'id': 2, 'type': 'sentient/settings/save', 'group': 'updates', 'data': {'update_checker': True}})
    assert (await ws.receive_json())['success']
    await hass.async_block_till_done()
    await ws.send_json({'id': 3, 'type': 'sentient/update_check'})
    out = (await ws.receive_json())['result']
    assert out['update_available'] is True and out['installed_version']
    await ws.send_json({'id': 4, 'type': 'sentient/settings/save', 'group': 'updates', 'data': {'update_checker': False}})
    assert (await ws.receive_json())['success']
    await hass.async_block_till_done()
    assert not entry.runtime_data.updates.enabled
