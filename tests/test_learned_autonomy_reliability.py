"""Approved habits must retain their meaning through learning and delayed consent."""
import asyncio
from copy import deepcopy
from datetime import datetime, timedelta
from types import SimpleNamespace
from unittest.mock import AsyncMock, Mock, patch

import pytest
from homeassistant.core import State
from homeassistant.util import dt as dt_util
from pytest_homeassistant_custom_component.common import async_mock_service

from custom_components.sentient.learning import Learner, Routine, Link, _changes_to, _state_at, find_routines, find_links
from custom_components.sentient.trust import Trust


@pytest.fixture
def learner(hass):
    options = {'learn_entities': ['light.lamp'], 'suggest_routines': True}
    speech = SimpleNamespace(guest_mode=False, _privacy_generation=0, _guest_generation=0,
        hushed=Mock(return_value=False), async_ask=AsyncMock(return_value=SimpleNamespace(spoken=True)), async_say=AsyncMock())
    obj = Learner(hass, 'approved-habits', options, speech, None)
    obj._save = Mock()
    obj._async_history = AsyncMock(return_value={})
    obj._async_automated = AsyncMock(return_value={})
    rt = SimpleNamespace(speech=speech, timeline=Mock())
    obj.trust = Trust(hass, 'approved-habits', options, lambda: rt)
    obj.trust._save = Mock()
    hass.states.async_set('light.lamp', 'off', {'friendly_name': 'Lamp'})
    return obj


def habit(obj, adopted=False):
    item = Routine('light.lamp', 'lamp', 'on', 1140, 'every day', 0.8, 10, 4, adopted=adopted)
    obj.routines[item.id] = item
    return item


async def ask_offer(obj):
    await obj._async_offer(dt_util.now())
    return obj.speech.async_ask.call_args.kwargs['on_answer']


@pytest.mark.parametrize('change', ['removed', 'replaced', 'time', 'target', 'adopted', 'declined', 'disabled',
    'settings', 'guest', 'forgotten', 'guest_cycle', 'stopped', 'expired'])
async def test_saved_habit_offer_does_not_adopt_a_changed_plan(learner, freezer, change):
    obj = learner
    item = habit(obj)
    answer = await ask_offer(obj)
    if change == 'removed': obj.dismiss(item.id)
    if change == 'replaced': obj.routines[item.id] = deepcopy(item)
    if change == 'time': item.minute += 30
    if change == 'target': item.entity_id = 'light.other'
    if change == 'adopted': item.adopted = True
    if change == 'declined': item.declined = True
    if change == 'disabled': obj.options['suggest_routines'] = False
    if change == 'settings': obj.options = dict(obj.options)
    if change == 'guest': obj.speech.guest_mode = True
    if change == 'forgotten': obj.speech._privacy_generation += 1
    if change == 'guest_cycle': obj.speech._guest_generation += 1
    if change == 'stopped': obj.async_stop()
    if change == 'expired': freezer.tick(timedelta(minutes=3))
    before = [r.as_dict() for r in obj.all()]
    assert "haven't changed" in await answer('yes')
    assert [r.as_dict() for r in obj.all()] == before


async def test_saved_offer_is_consumed_once(learner):
    item = habit(learner)
    answer = await ask_offer(learner)
    assert "from now on" in await answer('yes')
    assert item.adopted
    assert "haven't changed" in await answer('no')
    assert item.adopted and not item.declined


async def test_grouped_offer_cannot_partly_adopt_after_one_member_changes(learner):
    first = habit(learner)
    first.dates = ['2026-10-01', '2026-10-02']
    second = Routine('switch.lamp', 'second lamp', 'on', 1145, 'every day', 0.8, 10, 4, dates=first.dates)
    learner.routines[second.id] = second
    answer = await ask_offer(learner)
    second.minute += 40
    await answer('yes')
    assert not first.adopted and not second.adopted


async def test_overlapping_offers_ask_once(learner):
    habit(learner)
    entered, release = asyncio.Event(), asyncio.Event()
    async def ask(*args, **kwargs):
        entered.set()
        await release.wait()
        return SimpleNamespace(spoken=True)
    learner.speech.async_ask.side_effect = ask
    task = asyncio.create_task(learner._async_offer(dt_util.now()))
    await entered.wait()
    await learner._async_offer(dt_util.now())
    release.set()
    await task
    learner.speech.async_ask.assert_awaited_once()


@pytest.mark.parametrize('change', ['unadopt', 'delete', 'target', 'settings', 'forgotten', 'stopped', 'never'])
async def test_delayed_trust_answer_rechecks_the_adopted_habit(hass, learner, change):
    item = habit(learner, True)
    learner.options['trust_lights'] = 'ask'
    calls = async_mock_service(hass, 'light', 'turn_on')
    await learner._async_do(item)
    answer = learner.speech.async_ask.call_args.kwargs['on_answer']
    if change == 'unadopt': learner.adopt(item.id, False)
    if change == 'delete': learner.dismiss(item.id)
    if change == 'target': item.entity_id = 'light.other'
    if change == 'settings': learner.options = dict(learner.options)
    if change == 'forgotten': learner.speech._privacy_generation += 1
    if change == 'stopped': learner.async_stop()
    with patch('custom_components.sentient.never.never_list', return_value={'light.lamp'} if change == 'never' else set()):
        assert 'No action was taken' in await answer('yes')
    assert not calls and not learner.trust.yes_run


@pytest.mark.parametrize('change', ['unadopt', 'delete', 'state', 'never', 'stopped'])
async def test_tell_then_do_rechecks_after_speech(hass, learner, change):
    item = habit(learner, True)
    learner.options['trust_lights'] = 'tell'
    calls = async_mock_service(hass, 'light', 'turn_on')
    async def say(*args, **kwargs):
        if change == 'unadopt': learner.adopt(item.id, False)
        if change == 'delete': learner.dismiss(item.id)
        if change == 'state': hass.states.async_set('light.lamp', 'unavailable')
        if change == 'stopped': learner.async_stop()
    learner.speech.async_say.side_effect = say
    with patch('custom_components.sentient.never.blocked', side_effect=lambda *args: change == 'never' and learner.speech.async_say.await_count > 0):
        await learner._async_do(item)
    assert not calls


async def test_saved_trust_yes_is_once_and_reports_submission(hass, learner):
    item = habit(learner, True)
    learner.options['trust_lights'] = 'ask'
    calls = async_mock_service(hass, 'light', 'turn_on')
    await learner._async_do(item)
    answer = learner.speech.async_ask.call_args.kwargs['on_answer']
    assert await answer('yes') == 'Request sent.'
    assert 'already been handled' in await answer('yes')
    assert len(calls) == 1 and learner.trust.yes_run['lights'] == 1


async def test_trust_callback_expiry_and_unload(hass, learner, freezer):
    habit(learner, True)
    learner.options['trust_lights'] = 'ask'
    calls = async_mock_service(hass, 'light', 'turn_on')
    await learner._async_do(next(iter(learner.routines.values())))
    answer = learner.speech.async_ask.call_args.kwargs['on_answer']
    freezer.tick(timedelta(minutes=3))
    assert 'No action was taken' in await answer('yes')
    assert not calls


async def test_trust_cannot_act_on_locks_or_alarm(hass, learner):
    for eid in ('lock.front', 'alarm_control_panel.home'):
        hass.states.async_set(eid, 'locked')
        run = AsyncMock()
        assert await learner.trust.async_act(eid, 'Doing it', 'Shall I?', run, 'test') == 'not asked'
        run.assert_not_awaited()


async def test_trust_does_not_count_a_refused_callback_as_success(learner):
    learner.options['trust_lights'] = 'ask'
    run = AsyncMock(return_value=False)
    await learner.trust.async_act('light.lamp', 'Putting it on', 'Shall I?', run, 'test')
    answer = learner.speech.async_ask.call_args.kwargs['on_answer']
    assert 'No action was taken' in await answer('yes')
    assert not learner.trust.yes_run


async def test_relearning_preserves_approved_schedule(learner):
    old = habit(learner, True)
    fresh = deepcopy(old)
    fresh.minute = 400
    fresh.anchor = 'sunset'
    fresh.offset = 60
    with patch('custom_components.sentient.learning.find_routines', return_value=[fresh]):
        await learner.async_learn()
    assert learner.get(old.id) is old
    assert old.minute == 1140 and old.anchor == 'clock' and old.offset == 0


@pytest.mark.parametrize('change', ['dismiss', 'unadopt', 'settings', 'privacy', 'stop'])
async def test_delayed_learning_does_not_overwrite_new_decisions(learner, change):
    old = habit(learner, True)
    async def history(*args):
        if change == 'dismiss': learner.dismiss(old.id)
        if change == 'unadopt': learner.adopt(old.id, False)
        if change == 'settings': learner.options = dict(learner.options)
        if change == 'privacy': learner.speech._privacy_generation += 1
        if change == 'stop': learner.async_stop()
        return {}
    learner._async_history.side_effect = history
    with patch('custom_components.sentient.learning.find_routines', return_value=[deepcopy(old)]):
        await learner.async_learn()
    assert learner.last_run is None
    if change == 'dismiss': assert learner.get(old.id) is None
    if change == 'unadopt': assert not old.adopted
    assert not learner._learning


async def test_learning_is_single_flight(learner):
    entered, release = asyncio.Event(), asyncio.Event()
    async def history(*args):
        entered.set()
        await release.wait()
        return {}
    learner._async_history.side_effect = history
    task = asyncio.create_task(learner.async_learn())
    await entered.wait()
    await learner.async_learn()
    release.set()
    await task
    # One effect-history read, then one for possible causes; the concurrent call reads neither.
    assert learner._async_history.await_count <= 2


def test_recovery_from_unknown_is_not_a_human_action():
    now = dt_util.utcnow()
    states = [State('light.lamp', value, last_changed=now + timedelta(seconds=i))
              for i, value in enumerate(('off', 'unknown', 'on'))]
    assert _changes_to(states, 'on') == []
    assert _state_at(states, now + timedelta(seconds=1)) is None


def test_sparse_anchor_matches_do_not_claim_every_day_confidence():
    today = dt_util.now().replace(hour=12)
    events = []
    for ago in range(14, 0, -1):
        day = today - timedelta(days=ago)
        events.append(State('light.lamp', 'off', last_changed=day.replace(hour=8)))
        if ago <= 3:
            events.append(State('light.lamp', 'on', last_changed=day.replace(hour=18, minute=ago)))
    found = find_routines('light.lamp', 'lamp', events, today,
        anchors={'sunset': lambda day: 18 * 60})
    assert not [r for r in found if r.state == 'on']


async def test_scheduled_routine_is_not_dispatched_twice_in_one_local_minute(learner):
    item = habit(learner, True)
    now = dt_util.now().replace(hour=19, minute=0)
    with patch.object(learner, '_async_do', new=AsyncMock()) as run:
        learner._every_minute(now)
        learner._every_minute(now)
        await learner.hass.async_block_till_done()
    run.assert_awaited_once_with(item)


@pytest.mark.parametrize('change', ['unload', 'options', 'inplace', 'privacy', 'guest_cycle', 'state', 'attributes'])
async def test_shared_trust_consent_expires_with_its_original_context(hass, learner, change):
    learner.options['trust_lights'] = 'ask'
    run = AsyncMock()
    await learner.trust.async_act('light.lamp', 'Putting it on', 'Shall I?', run, 'test')
    answer = learner.speech.async_ask.call_args.kwargs['on_answer']
    if change == 'unload': learner.trust.async_stop()
    if change == 'options': learner.trust.options = dict(learner.options)
    if change == 'inplace': learner.options['new_setting'] = True
    if change == 'privacy': learner.speech._privacy_generation += 1
    if change == 'guest_cycle': learner.speech._guest_generation += 1
    if change == 'state': hass.states.async_set('light.lamp', 'on', {'friendly_name': 'Lamp'})
    if change == 'attributes': hass.states.async_set('light.lamp', 'off', {'friendly_name': 'Renamed lamp'})
    assert 'No action was taken' in await answer('yes')
    run.assert_not_awaited()
    assert not learner.trust.yes_run


async def test_forgetting_during_execution_does_not_restore_trust_success_history(learner):
    learner.options['trust_lights'] = 'ask'
    async def run():
        learner.speech._privacy_generation += 1
    await learner.trust.async_act('light.lamp', 'Putting it on', 'Shall I?', run, 'test')
    answer = learner.speech.async_ask.call_args.kwargs['on_answer']
    assert await answer('yes') == 'Request sent.'  # it ran, so don't claim nothing happened
    assert not learner.trust.yes_run


async def test_decision_changed_during_link_history_read_does_not_partly_publish(learner):
    old = habit(learner, True)
    candidate = Routine('light.lamp', 'lamp', 'off', 1200, 'every day', 0.9, 12, 4)
    async def links(*args):
        learner.adopt(old.id, False)
        return {}
    with patch('custom_components.sentient.learning.find_routines', return_value=[candidate]), patch.object(learner, '_async_learn_links', side_effect=links):
        await learner.async_learn()
    assert learner.get(candidate.id) is None and not old.adopted
    assert learner.last_run is None


async def test_learning_with_no_selected_effects_does_not_read_unrelated_door_history(hass, learner):
    learner.options['learn_entities'] = []
    hass.states.async_set('binary_sensor.front', 'off', {'device_class': 'door'})
    await learner.async_learn()
    assert learner._async_history.await_args_list[0].args == ([],)
    assert learner._async_history.await_count == 1
