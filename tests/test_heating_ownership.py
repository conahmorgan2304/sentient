"""Temporary heating may restore only a confirmed, still-owned control state."""
import asyncio
from copy import deepcopy
from types import SimpleNamespace
from datetime import datetime, timedelta
from unittest.mock import AsyncMock, Mock, patch

import pytest
from homeassistant.const import EVENT_CALL_SERVICE, EVENT_STATE_CHANGED
from homeassistant.core import Context, Event
from homeassistant.exceptions import HomeAssistantError
from homeassistant.util import dt as dt_util
from pytest_homeassistant_custom_component.common import async_mock_service

from custom_components.sentient.heating import Heating
from custom_components.sentient.trust import Trust

EID = 'climate.test'
BASE = {'temperature': 16, 'current_temperature': 15, 'preset_mode': 'auto',
        'preset_modes': ['auto', 'manual'], 'min_temp': 5, 'max_temp': 30, 'friendly_name': 'Heating'}


@pytest.fixture
def heating(hass, freezer):
    freezer.move_to(dt_util.as_utc(datetime(2026, 11, 8, 5, 0, tzinfo=dt_util.get_default_time_zone())))
    options = {'preheat': True, 'heating_entities': [EID], 'heating_limit': 2}
    speech = SimpleNamespace(_privacy_generation=0, _guest_generation=0, async_say=AsyncMock(),
        async_ask=AsyncMock(return_value=SimpleNamespace(spoken=True)))
    obj = Heating(hass, 'ownership', options, Mock())
    obj._save = Mock()
    obj.learner = SimpleNamespace(speech=speech)
    obj.trust = Trust(hass, 'ownership', options, lambda: SimpleNamespace())
    rt = SimpleNamespace(speech=speech, timeline=obj.timeline)
    obj.trust._rt = lambda: rt
    obj.trust._save = Mock()
    hass.states.async_set(EID, 'auto', BASE)
    unsubs = [hass.bus.async_listen(EVENT_STATE_CHANGED, obj._state_changed), hass.bus.async_listen(EVENT_CALL_SERVICE, obj._on_call)]
    yield obj
    for unsub in unsubs: unsub()


async def start(obj):
    await obj._async_boost(EID, obj.hass.states.get(EID), 20, 420, dt_util.now())


async def activate(obj, **changes):
    obj.hass.states.async_set(EID, 'auto', {**BASE, 'temperature': 20, 'preset_mode': 'manual', **changes})
    await obj.hass.async_block_till_done()
    return obj._boosted.get(EID)


def manual(obj, service='set_temperature', data=None, context=None):
    obj._on_call(Event(EVENT_CALL_SERVICE, {'domain': 'climate', 'service': service,
        'service_data': {'entity_id': EID, **(data or {'temperature': 21})}}, context=context or Context()))


async def owned(obj):
    async_mock_service(obj.hass, 'climate', 'set_temperature')
    await start(obj)
    boost = await activate(obj)
    assert boost['status'] == 'active'
    boost['until'] = (dt_util.utcnow() - timedelta(seconds=1)).isoformat()
    return boost


@pytest.mark.parametrize('reply', ['no', 'done'])
async def test_unapproved_preheat_has_no_restore_record(heating, reply):
    heating.options['trust_heating'] = 'ask'
    calls = async_mock_service(heating.hass, 'climate', 'set_temperature')
    await start(heating)
    assert not heating._boosted and not calls
    answer = heating.learner.speech.async_ask.call_args.kwargs['on_answer']
    await answer(reply)
    assert not heating._boosted and not calls


async def test_approved_preheat_is_pending_until_observed(heating):
    heating.options['trust_heating'] = 'ask'
    calls = async_mock_service(heating.hass, 'climate', 'set_temperature')
    await start(heating)
    answer = heating.learner.speech.async_ask.call_args.kwargs['on_answer']
    assert await answer('yes') == 'Request sent.'
    assert len(calls) == 1 and heating._boosted[EID]['status'] == 'pending'
    assert heating.report()['warming_now'] == []
    assert heating.report()['pending_requests'] == ['Heating']
    await activate(heating)
    assert heating.report()['warming_now'] == ['Heating']


@pytest.mark.parametrize('change', ['off', 'target', 'preset', 'target_then_back'])
async def test_manual_control_changes_revoke_restore_ownership(heating, change):
    boost = await owned(heating)
    restores = async_mock_service(heating.hass, 'climate', 'set_preset_mode')
    attrs = {**BASE, 'temperature': 20, 'preset_mode': 'manual'}
    state = 'auto'
    if change == 'off': state = 'off'
    if change in ('target', 'target_then_back'): attrs['temperature'] = 19
    if change == 'preset': attrs['preset_mode'] = 'eco'
    heating.hass.states.async_set(EID, state, attrs)
    await heating.hass.async_block_till_done()
    if change == 'target_then_back':
        await activate(heating)
    await heating._async_restore(EID, boost)
    assert not restores and EID not in heating._boosted


async def test_ordinary_room_reading_does_not_revoke_owned_target(heating):
    boost = await owned(heating)
    restores = async_mock_service(heating.hass, 'climate', 'set_preset_mode')
    await activate(heating, current_temperature=19.5)
    await heating._async_restore(EID, boost)
    assert len(restores) == 1 and restores[0].data['preset_mode'] == 'auto'


@pytest.mark.parametrize('change', ['never', 'disabled', 'removed', 'stopped', 'legacy'])
async def test_restore_obeys_current_control_policy(heating, change):
    boost = await owned(heating)
    restores = async_mock_service(heating.hass, 'climate', 'set_preset_mode')
    if change == 'disabled': heating.options['preheat'] = False
    if change == 'removed': heating.options['heating_entities'] = ['climate.other']
    if change == 'stopped': heating.async_stop()
    if change == 'legacy': boost.pop('origin')
    with patch('custom_components.sentient.never.never_list', return_value={EID} if change == 'never' else set()):
        await heating._async_restore(EID, boost)
    assert not restores


async def test_unavailable_zone_can_recover_within_restore_window(heating):
    boost = await owned(heating)
    restores = async_mock_service(heating.hass, 'climate', 'set_preset_mode')
    heating.hass.states.async_set(EID, 'unavailable', {})
    await heating.hass.async_block_till_done()
    await heating._async_restore(EID, boost)
    assert heating._boosted[EID] is boost and not restores
    await activate(heating)
    await heating._async_restore(EID, boost)
    assert len(restores) == 1


async def test_unavailable_zone_is_not_restored_hours_late(heating, freezer):
    boost = await owned(heating)
    restores = async_mock_service(heating.hass, 'climate', 'set_preset_mode')
    freezer.tick(timedelta(hours=2))
    await heating._async_restore(EID, boost)
    assert not restores and not heating._boosted


async def test_failed_restore_is_reported_without_blind_retry(heating):
    boost = await owned(heating)
    calls = 0
    async def fail(call):
        nonlocal calls
        calls += 1
        raise HomeAssistantError('Device did not reply')
    heating.hass.services.async_register('climate', 'set_preset_mode', fail)
    await heating._async_restore(EID, boost)
    await heating._async_restore(EID, boost)
    assert calls == 1 and not heating._boosted
    assert any('unconfirmed' in call.args[1] for call in heating.timeline.add.call_args_list)


async def test_old_restore_cannot_remove_a_newer_timer(heating):
    old = await owned(heating)
    newer = deepcopy(old)
    heating._boosted[EID] = newer
    restores = async_mock_service(heating.hass, 'climate', 'set_preset_mode')
    await heating._async_restore(EID, old)
    assert heating._boosted[EID] is newer and not restores


async def test_concurrent_restore_submits_once(heating):
    boost = await owned(heating)
    entered, release = asyncio.Event(), asyncio.Event()
    count = 0
    async def restore(call):
        nonlocal count
        count += 1
        entered.set()
        await release.wait()
    heating.hass.services.async_register('climate', 'set_preset_mode', restore)
    task = asyncio.create_task(heating._async_restore(EID, boost))
    await entered.wait()
    await heating._async_restore(EID, boost)
    release.set()
    await task
    assert count == 1


async def test_unconfirmed_preheat_expires_without_rollback(heating, freezer):
    async_mock_service(heating.hass, 'climate', 'set_temperature')
    await start(heating)
    boost = heating._boosted[EID]
    freezer.tick(timedelta(minutes=11))
    heating._tick(dt_util.utcnow())
    await heating.hass.async_block_till_done()
    assert EID not in heating._boosted
    assert boost['status'] == 'pending'
    assert any('not confirmed' in call.args[1] for call in heating.timeline.add.call_args_list)


async def test_manual_limit_requires_observed_target(heating, freezer):
    manual(heating)
    boost = heating._boosted[EID]
    assert boost['status'] == 'pending'
    freezer.tick(timedelta(minutes=11))
    heating._tick(dt_util.utcnow())
    assert not heating._boosted


async def test_repeated_raise_preserves_original_baseline(heating):
    manual(heating)
    await activate(heating, temperature=21)
    manual(heating, data={'temperature': 22})
    assert heating._boosted[EID]['temperature'] == 16
    assert heating._boosted[EID]['preset'] == 'auto'
    assert heating._boosted[EID]['set_to'] == 22


async def test_automation_takeover_cancels_previous_manual_limit(heating):
    manual(heating)
    await activate(heating, temperature=21)
    manual(heating, data={'temperature': 22}, context=Context(parent_id='automation-parent'))
    assert EID not in heating._boosted


async def test_turn_on_limit_does_not_switch_off_a_later_manual_change(heating):
    heating.hass.states.async_set(EID, 'off', BASE)
    await heating.hass.async_block_till_done()
    manual(heating, 'turn_on', data={'entity_id': EID})
    boost = heating._boosted[EID]
    assert boost['was_off']
    await activate(heating, temperature=16)
    await activate(heating, temperature=19)
    off = async_mock_service(heating.hass, 'climate', 'turn_off')
    await heating._async_restore(EID, boost)
    assert not off and not heating._boosted


@pytest.mark.parametrize('change', ['off', 'preset', 'disabled', 'settings', 'never', 'privacy'])
async def test_preheat_rechecks_after_asking(heating, change):
    heating.options['trust_heating'] = 'ask'
    calls = async_mock_service(heating.hass, 'climate', 'set_temperature')
    await start(heating)
    answer = heating.learner.speech.async_ask.call_args.kwargs['on_answer']
    if change == 'off': heating.hass.states.async_set(EID, 'off', BASE)
    if change == 'preset': heating.hass.states.async_set(EID, 'auto', {**BASE, 'preset_mode': 'eco'})
    if change == 'disabled': heating.options['preheat'] = False
    if change == 'settings': heating.options = dict(heating.options)
    if change == 'privacy': heating.learner.speech._privacy_generation += 1
    with patch('custom_components.sentient.never.never_list', return_value={EID} if change == 'never' else set()):
        assert 'No action was taken' in await answer('yes')
    assert not calls and not heating._boosted


@pytest.mark.parametrize('level', ['do', 'tell', 'ask'])
async def test_failed_preheat_is_unconfirmed_and_does_not_count_as_trust_success(heating, level):
    heating.options['trust_heating'] = level
    async def fail(call): raise HomeAssistantError('Request timed out')
    heating.hass.services.async_register('climate', 'set_temperature', fail)
    await start(heating)
    if level == 'ask':
        answer = heating.learner.speech.async_ask.call_args.kwargs['on_answer']
        assert 'cannot confirm' in await answer('yes')
    assert not heating._boosted and not heating.trust.yes_run
    assert any('unconfirmed' in call.args[1] for call in heating.timeline.add.call_args_list)


@pytest.mark.parametrize('rate', [float('nan'), float('inf'), -1, 0])
async def test_invalid_warmup_rate_cannot_crash_planning_or_trigger_preheat(heating, rate):
    heating.zones[EID] = {'rate': rate, 'comfort': 20}
    heating.wake = {'weekends': 420}
    assert 'minutes_to_warm' not in heating.plan(EID)
    calls = async_mock_service(heating.hass, 'climate', 'set_temperature')
    heating._tick(dt_util.utcnow())
    await heating.hass.async_block_till_done()
    assert not calls


@pytest.mark.parametrize('change', ['settings', 'privacy', 'stopped'])
async def test_old_heating_learning_cannot_publish_after_policy_change(heating, change):
    async def history(*args):
        if change == 'settings': heating.options = dict(heating.options)
        if change == 'privacy': heating.learner.speech._privacy_generation += 1
        if change == 'stopped': heating.async_stop()
        return {}
    heating._async_switched_on = AsyncMock(return_value={})
    heating._async_history = history
    await heating.async_learn()
    assert heating.last_run is None and not heating.zones and not heating._learning


async def test_forgetting_during_restore_does_not_recreate_timeline(heating):
    boost = await owned(heating)
    heating.timeline.add.reset_mock()
    async def restore(call): heating.learner.speech._privacy_generation += 1
    heating.hass.services.async_register('climate', 'set_preset_mode', restore)
    await heating._async_restore(EID, boost)
    heating.timeline.add.assert_not_called()


async def test_room_temperature_update_does_not_cancel_a_valid_preheat_yes(heating):
    heating.options['trust_heating'] = 'ask'
    calls = async_mock_service(heating.hass, 'climate', 'set_temperature')
    await start(heating)
    answer = heating.learner.speech.async_ask.call_args.kwargs['on_answer']
    heating.hass.states.async_set(EID, 'auto', {**BASE, 'current_temperature': 16})
    assert await answer('yes') == 'Request sent.'
    assert len(calls) == 1


async def test_restore_respects_changed_target_limits(heating):
    heating.hass.states.async_set(EID, 'auto', {**BASE, 'preset_mode': None, 'preset_modes': []})
    boost = await owned(heating)
    heating.hass.states.async_set(EID, 'auto', {**BASE, 'temperature': 20, 'preset_mode': 'manual', 'preset_modes': [], 'min_temp': 18})
    await heating.hass.async_block_till_done()
    calls = async_mock_service(heating.hass, 'climate', 'set_temperature')
    await heating._async_restore(EID, boost)
    assert not calls
    assert any('outside' in str(call.args) for call in heating.timeline.add.call_args_list)


async def test_legacy_unverified_record_is_not_restored(heating):
    legacy = {'temperature': 16, 'preset': 'auto', 'set_to': 20, 'until': dt_util.utcnow().isoformat()}
    heating._boosted[EID] = legacy
    restores = async_mock_service(heating.hass, 'climate', 'set_preset_mode')
    heating._tick(dt_util.utcnow())
    await heating.hass.async_block_till_done()
    assert not restores and not heating._boosted


async def test_preheat_yes_after_warm_by_time_does_not_start_late(heating, freezer):
    heating.options['trust_heating'] = 'ask'
    calls = async_mock_service(heating.hass, 'climate', 'set_temperature')
    await heating._async_boost(EID, heating.hass.states.get(EID), 20, 301, dt_util.now())
    answer = heating.learner.speech.async_ask.call_args.kwargs['on_answer']
    freezer.tick(timedelta(seconds=90))  # inside consent expiry, but after the 05:01 warm-by deadline
    assert 'No action was taken' in await answer('yes')
    assert not calls and not heating._boosted
