"""Report evidence and continue only never-dispatched guarded actions after new consent."""
import asyncio
from copy import deepcopy
from datetime import timedelta
from unittest.mock import AsyncMock, Mock, patch

import pytest
from homeassistant.core import Context
from homeassistant.exceptions import HomeAssistantError
from homeassistant.util import dt as dt_util
from pytest_homeassistant_custom_component.common import async_mock_service

from custom_components.sentient.control import Control
from custom_components.sentient.action_recovery import inspect, outcome_text, CONF
from tests.test_voice_sessions_control import active, agent, request, response

OPTIONS = {'conversation_sessions': True, 'guarded_control': True, CONF: True}


async def batch(hass):
    _, session = active(OPTIONS)
    control = Control(hass, OPTIONS, Mock())
    calls = []
    for name in ('kitchen', 'hall', 'livingroom'):
        hass.states.async_set('light.' + name, 'on', {'friendly_name': name.title()})
    async def off(call):
        eid = call.data['entity_id']
        calls.append(eid)
        if eid == 'light.hall':
            raise HomeAssistantError('network error')
        hass.states.async_set(eid, 'off', {'friendly_name': eid.split('.')[1].title()})
    hass.services.async_register('light', 'turn_off', off)
    actions = [{'entity_id': 'light.' + name, 'action': 'turn_off'} for name in ('kitchen', 'hall', 'livingroom')]
    with patch('custom_components.sentient.control.async_should_expose', return_value=True):
        result = await control.execute(session, actions, Context())
    return control, session, calls, result


async def test_partial_result_names_success_uncertainty_and_not_sent(hass):
    control, session, calls, result = await batch(hass)
    assert calls == ['light.kitchen', 'light.hall']
    assert [r['status'] for r in result['actions']] == ['verified', 'failed', 'not_started']
    text = outcome_text(hass, result)
    assert 'Kitchen: reported the requested state' in text
    assert 'Hall: request failed; delivery may be uncertain' in text
    assert 'Livingroom: not sent' in text


async def test_only_unsent_device_is_proposed_and_needs_new_turn(hass):
    control, session, calls, _ = await batch(hass)
    with patch('custom_components.sentient.action_recovery.async_should_expose', return_value=True), patch('custom_components.sentient.control.async_should_expose', return_value=True):
        result = await inspect(control, session, Context())
        assert result['listen']
        assert session.pending['actions'] == [{'entity_id': 'light.livingroom', 'action': 'turn_off'}]
        assert (await control.confirm(session, Context())).get('error')  # same-turn yes is not consent
        await inspect(control, session, Context())
        session.turn += 1
        result = await control.confirm(session, Context())
    assert calls == ['light.kitchen', 'light.hall', 'light.livingroom']
    assert result['actions'][0]['status'] == 'verified'


async def test_late_success_and_manual_changes_are_not_replayed(hass):
    control, session, calls, _ = await batch(hass)
    hass.states.async_set('light.kitchen', 'on', {'friendly_name': 'Kitchen'})
    hass.states.async_set('light.hall', 'off', {'friendly_name': 'Hall'})
    hass.states.async_set('light.livingroom', 'off', {'friendly_name': 'Livingroom'})
    with patch('custom_components.sentient.action_recovery.async_should_expose', return_value=True):
        result = await inspect(control, session, Context())
    assert not result.get('listen') and session.pending is None
    assert 'previously reported success but has since changed' in result['text']
    assert 'Hall now reports the requested state' in result['text']
    assert len(calls) == 2


async def test_state_change_between_retry_offer_and_confirmation_blocks_dispatch(hass):
    control, session, calls, _ = await batch(hass)
    with patch('custom_components.sentient.action_recovery.async_should_expose', return_value=True), patch('custom_components.sentient.control.async_should_expose', return_value=True):
        await inspect(control, session, Context())
        session.turn += 1
        hass.states.async_set('light.livingroom', 'off')
        result = await control.confirm(session, Context())
    assert result.get('error') and len(calls) == 2


async def test_other_device_and_expired_record_cannot_offer_retries(hass):
    control, session, _, _ = await batch(hass)
    _, other = active(OPTIONS)
    other.owner = ('bedroom', None, None)
    assert 'recent guarded' in (await inspect(control, other, Context()))['text']
    control.recovery_records[session.owner]['expires'] = dt_util.utcnow() - timedelta(seconds=1)
    assert 'recent guarded' in (await inspect(control, session, Context()))['text']
    assert session.pending is None


async def test_hidden_devices_are_not_named_or_retried(hass):
    control, session, _, _ = await batch(hass)
    with patch('custom_components.sentient.action_recovery.async_should_expose', return_value=False):
        result = await inspect(control, session, Context())
    assert 'Kitchen' not in result['text'] and 'Hall' not in result['text']
    assert session.pending is None


async def test_forget_during_dispatch_cannot_recreate_record(hass):
    _, session = active(OPTIONS)
    control = Control(hass, OPTIONS, Mock())
    hass.states.async_set('light.test', 'on')
    entered, release = asyncio.Event(), asyncio.Event()
    async def off(call):
        entered.set()
        await release.wait()
        hass.states.async_set('light.test', 'off')
    hass.services.async_register('light', 'turn_off', off)
    with patch('custom_components.sentient.control.async_should_expose', return_value=True):
        task = hass.async_create_task(control.execute(session, [{'entity_id': 'light.test', 'action': 'turn_off'}], Context()))
        await entered.wait()
        control.clear_recovery()
        release.set()
        await task
    assert not control.recovery_records


async def test_old_request_cannot_replace_record_after_new_request_started(hass):
    _, session = active(OPTIONS)
    control = Control(hass, OPTIONS, Mock())
    hass.states.async_set('light.test', 'on')
    entered, release = asyncio.Event(), asyncio.Event()
    async def off(call):
        entered.set()
        await release.wait()
        hass.states.async_set('light.test', 'off')
    hass.services.async_register('light', 'turn_off', off)
    control.invalidate_recovery(session.owner)
    with patch('custom_components.sentient.control.async_should_expose', return_value=True):
        task = hass.async_create_task(control.execute(session, [{'entity_id': 'light.test', 'action': 'turn_off'}], Context()))
        await entered.wait()
        control.invalidate_recovery(session.owner)
        release.set()
        await task
    assert not control.recovery_records


async def test_recovery_on_new_wake_uses_same_speaker_but_fresh_confirmation(hass):
    control, old, calls, _ = await batch(hass)
    obj, rt = agent(hass, OPTIONS)
    rt.control = control
    with patch.object(obj, '_guarded_configuration', return_value=True), patch.object(obj, '_converse', AsyncMock()) as ai, patch('custom_components.sentient.action_recovery.async_should_expose', return_value=True), patch('custom_components.sentient.control.async_should_expose', return_value=True):
        result = await obj.async_process(request("That didn't work", cid='new-wake'))
        assert result.continue_conversation
        result = await obj.async_process(request('yes', cid='new-wake'))
    ai.assert_not_awaited()
    assert not result.continue_conversation
    assert calls == ['light.kitchen', 'light.hall', 'light.livingroom']


async def test_missing_record_does_not_guess_or_call_model(hass):
    obj, _ = agent(hass, OPTIONS)
    with patch.object(obj, '_guarded_configuration', return_value=True), patch.object(obj, '_converse', AsyncMock()) as ai:
        result = await obj.async_process(request("That didn't work"))
    assert "don't have a recent guarded action record" in result.response.speech['plain']['speech']
    ai.assert_not_awaited()


def test_new_option_is_switchable():
    from custom_components.sentient.config_flow import _speech_schema
    from custom_components.sentient.websocket import GROUPS
    assert CONF in {str(k) for k in _speech_schema({}).schema}
    assert CONF in next(row[4] for row in GROUPS if row[0] == 'voice')


async def test_two_batches_in_same_turn_keep_all_device_outcomes(hass):
    _, session = active(OPTIONS)
    control = Control(hass, OPTIONS, Mock())
    calls = []
    async def off(call):
        calls.append(call.data['entity_id'])
        hass.states.async_set(call.data['entity_id'], 'off')
    hass.services.async_register('light', 'turn_off', off)
    with patch('custom_components.sentient.control.async_should_expose', return_value=True):
        for name in ('one', 'two'):
            hass.states.async_set('light.' + name, 'on')
            result = await control.execute(session, [{'entity_id': 'light.' + name, 'action': 'turn_off'}], Context())
    assert [r['entity_id'] for r in result['recovery_outcomes']] == ['light.one', 'light.two']
    assert len(control.recovery_records[session.owner]['rows']) == 2


async def test_option_off_creates_no_recovery_records_and_preserves_model_path(hass):
    obj, rt = agent(hass, {'conversation_sessions': True})
    with patch.object(obj, '_async_process_legacy', AsyncMock(return_value=response('Please tell me more.'))) as ai:
        await obj.async_process(request("That didn't work"))
    ai.assert_awaited_once()
    assert not rt.control.recovery_records and not rt.control.recovery_turns


async def test_settings_and_forgetting_clear_records(hass):
    from tests.test_phone_answers import _setup
    from custom_components.sentient.privacy import forget_recent
    entry = await _setup(hass, **OPTIONS)
    rt = entry.runtime_data
    rt.control.recovery_records[('kitchen', None, None)] = {'private': 'old'}
    forget_recent(rt)
    assert not rt.control.recovery_records
    rt.control.recovery_records[('kitchen', None, None)] = {'private': 'old'}
    hass.config_entries.async_update_entry(entry, options={CONF: False})
    await hass.async_block_till_done()
    assert not rt.control.recovery_records


async def test_model_success_claim_is_replaced_by_actual_partial_outcomes(hass):
    from custom_components.sentient.sessions import current_session
    obj, rt = agent(hass, OPTIONS)
    for name in ('one', 'two', 'three'):
        hass.states.async_set('light.' + name, 'on', {'friendly_name': name.title()})
    async def off(call):
        if call.data['entity_id'] == 'light.two':
            raise HomeAssistantError('failed')
        hass.states.async_set(call.data['entity_id'], 'off', {'friendly_name': 'One'})
    hass.services.async_register('light', 'turn_off', off)
    async def model(*args):
        await rt.control.execute(current_session.get(), [{'entity_id': 'light.' + n, 'action': 'turn_off'} for n in ('one', 'two', 'three')], Context())
        return response('Everything is off!')
    with patch.object(obj, '_guarded_configuration', return_value=True), patch.object(obj, '_converse', side_effect=model), patch('custom_components.sentient.control.async_should_expose', return_value=True):
        result = await obj.async_process(request('Turn off the lights'))
    said = result.response.speech['plain']['speech']
    assert 'Everything is off' not in said
    assert 'Three: not sent' in said and 'delivery may be uncertain' in said
    assert not result.continue_conversation


async def test_guest_error_clears_any_inflight_recovery_record(hass):
    from types import SimpleNamespace
    obj, rt = agent(hass, OPTIONS)
    rt.speech = SimpleNamespace(guest_mode=True)
    async def failing(*args):
        rt.control.recovery_records[('kitchen', None, None)] = {'private': 'old'}
        raise asyncio.CancelledError
    with patch.object(obj, '_async_process_impl', side_effect=failing), pytest.raises(asyncio.CancelledError):
        await obj.async_process(request('Turn off the lights'))
    assert not rt.control.recovery_records


async def test_queued_old_batch_cannot_publish_after_new_request(hass):
    _, session = active(OPTIONS)
    control = Control(hass, OPTIONS, Mock())
    entered, release = asyncio.Event(), asyncio.Event()
    for name in ('one', 'two'):
        hass.states.async_set('light.' + name, 'on')
    async def off(call):
        if call.data['entity_id'] == 'light.one':
            entered.set()
            await release.wait()
        hass.states.async_set(call.data['entity_id'], 'off')
    hass.services.async_register('light', 'turn_off', off)
    control.invalidate_recovery(session.owner)
    with patch('custom_components.sentient.control.async_should_expose', return_value=True):
        first = hass.async_create_task(control.execute(session, [{'entity_id': 'light.one', 'action': 'turn_off'}], Context()))
        await entered.wait()
        second = hass.async_create_task(control.execute(session, [{'entity_id': 'light.two', 'action': 'turn_off'}], Context()))
        await asyncio.sleep(0)
        control.invalidate_recovery(session.owner)
        release.set()
        await asyncio.gather(first, second)
    assert not control.recovery_records


async def test_retry_permission_revocation_blocks_action(hass):
    control, session, calls, _ = await batch(hass)
    with patch('custom_components.sentient.action_recovery.async_should_expose', return_value=True), patch('custom_components.sentient.control.async_should_expose', return_value=True):
        await inspect(control, session, Context())
    session.turn += 1
    with patch('custom_components.sentient.control.async_should_expose', return_value=False):
        result = await control.confirm(session, Context())
    assert result.get('error') and len(calls) == 2


async def test_record_expiry_during_permission_check_cannot_create_confirmation(hass):
    control, session, _, _ = await batch(hass)
    async def expire(*args):
        control.recovery_records[session.owner]['expires'] = dt_util.utcnow() - timedelta(seconds=1)
    with patch('custom_components.sentient.action_recovery.async_should_expose', return_value=True), patch.object(control, 'validate', side_effect=expire):
        result = await inspect(control, session, Context())
    assert not result.get('listen') and session.pending is None


def test_recovery_phrase_tolerates_stt_punctuation():
    from custom_components.sentient.action_recovery import requested
    assert requested('That didnt work!')
    assert requested('That didn’t work.')
    assert not requested('That did not work yesterday, turn on another lamp')
