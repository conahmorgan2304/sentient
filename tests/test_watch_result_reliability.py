"""Uncertain, failed and queued watches must not become false observations."""
import asyncio
from datetime import timedelta
from types import SimpleNamespace
from unittest.mock import AsyncMock, Mock, patch

import pytest
from homeassistant.core import State
from homeassistant.exceptions import HomeAssistantError
from homeassistant.util import dt as dt_util

from custom_components.sentient.ai import AIGate
from custom_components.sentient.watches import Watch, WatchManager


@pytest.fixture
def manager(hass):
    speech = SimpleNamespace(async_say=AsyncMock(), find_speaker=Mock())
    obj = WatchManager(hass, 'watch-results', {'camera_cooldown': 0}, speech)
    obj._save = Mock()
    obj.gate = AIGate(hass, 'watch-results', obj.options)
    obj.gate._save = Mock()
    obj.spots = SimpleNamespace(async_fingerprint=AsyncMock(return_value=[30] * 768),
        async_attachment=AsyncMock(return_value={'media_content_id': 'image', 'media_content_type': 'image/jpeg'}))
    hass.states.async_set('camera.test', 'idle')
    return obj


def watch_for(manager, kind='camera', repeat=True):
    watch = Watch('Test watch', kind, 'camera.test' if kind == 'camera' else 'sensor.test',
        (dt_util.utcnow() + timedelta(hours=1)).isoformat(), question='Is someone there?', repeat=repeat)
    manager._watches[watch.id] = watch
    return watch


async def check(manager, watch, payload):
    with patch('custom_components.sentient.watches.asyncio.sleep', new=AsyncMock()), patch(
        'custom_components.sentient.watches.async_generate', new=AsyncMock(return_value={'data': payload})) as ai:
        await manager._async_check_camera(watch)
    return ai


@pytest.mark.parametrize('payload', [None, [], 1, {}, {'answer': 'false'}, {'answer': 'true'},
    {'answer': 1}, {'answer': []}, {'answer': True, 'description': 42},
    {'answer': False, 'unsure': 'false'}, {'answer': True, 'unsure': None},
    'yes, but the image is unclear', 'true-ish', 'yesterday there was someone'])
async def test_invalid_camera_answers_stay_unknown_and_allow_retry(manager, payload):
    watch = watch_for(manager)
    await check(manager, watch, payload)
    assert watch.last_result.startswith('error: Invalid camera AI result')
    assert not watch.fired and not manager.speech.async_say.await_count
    assert not manager._prints and not manager._busy
    await check(manager, watch, {'answer': True, 'description': 'Someone is there.'})
    assert watch.fired == 1 and watch.checks == 2
    manager.speech.async_say.assert_awaited_once()


@pytest.mark.parametrize('second', ['none', 'same', 'capped', 'unsure', 'yes', 'no'])
async def test_uncertain_camera_result_needs_a_certain_second_opinion(manager, second):
    watch = watch_for(manager)
    manager.options['ai_task_entity'] = 'ai_task.small'
    if second != 'none':
        manager.options['second_opinion_ai'] = 'ai_task.small' if second == 'same' else 'ai_task.big'
    if second == 'capped':
        manager.options['daily_ai_limit'] = 1
    answers = [{'data': {'answer': True, 'description': 'Maybe someone.', 'unsure': True}},
               {'data': {'answer': second != 'no', 'unsure': second == 'unsure', 'description': 'Second look.'}}]
    with patch('custom_components.sentient.watches.asyncio.sleep', new=AsyncMock()), patch(
        'custom_components.sentient.watches.async_generate', new=AsyncMock(side_effect=answers)) as ai:
        await manager._async_check_camera(watch)
    assert bool(watch.fired) == (second == 'yes')
    assert ai.await_count == (2 if second in ('yes', 'no', 'unsure') else 1)
    # Reuse the prepared attachment reference; no second image preparation.
    manager.spots.async_attachment.assert_awaited_once()
    assert bool(manager._prints) == (second in ('yes', 'no'))
    if second not in ('yes', 'no'):
        assert watch.last_result == 'unknown: the picture is unclear'


@pytest.mark.parametrize('payload,expected', [('yes', True), ('TRUE.', True), ('No!', False),
    (' false ', False), ({'answer': False}, False), ({'answer': True, 'description': None}, True)])
async def test_unambiguous_legacy_answers_and_typed_results(manager, payload, expected):
    watch = watch_for(manager)
    await check(manager, watch, payload)
    assert bool(watch.fired) == expected
    assert manager._prints  # a valid evaluated image can be skipped next time


async def test_failed_camera_preparation_does_not_spend_and_can_retry(manager):
    watch = watch_for(manager)
    manager.spots.async_attachment.side_effect = HomeAssistantError('Camera is asleep')
    ai = await check(manager, watch, {'answer': True})
    ai.assert_not_awaited()
    assert manager.gate.used == 0 and watch.checks == 0
    assert not manager._prints and not manager._busy
    manager.spots.async_attachment.side_effect = None
    await check(manager, watch, {'answer': False})
    assert manager.gate.used == 1 and watch.checks == 1


async def test_budget_blocked_camera_does_no_image_work(manager):
    watch = watch_for(manager)
    manager.options['daily_ai_limit'] = 1
    manager.gate.counts = {'other': 1}
    await check(manager, watch, {'answer': True})
    manager.spots.async_fingerprint.assert_not_awaited()
    manager.spots.async_attachment.assert_not_awaited()
    assert watch.last_result == 'skipped: daily AI limit reached'


async def test_budget_consumed_during_preparation_does_not_cache_unevaluated_image(manager):
    watch = watch_for(manager)
    async def prepare(*args):
        manager.options['daily_ai_limit'] = 1
        manager.gate.counts = {'other': 1}
        return {'media_content_id': 'image', 'media_content_type': 'image/jpeg'}
    manager.spots.async_attachment.side_effect = prepare
    ai = await check(manager, watch, {'answer': True})
    ai.assert_not_awaited()
    assert not manager._prints and manager.gate.used == 1


async def test_failed_provider_does_not_poison_unchanged_frame(manager):
    watch = watch_for(manager)
    with patch('custom_components.sentient.watches.asyncio.sleep', new=AsyncMock()), patch(
        'custom_components.sentient.watches.async_generate', new=AsyncMock(side_effect=HomeAssistantError('offline'))):
        await manager._async_check_camera(watch)
    assert not manager._prints and not manager._busy
    await check(manager, watch, {'answer': True})
    assert watch.fired == 1


@pytest.mark.parametrize('change', ['expired', 'stopped', 'cancelled', 'replaced'])
async def test_queued_state_watch_rechecks_lifecycle_before_speaking(manager, change):
    watch = watch_for(manager, 'state')
    if change == 'expired':
        watch.until = (dt_util.utcnow() - timedelta(seconds=1)).isoformat()
    elif change == 'stopped':
        manager.async_stop()
    elif change == 'cancelled':
        manager.cancel(watch.id)
    else:
        replacement = watch_for(manager, 'state')
        manager._watches[watch.id] = replacement
    await manager._async_fire(watch, 'Old queued result', State('sensor.test', 'on'))
    assert watch.fired == 0
    manager.speech.async_say.assert_not_awaited()


async def test_due_reminder_still_speaks_after_its_deadline(manager):
    watch = watch_for(manager, 'reminder', repeat=False)
    watch.until = (dt_util.utcnow() - timedelta(seconds=1)).isoformat()
    watch.message = 'Your requested reminder.'
    await manager._async_fire(watch, None, None)
    assert watch.fired == 1 and watch.id not in manager._watches
    manager.speech.async_say.assert_awaited_once()


async def test_cancelled_camera_work_releases_busy_and_unevaluated_fingerprint(manager):
    watch = watch_for(manager)
    entered = asyncio.Event()
    async def pending(*args):
        entered.set()
        await asyncio.Event().wait()
    with patch('custom_components.sentient.watches.asyncio.sleep', new=AsyncMock()), patch(
        'custom_components.sentient.watches.async_generate', side_effect=pending):
        task = asyncio.create_task(manager._async_check_camera(watch))
        await entered.wait()
        task.cancel()
        with pytest.raises(asyncio.CancelledError):
            await task
    assert not manager._busy and not manager._prints
