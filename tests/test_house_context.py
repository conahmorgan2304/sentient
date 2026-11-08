"""Current house context remains bounded, relevant, private and evidence-based."""
from datetime import timedelta
from types import SimpleNamespace
from unittest.mock import AsyncMock, Mock, patch
import pytest
from homeassistant.core import Context, Event
from homeassistant.util import dt as dt_util
from custom_components.sentient.house_context import HouseContext, CONF, UPDATES, prompt
from tests.test_voice_sessions_control import request
from custom_components.sentient.sessions import Sessions


def engine(hass, updates=False):
    rt = SimpleNamespace(options={CONF: True, UPDATES: updates}, speech=SimpleNamespace(guest_mode=False, async_say=AsyncMock()), sessions=Sessions({}))
    obj = HouseContext(hass, lambda: rt)
    return obj, rt


def heat(hass, current=17, target=20):
    hass.states.async_set('climate.room', 'heat', {'current_temperature': current, 'temperature': target, 'friendly_name': 'Room'})


async def test_snapshot_is_fresh_and_history_expires(hass, freezer):
    obj, rt = engine(hass)
    with patch('custom_components.sentient.house_context.async_should_expose', return_value=True):
        obj.configure()
        heat(hass)
        await hass.async_block_till_done()
        heat(hass, current=18)
        await hass.async_block_till_done()
        data = await obj.snapshot(Context(), request='how is the house')
        assert data['current_readings'][0]['attributes']['current_temperature'] == 18
        assert data['recent_changes'][0]['from_attributes']['current_temperature'] == 17
        freezer.tick(timedelta(minutes=31))
        assert not (await obj.snapshot(Context(), request='house'))['recent_changes']
    obj.stop()


@pytest.mark.parametrize('mode', ['off', 'guest', 'hidden', 'denied'])
async def test_snapshot_privacy(hass, mode):
    obj, rt = engine(hass)
    heat(hass)
    if mode == 'off': rt.options[CONF] = False
    if mode == 'guest': rt.speech.guest_mode = True
    user = SimpleNamespace(is_active=True, permissions=SimpleNamespace(check_entity=lambda *a: False))
    with patch('custom_components.sentient.house_context.async_should_expose', return_value=mode != 'hidden'), patch.object(type(hass.auth), 'async_get_user', new=AsyncMock(return_value=user)):
        data = await obj.snapshot(Context(user_id='user' if mode == 'denied' else None), request='house')
    assert data is None or not data['current_readings']


async def test_snapshot_is_bounded_relevant_and_json_safe(hass):
    obj, rt = engine(hass)
    for i in range(20):
        hass.states.async_set(f'climate.room_{i}', 'heat', {'temperature': float('nan')})
    hass.states.async_set('sensor.washing', 'finished')
    rt.options['house_context_entities'] = ['sensor.washing']
    with patch('custom_components.sentient.house_context.async_should_expose', return_value=True):
        data = await obj.snapshot(Context(), request='washing')
        assert len(data['current_readings']) == 1
        assert data['current_readings'][0]['state'] == 'finished'
        assert 'NaN' not in prompt(await obj.snapshot(Context(), request='house'))
        assert len((await obj.snapshot(Context(), request='house'))['current_readings']) == 8


async def test_unfinished_task_is_scoped_to_current_conversation(hass):
    obj, rt = engine(hass)
    session = rt.sessions.get(request('check heating'))
    session.goal = 'check heating'
    with patch('custom_components.sentient.house_context.async_should_expose', return_value=True):
        data = await obj.snapshot(Context(), 'kitchen', 'heating', session)
        assert data['unfinished_in_this_conversation']['goal'] == 'check heating'
        assert (await obj.snapshot(Context(), 'bedroom', 'heating', session))['unfinished_in_this_conversation'] is None


async def test_followup_once_when_measured_temperature_reaches_target(hass):
    obj, rt = engine(hass, updates=True)
    with patch('custom_components.sentient.house_context.async_should_expose', return_value=True):
        obj.configure()
        heat(hass)
        await hass.async_block_till_done()
        session = rt.sessions.get(request('warm room'))
        obj.track(session, [{'entity_id': 'climate.room', 'action': 'set_temperature', 'temperature': 20}],
                  {'actions': [{'entity_id': 'climate.room', 'status': 'verified'}]}, Context())
        heat(hass, current=20)
        await hass.async_block_till_done()
        heat(hass, current=21)
        await hass.async_block_till_done()
    assert rt.speech.async_say.await_count == 1
    assert 'reports' in rt.speech.async_say.call_args.args[0]
    assert not obj.followups
    obj.stop()


@pytest.mark.parametrize('mode', ['off', 'unverified', 'expired', 'changed_target', 'guest', 'forget'])
async def test_followup_never_revives_invalid_request(hass, freezer, mode):
    obj, rt = engine(hass, updates=mode != 'off')
    with patch('custom_components.sentient.house_context.async_should_expose', return_value=True):
        obj.configure()
        heat(hass)
        await hass.async_block_till_done()
        session = rt.sessions.get(request('warm room'))
        obj.track(session, [{'entity_id': 'climate.room', 'action': 'set_temperature', 'temperature': 20}],
                  {'actions': [{'entity_id': 'climate.room', 'status': 'attempted' if mode == 'unverified' else 'verified'}]}, Context())
        if mode == 'expired': freezer.tick(timedelta(hours=2))
        if mode == 'guest': rt.speech.guest_mode = True
        if mode == 'forget': obj.clear()
        heat(hass, current=20, target=22 if mode == 'changed_target' else 20)
        await hass.async_block_till_done()
    rt.speech.async_say.assert_not_awaited()
    obj.stop()


async def test_context_integrates_into_shared_api(hass):
    from tests.test_reasons import _setup
    from homeassistant.helpers import llm
    from custom_components.sentient.const import DOMAIN
    entry = await _setup(hass)
    rt = entry.runtime_data
    rt.options[CONF] = True
    rt.options['house_context_entities'] = ['sensor.washing']
    rt.house_context.configure()
    hass.states.async_set('sensor.washing', 'finished')
    with patch('custom_components.sentient.house_context.async_should_expose', return_value=True):
        api = await llm.async_get_api(hass, DOMAIN, llm.LLMContext(platform='test', context=Context(), language='en', assistant=None, device_id=None))
    assert 'Current house context (quoted data' in api.api_prompt
    assert 'sensor.washing' in api.api_prompt
    assert 'not physical verification' in api.api_prompt
    rt.options[CONF] = False
    rt.house_context.configure()
    api = await llm.async_get_api(hass, DOMAIN, llm.LLMContext(platform='test', context=Context(), language='en', assistant=None, device_id=None))
    assert 'Current house context (quoted data' not in api.api_prompt


async def test_clear_during_permission_await_discards_snapshot(hass):
    obj, rt = engine(hass)
    async def get_user(*a):
        obj.clear()
        return SimpleNamespace(is_active=True)
    with patch.object(type(hass.auth), 'async_get_user', side_effect=get_user):
        assert await obj.snapshot(Context(user_id='user')) is None


async def test_cache_is_bounded_and_disabling_clears_it(hass):
    obj, rt = engine(hass)
    with patch('custom_components.sentient.house_context.async_should_expose', return_value=True):
        obj.configure()
        for i in range(70):
            hass.states.async_set(f'switch.item_{i}', 'off')
            hass.states.async_set(f'switch.item_{i}', 'on')
        await hass.async_block_till_done()
    assert len(obj.changes) == 64
    rt.options[CONF] = False
    obj.configure()
    assert not obj.changes and not obj.followups and obj._unsub is None


async def test_followup_respects_revoked_access(hass):
    obj, rt = engine(hass, updates=True)
    heat(hass, current=20)
    follow = {'target': 20, 'expires': dt_util.utcnow() + timedelta(hours=1),
              'context': Context(user_id='user'), 'owner': ('kitchen', None, 'user')}
    user = SimpleNamespace(is_active=True, permissions=SimpleNamespace(check_entity=lambda *a: False))
    with patch.object(type(hass.auth), 'async_get_user', new=AsyncMock(return_value=user)):
        await obj._announce('climate.room', follow, obj.generation)
    rt.speech.async_say.assert_not_awaited()


async def test_followup_frequency_is_bounded(hass):
    obj, rt = engine(hass, updates=True)
    heat(hass, current=20)
    follow = {'target': 20, 'expires': dt_util.utcnow() + timedelta(hours=1),
              'context': Context(), 'owner': ('kitchen', None, None)}
    with patch('custom_components.sentient.house_context.async_should_expose', return_value=True):
        await obj._announce('climate.room', follow, obj.generation)
        await obj._announce('climate.room', follow, obj.generation)
    assert rt.speech.async_say.await_count == 1


async def test_guarded_action_arms_followup_only_after_verified_service(hass):
    from custom_components.sentient.control import Control
    obj, rt = engine(hass, updates=True)
    control = Control(hass, {}, Mock())
    control.on_result = obj.track
    session = rt.sessions.get(request('heat room'))
    session.turn = 1
    session.running = session.authorised = True
    heat(hass, current=17, target=17)
    async def setpoint(call):
        heat(hass, current=17, target=call.data['temperature'])
    hass.services.async_register('climate', 'set_temperature', setpoint)
    with patch('custom_components.sentient.control.async_should_expose', return_value=True):
        result = await control.execute(session, [{'entity_id': 'climate.room', 'action': 'set_temperature', 'temperature': 20}], Context())
    assert result['actions'][0]['status'] == 'verified'
    assert obj.followups['climate.room']['target'] == 20
    rt.speech.async_say.assert_not_awaited()


async def test_removed_device_cancels_observation(hass):
    obj, rt = engine(hass, updates=True)
    with patch('custom_components.sentient.house_context.async_should_expose', return_value=True):
        obj.configure()
        heat(hass)
        await hass.async_block_till_done()
        obj.followups['climate.room'] = {'target': 20, 'expires': dt_util.utcnow() + timedelta(hours=1)}
        hass.states.async_remove('climate.room')
        await hass.async_block_till_done()
    assert not obj.followups
    rt.speech.async_say.assert_not_awaited()
    obj.stop()


async def test_guest_switch_clears_context_immediately(hass):
    from custom_components.sentient.switch import set_guest_mode
    obj, rt = engine(hass)
    rt.house_context = obj
    obj.changes['climate.room'] = {'at': dt_util.utcnow()}
    obj.followups['climate.room'] = {'target': 20}
    set_guest_mode(hass, SimpleNamespace(runtime_data=rt), True)
    assert not obj.changes and not obj.followups


async def test_private_room_never_gets_other_room_snapshot(hass):
    obj, rt = engine(hass)
    rt.profiles = SimpleNamespace(for_device=lambda device: {'private': True})
    heat(hass)
    with patch('custom_components.sentient.house_context.async_should_expose', return_value=True):
        assert not (await obj.snapshot(Context(), request='house'))['current_readings']


async def test_pending_heating_context_works_without_spoken_updates(hass):
    obj, rt = engine(hass, updates=False)
    heat(hass)
    session = rt.sessions.get(request('heat room'))
    obj.track(session, [{'entity_id': 'climate.room', 'action': 'set_temperature', 'temperature': 20}],
              {'actions': [{'entity_id': 'climate.room', 'status': 'verified'}]}, Context())
    with patch('custom_components.sentient.house_context.async_should_expose', return_value=True):
        data = await obj.snapshot(Context(), 'kitchen', 'room', session)
        assert data['pending_observations'][0]['target'] == 20
        assert not (await obj.snapshot(Context(), 'bedroom', 'room', session))['pending_observations']
    rt.speech.async_say.assert_not_awaited()
