"""Retries, image checks and saved offer answers must belong to current work."""
import asyncio
from datetime import timedelta
from types import SimpleNamespace
from unittest.mock import AsyncMock, Mock, patch
import pytest
from homeassistant.core import State, SupportsResponse
from homeassistant.exceptions import HomeAssistantError
from homeassistant.util import dt as dt_util
from custom_components.sentient.ai import AIGate, AIUnavailable
from custom_components.sentient.checks import CameraChecks, triggered
from custom_components.sentient.items import Items
from custom_components.sentient.offers import Offers


def ai(hass, options=None):
    gate = AIGate(hass, 'lifecycle', options or {})
    gate._save = Mock()
    return gate


async def test_retry_is_another_service_attempt_not_another_budget_job(hass):
    gate = ai(hass)
    calls = 0
    async def generate(call):
        nonlocal calls
        calls += 1
        if calls == 1: raise HomeAssistantError('503 high demand')
        return {'data': 'ready'}
    hass.services.async_register('ai_task', 'generate_data', generate, supports_response=SupportsResponse.ONLY)
    assert gate.spend('noticing')
    with patch('custom_components.sentient.ai.RETRY_AFTER', 0):
        await gate.async_call({'task_name': 'remark'})
    assert gate.used == 1 and gate.service_attempts == 2 and gate.retry_attempts == 1
    assert len(gate.sent) == 2


@pytest.mark.parametrize('change', ['privacy', 'settings'])
async def test_changed_policy_cancels_retry_without_new_health_failure(hass, change):
    gate = ai(hass)
    calls = 0
    async def generate(call):
        nonlocal calls
        calls += 1
        raise HomeAssistantError('503 high demand')
    async def changed_delay(*args):
        if change == 'privacy': gate.forget_sent()
        else:
            gate.invalidate_requests()
            gate.options = {'ai_task_entity': 'ai_task.new'}
    hass.services.async_register('ai_task', 'generate_data', generate, supports_response=SupportsResponse.ONLY)
    with patch('custom_components.sentient.ai.asyncio.sleep', side_effect=changed_delay):
        with pytest.raises(AIUnavailable): await gate.async_call({'task_name': 'remark'})
    assert calls == 1 and gate.retry_attempts == 0 and gate.fails_in_row == 0
    if change == 'privacy': assert not gate.sent


@pytest.mark.parametrize('old_error', [True, False])
async def test_old_request_does_not_overwrite_current_provider_health(hass, old_error):
    gate = ai(hass)
    entered, release = asyncio.Event(), asyncio.Event()
    async def generate(call):
        entered.set()
        await release.wait()
        if old_error: raise HomeAssistantError('400 old provider error')
        return {'data': 'old success'}
    hass.services.async_register('ai_task', 'generate_data', generate, supports_response=SupportsResponse.ONLY)
    task = asyncio.create_task(gate.async_call({'task_name': 'old'}))
    await entered.wait()
    gate.invalidate_requests()
    gate.options = {'ai_task_entity': 'ai_task.new'}
    gate.failed(HomeAssistantError('new provider error'))
    release.set()
    if old_error:
        with pytest.raises(AIUnavailable): await task
    else: await task
    assert gate.fails_in_row == 1 and gate.last_error == 'new provider error'
    assert gate.last_ok_at is None


@pytest.mark.parametrize('payload', ['invalid', [], 17])
async def test_malformed_ai_service_response_uses_gateway_failure(hass, payload):
    gate = ai(hass)
    async def generate(call): return payload
    hass.services.async_register('ai_task', 'generate_data', generate, supports_response=SupportsResponse.ONLY)
    with pytest.raises(AIUnavailable, match='invalid'):
        await gate.async_call({'task_name': 'test'})
    assert gate.service_attempts == 1 and gate.fails_in_row == 1


async def test_attempt_counters_roll_at_midnight(hass, freezer):
    gate = ai(hass)
    gate.service_attempts, gate.retry_attempts = 7, 2
    freezer.tick(timedelta(days=1))
    assert gate.describe()['service_attempts_today'] == 0
    assert gate.retry_attempts == 0


@pytest.mark.parametrize('keeping', [True, False])
async def test_restored_sent_records_respect_retention_and_toggle(hass, keeping):
    gate = ai(hass, {'privacy_log': keeping})
    data = {'day': gate.day, 'service_attempts': 4, 'retry_attempts': 1, 'sent': [
        {'time': dt_util.utcnow().isoformat(), 'feature': 'recent'},
        {'time': (dt_util.utcnow() - timedelta(days=15)).isoformat(), 'feature': 'old'}]}
    gate._store = SimpleNamespace(async_load=AsyncMock(return_value=data))
    await gate.async_start()
    assert len(gate.sent) == (1 if keeping else 0)
    assert gate.service_attempts == 4 and gate.retry_attempts == 1
    gate._save.assert_called()


def check_engine(hass):
    rt = SimpleNamespace(options={}, speech=SimpleNamespace(_privacy_generation=0, guest_mode=False, async_say=AsyncMock()),
        timeline=Mock(), watches=SimpleNamespace(unchanged=AsyncMock(return_value=False),
        _attachment=AsyncMock(return_value={'media_content_id': 'camera', 'media_content_type': 'image/jpeg'}), spend=Mock(return_value=True)))
    store = Items(hass, 'lifecycle', 'checks', {})
    item = store.upsert({'name': 'Van', 'enabled': True, 'camera': 'camera.front', 'question': 'A van?', 'say': True})
    hass.states.async_set('camera.front', 'idle')
    return CameraChecks(hass, store, lambda: rt), item, rt


async def test_camera_reserves_busy_before_prefilter_await(hass):
    obj, item, rt = check_engine(hass)
    entered, release = asyncio.Event(), asyncio.Event()
    async def unchanged(*args):
        entered.set()
        await release.wait()
        return False
    rt.watches.unchanged.side_effect = unchanged
    with patch('custom_components.sentient.checks.async_generate', new=AsyncMock(return_value={'data': {'match': False}})) as generate:
        first = asyncio.create_task(obj.async_check(item))
        await entered.wait()
        assert 'skipped' in await obj.async_check(item)
        release.set()
        await first
    assert generate.await_count == 1 and rt.watches.spend.call_count == 1
    assert not obj._busy


@pytest.mark.parametrize('changed', ['disabled', 'edited', 'deleted', 'stopped', 'guest', 'forgotten', 'settings', 'camera_removed'])
async def test_late_camera_result_is_not_published(hass, changed):
    obj, item, rt = check_engine(hass)
    async def generate(*args):
        if changed == 'disabled': item['enabled'] = False
        if changed == 'edited': obj.items.upsert({**item, 'question': 'A different question?'})
        if changed == 'deleted': obj.items.remove(item['id'])
        if changed == 'stopped': obj.async_stop()
        if changed == 'guest': rt.speech.guest_mode = True
        if changed == 'forgotten': rt.speech._privacy_generation += 1
        if changed == 'settings': rt.options = {'ai_task_entity': 'ai_task.new'}
        if changed == 'camera_removed': hass.states.async_remove('camera.front')
        return {'data': {'match': True, 'description': 'A van'}}
    with patch('custom_components.sentient.checks.async_generate', side_effect=generate):
        result = await obj.async_check(item)
    assert 'skipped' in result
    rt.timeline.add.assert_not_called()
    rt.speech.async_say.assert_not_awaited()
    assert not obj._busy


@pytest.mark.parametrize('data', [{'match': 'false'}, {'match': 'true'}, {'match': 1}, {'match': True, 'description': 5}, []])
async def test_malformed_camera_result_is_unknown_not_an_alert(hass, data):
    obj, item, rt = check_engine(hass)
    with patch('custom_components.sentient.checks.async_generate', new=AsyncMock(return_value={'data': data})):
        result = await obj.async_check(item)
    assert result['match'] is None and item['last_answer'].startswith('unknown:')
    rt.timeline.add.assert_not_called()
    rt.speech.async_say.assert_not_awaited()


async def test_failed_camera_preparation_does_not_spend_ai_budget(hass):
    obj, item, rt = check_engine(hass)
    rt.watches._attachment.side_effect = HomeAssistantError('camera asleep')
    with patch('custom_components.sentient.checks.async_generate', new=AsyncMock()) as generate:
        assert 'error' in await obj.async_check(item)
    rt.watches.spend.assert_not_called()
    generate.assert_not_awaited()
    assert not obj._busy


@pytest.mark.parametrize('value', ['nan', 'inf', '-inf'])
def test_non_finite_object_count_does_not_trigger_camera(value):
    assert not triggered(State('sensor.car_count', value), State('sensor.car_count', '1'))


def offer_engine(hass):
    speech = SimpleNamespace(guest_mode=False, _privacy_generation=0, async_ask=AsyncMock(return_value=SimpleNamespace(as_dict=lambda: {})))
    rt = SimpleNamespace(options={}, speech=speech, timeline=Mock(), feedback=Mock())
    items = Items(hass, 'lifecycle', 'offers', {})
    item = items.upsert({'name': 'Kettle', 'enabled': True, 'questions': 'Kettle on?',
                         'actions': [{'action': 'switch.turn_on', 'target': {'entity_id': 'switch.kettle'}}]})
    return Offers(hass, items, lambda: rt), item, rt


@pytest.mark.parametrize('changed', ['edited', 'mutated', 'deleted', 'disabled', 'guest', 'settings', 'privacy', 'stopped', 'expired'])
async def test_saved_offer_answer_cannot_execute_changed_actions(hass, freezer, changed):
    obj, item, rt = offer_engine(hass)
    await obj.async_ask(item)
    answer = rt.speech.async_ask.call_args.kwargs['on_answer']
    if changed == 'edited': obj.items.upsert({**item, 'actions': [{'action': 'switch.turn_off'}]})
    if changed == 'mutated': item['actions'][0]['target']['entity_id'] = 'switch.other'
    if changed == 'deleted': obj.items.remove(item['id'])
    if changed == 'disabled': item['enabled'] = False
    if changed == 'guest': rt.speech.guest_mode = True
    if changed == 'settings': rt.options = {'new': True}
    if changed == 'privacy': rt.speech._privacy_generation += 1
    if changed == 'stopped': obj.async_stop()
    if changed == 'expired': freezer.tick(timedelta(minutes=3))
    with patch('custom_components.sentient.offers.async_run_actions', new=AsyncMock()) as actions:
        assert 'no longer awaiting' in await answer('yes')
    actions.assert_not_awaited()


async def test_offer_answer_is_consumed_once(hass):
    obj, item, rt = offer_engine(hass)
    await obj.async_ask(item)
    answer = rt.speech.async_ask.call_args.kwargs['on_answer']
    with patch('custom_components.sentient.offers.async_run_actions', new=AsyncMock()) as actions:
        assert await answer('yes') == 'Request sent.'
        assert 'no longer awaiting' in await answer('yes')
    assert actions.await_count == 1


async def test_offer_rechecks_after_async_action_validation(hass):
    obj, item, rt = offer_engine(hass)
    async def validate(hass, config):
        item['enabled'] = False
        return config
    with patch('custom_components.sentient.shortcuts.async_validate_actions_config', side_effect=validate), patch('custom_components.sentient.shortcuts.Script') as script:
        result = await obj.async_accept(item['id'], True)
    assert 'error' in result
    script.assert_not_called()


async def test_never_touch_offer_is_not_reported_done(hass):
    obj, item, rt = offer_engine(hass)
    with patch('custom_components.sentient.never.never_list', return_value={'switch.kettle'}), patch('custom_components.sentient.offers.async_run_actions', new=AsyncMock()) as actions:
        assert 'error' in await obj.async_accept(item['id'], True)
    actions.assert_not_awaited()


async def test_changing_configured_provider_clears_old_health_only(hass):
    from tests.test_deep_test_fixes import _setup
    entry = await _setup(hass)
    gate = entry.runtime_data.ai
    gate.spend('noticing')
    for _ in range(3): gate.failed(HomeAssistantError('old provider offline'))
    assert gate.failing
    hass.config_entries.async_update_entry(entry, options={**entry.options, 'ai_task_entity': 'ai_task.replacement'})
    await hass.async_block_till_done()
    assert not gate.failing and gate.last_error is None and gate.last_ok_at is None
    assert gate.used == 1 and gate.failed_today == 3


async def test_empty_offer_is_not_reported_as_an_action(hass):
    obj, item, rt = offer_engine(hass)
    item['actions'] = []
    assert 'no configured actions' in (await obj.async_accept(item['id'], True))['error']


async def test_disabled_offer_cannot_be_accepted_by_voice_tool(hass):
    obj, item, rt = offer_engine(hass)
    item['enabled'] = False
    with patch('custom_components.sentient.offers.async_run_actions', new=AsyncMock()) as actions:
        assert 'error' in await obj.async_accept(item['id'], True)
    actions.assert_not_awaited()


async def test_explicit_offer_test_retains_disabled_rule_override(hass):
    obj, item, rt = offer_engine(hass)
    item['enabled'] = False
    await obj.async_ask(item, force=True)
    answer = rt.speech.async_ask.call_args.kwargs['on_answer']
    with patch('custom_components.sentient.offers.async_run_actions', new=AsyncMock()) as actions:
        assert await answer('yes') == 'Request sent.'
    actions.assert_awaited_once()


async def test_guest_camera_check_never_prepares_or_spends(hass):
    obj, item, rt = check_engine(hass)
    rt.speech.guest_mode = True
    assert 'skipped' in await obj.async_check(item)
    rt.watches._attachment.assert_not_awaited()
    rt.watches.spend.assert_not_called()


async def test_forgotten_offer_does_not_publish_late_completion(hass):
    obj, item, rt = offer_engine(hass)
    async def run(*args, **kwargs): rt.speech._privacy_generation += 1
    with patch('custom_components.sentient.offers.async_run_actions', side_effect=run):
        result = await obj.async_accept(item['id'], True)
    assert result['done'] is None and 'Actions were started' in result['error']
    rt.timeline.add.assert_not_called()


async def test_edited_camera_question_is_evaluated_even_if_frame_is_identical(hass):
    from tests.test_phase1 import _setup
    hass.states.async_set('camera.front', 'idle')
    entry = await _setup(hass)
    rt = entry.runtime_data
    rt.spots.async_fingerprint = AsyncMock(return_value=[10] * 300 + [200] * 468)
    item = rt.lists['checks'].upsert({'name': 'Vehicle', 'camera': 'camera.front', 'question': 'Is there a van?', 'cooldown_minutes': 0})
    with patch('custom_components.sentient.checks.async_generate', new=AsyncMock(side_effect=[{'data': {'match': False}}, {'data': {'match': True}}])) as generate:
        assert (await rt.checks.async_check(item))['match'] is False
        assert 'skipped' in await rt.checks.async_check(item)
        item = rt.lists['checks'].upsert({**item, 'question': 'Is there a car?'})
        assert (await rt.checks.async_check(item))['match'] is True
    assert generate.await_count == 2


async def test_failed_camera_evaluation_does_not_poison_unchanged_cache(hass):
    from tests.test_phase1 import _setup
    hass.states.async_set('camera.front', 'idle')
    entry = await _setup(hass)
    rt = entry.runtime_data
    rt.spots.async_fingerprint = AsyncMock(return_value=[10] * 300 + [200] * 468)
    item = rt.lists['checks'].upsert({'name': 'Vehicle', 'camera': 'camera.front', 'question': 'Is there a van?', 'cooldown_minutes': 0})
    with patch('custom_components.sentient.checks.async_generate', new=AsyncMock(side_effect=[AIUnavailable('offline'), {'data': {'match': True}}])) as generate:
        assert 'error' in await rt.checks.async_check(item)
        assert (await rt.checks.async_check(item))['match'] is True
    assert generate.await_count == 2


async def test_forgetting_clears_cached_and_recorded_camera_answers(hass):
    from tests.test_phase1 import _setup
    from custom_components.sentient.privacy import forget_recent
    from custom_components.sentient.watches import Watch
    entry = await _setup(hass)
    rt = entry.runtime_data
    now = dt_util.utcnow()
    rt.watches._looks[('camera.front', '', 'question')] = (now, {'answer': 'Private image description'})
    rt.watches._prints['check:old'] = [1, 2, 3]
    watch = Watch('Watch', 'camera', 'camera.front', (now + timedelta(hours=1)).isoformat(),
                  last_checked=now.isoformat(), last_result='Private observation')
    rt.watches._watches[watch.id] = watch
    item = rt.lists['checks'].upsert({'name': 'Vehicle', 'camera': 'camera.front', 'last': now.isoformat(), 'last_answer': 'Private description'})
    forget_recent(rt)
    assert not rt.watches._looks and not rt.watches._prints
    assert watch.last_result is None and watch.last_checked is None
    assert item['last_answer'] is None


async def test_camera_budget_block_skips_image_preparation(hass):
    obj, item, rt = check_engine(hass)
    gate = ai(hass, {'daily_ai_limit': 1})
    gate.spend('other')
    rt.watches.gate = gate
    assert 'skipped' in await obj.async_check(item)
    rt.watches.unchanged.assert_not_awaited()
    rt.watches._attachment.assert_not_awaited()
    rt.watches.spend.assert_not_called()


async def test_direct_feature_budget_query_rolls_after_midnight(hass, freezer):
    gate = ai(hass, {'budget_cameras': 1})
    assert gate.spend('camera checks')
    assert gate.over_budget('camera checks')
    freezer.tick(timedelta(days=1))
    assert gate.over_budget('camera checks') is None
    assert gate.budgets()['cameras']['used'] == 0


async def test_late_on_demand_look_cannot_repopulate_forgotten_cache(hass):
    from tests.test_phase1 import _setup
    from custom_components.sentient.privacy import forget_recent
    hass.states.async_set('camera.front', 'idle')
    entry = await _setup(hass)
    rt = entry.runtime_data
    async def generate(*args):
        forget_recent(rt)
        return {'data': 'Private old description'}
    with patch('custom_components.sentient.watches.async_generate', side_effect=generate):
        with pytest.raises(HomeAssistantError, match='invalidated'):
            await rt.watches.async_look('camera.front', 'What is there?')
    assert not rt.watches._looks


async def test_late_fingerprint_cannot_repopulate_cleared_cache(hass):
    from tests.test_phase1 import _setup
    entry = await _setup(hass)
    watches = entry.runtime_data.watches
    async def fingerprint(*args, **kwargs):
        watches.clear_camera_cache()
        return [10] * 300 + [200] * 468
    watches.spots.async_fingerprint = fingerprint
    assert await watches.unchanged('check:test', 'camera.front', None) is False
    assert not watches._prints


@pytest.mark.parametrize('changed', ['forgotten', 'expired', 'stopped'])
async def test_late_camera_watch_result_does_not_fire(hass, changed):
    from tests.test_phase1 import _setup
    from custom_components.sentient.watches import Watch
    from custom_components.sentient.privacy import forget_recent
    entry = await _setup(hass)
    rt = entry.runtime_data
    watch = Watch('Watch', 'camera', 'camera.front', (dt_util.utcnow() + timedelta(hours=1)).isoformat())
    rt.watches._watches[watch.id] = watch
    async def ask(*args):
        if changed == 'forgotten': forget_recent(rt)
        if changed == 'expired': watch.until = (dt_util.utcnow() - timedelta(seconds=1)).isoformat()
        if changed == 'stopped': rt.watches.async_stop()
        return True, 'Private old description', False
    with patch('custom_components.sentient.watches.asyncio.sleep', new=AsyncMock()), patch.object(rt.watches, 'unchanged', new=AsyncMock(return_value=False)), patch.object(rt.watches, '_async_ask', side_effect=ask), patch.object(rt.watches, '_async_fire', new=AsyncMock()) as fire:
        await rt.watches._async_check_camera(watch)
    fire.assert_not_awaited()
    assert watch.last_result is None
    assert not rt.watches._busy


async def test_forgotten_camera_watch_does_not_send_prepared_image(hass):
    from tests.test_phase1 import _setup
    from custom_components.sentient.watches import Watch
    from custom_components.sentient.privacy import forget_recent
    entry = await _setup(hass)
    rt = entry.runtime_data
    watch = Watch('Watch', 'camera', 'camera.front', (dt_util.utcnow() + timedelta(hours=1)).isoformat())
    rt.watches._watches[watch.id] = watch
    async def attachment(*args):
        forget_recent(rt)
        return {'media_content_id': 'camera', 'media_content_type': 'image/jpeg'}
    with patch.object(rt.watches, '_attachment', side_effect=attachment), patch('custom_components.sentient.watches.async_generate', new=AsyncMock()) as generate:
        with pytest.raises(HomeAssistantError, match='no AI request was sent'):
            await rt.watches._async_ask(watch)
    generate.assert_not_awaited()
