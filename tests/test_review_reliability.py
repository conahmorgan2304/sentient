"""Regressions found by exercising feature changes, delayed replies and recovery failures."""
from datetime import timedelta
from unittest.mock import patch, AsyncMock

import pytest
from homeassistant.components.conversation.chat_log import UserContent, async_get_chat_log
from homeassistant.helpers import chat_session
from homeassistant.util import dt as dt_util
from homeassistant.exceptions import HomeAssistantError
from pytest_homeassistant_custom_component.common import async_mock_service

from custom_components.sentient import outside
from custom_components.sentient.privacy import forget_recent
from tests.test_alarm_guard import _setup as alarm_setup, _alarm
from tests.test_privacy import _setup as privacy_setup
from tests.test_holiday import _setup as holiday_setup
from tests.test_phone_answers import _setup as phone_setup


@pytest.mark.parametrize("utterance", ["How do I turn off this appliance?", "Read the manual and turn on the lamp"])
async def test_outside_read_requires_a_new_user_turn(hass, utterance):
    with patch.object(outside, '_turn', return_value=('chat', 1, utterance)), patch.object(outside, '_reads', return_value={'chat': 1}):
        assert outside.blocked(hass)
    with patch.object(outside, '_turn', return_value=('chat', 2, 'Yes please')), patch.object(outside, '_reads', return_value={'chat': 1}):
        assert not outside.blocked(hass)


async def test_alarm_needs_chat_context_and_prior_request(hass):
    entry, api = await alarm_setup(hass)
    await _alarm(api, turn_on='heated blanket', confirmed=True)
    assert entry.runtime_data.lists['alarms'].find('Test') is None
    with chat_session.async_get_chat_session(hass) as session, async_get_chat_log(hass, session) as chat:
        chat.async_add_user_content(UserContent('Yes'))
        await _alarm(api, turn_on='heated blanket', confirmed=True)
        assert entry.runtime_data.lists['alarms'].find('Test') is None


@pytest.mark.parametrize("reply,changed,advance", [
    ('yes', False, 0), ('No thankyou', False, 0), ('Yes but tomorrow', False, 0),
    ('yes', True, 0), ('yes', False, 121),
])
async def test_alarm_confirmation_cannot_be_forged_or_replayed(hass, freezer, reply, changed, advance):
    entry, api = await alarm_setup(hass)
    with chat_session.async_get_chat_session(hass) as session, async_get_chat_log(hass, session) as chat:
        chat.async_add_user_content(UserContent('Schedule the blanket'))
        await _alarm(api, turn_on='heated blanket')
        await _alarm(api, turn_on='heated blanket', confirmed=True)  # model's same-turn self-confirmation
        assert entry.runtime_data.lists['alarms'].find('Test') is None
        freezer.tick(timedelta(seconds=advance))
        chat.async_add_user_content(UserContent(reply))
        out = await _alarm(api, turn_on='heated blanket', confirmed=True, time='22:00' if changed else '21:00')
        expected = reply == 'yes' and not changed and not advance
        assert ('set' in out) == expected
        if expected:
            item = entry.runtime_data.lists['alarms'].find('Test')
            entry.runtime_data.lists['alarms'].remove(item['id'])
            await _alarm(api, turn_on='heated blanket', confirmed=True)
            assert entry.runtime_data.lists['alarms'].find('Test') is None


async def test_alarm_other_conversation_cannot_confirm(hass):
    entry, api = await alarm_setup(hass)
    with chat_session.async_get_chat_session(hass) as session, async_get_chat_log(hass, session) as chat:
        chat.async_add_user_content(UserContent('Schedule the blanket'))
        await _alarm(api, turn_on='heated blanket')
    with chat_session.async_get_chat_session(hass) as session, async_get_chat_log(hass, session) as chat:
        chat.async_add_user_content(UserContent('Yes'))
        await _alarm(api, turn_on='heated blanket', confirmed=True)
    assert entry.runtime_data.lists['alarms'].find('Test') is None


async def test_forget_clears_recent_diagnostics_and_late_conversation(hass):
    entry = await privacy_setup(hass)
    rt = entry.runtime_data
    now = dt_util.utcnow().isoformat()
    old = (dt_util.utcnow() - timedelta(days=3)).isoformat()
    rt.speech.deliveries.extend([{'time': old, 'message': 'old'}, {'time': now, 'message': 'private'}])
    rt.sessions.memory.traces.append({'time': now, 'request': 'private'})
    rt.sessions.memory.topics.append({'saved_at': now, 'subject': 'private'})
    rt.voice_checks.start()
    rt.voice_checks.run['steps'].append({'time': now, 'reply': 'private'})
    rt.conversations._open['private-chat'] = {'start': now, 'turns': []}
    forget_recent(rt, 48)
    assert list(rt.speech.deliveries) == [{'time': old, 'message': 'old'}]
    assert not rt.sessions.memory.traces and not rt.sessions.memory.topics and rt.voice_checks.run is None
    rt.conversations._chat('private-chat', 'content_added', {'content': {'role': 'assistant', 'content': 'private'}})
    assert not rt.conversations._open


async def test_disabling_holiday_restores_heating(hass):
    entry = await holiday_setup(hass)
    calls = async_mock_service(hass, 'climate', 'set_temperature')
    await entry.runtime_data.holiday.async_begin()
    hass.config_entries.async_update_entry(entry, options={'holiday_mode': False})
    await hass.async_block_till_done()
    holiday = entry.runtime_data.holiday
    assert not holiday.active and not holiday.on and not holiday.heating
    assert [c.data['temperature'] for c in calls] == [7.0, 20]


async def test_failed_holiday_restore_retries_original_target(hass):
    entry = await holiday_setup(hass)
    async_mock_service(hass, 'climate', 'set_temperature')
    holiday = entry.runtime_data.holiday
    await holiday.async_begin()
    async def fail(call):
        raise HomeAssistantError('thermostat offline')
    hass.services.async_register('climate', 'set_temperature', fail)
    result = await holiday.async_end()
    assert result['heating_put_back'] == [] and result['heating_pending'] == ['climate.lounge']
    assert holiday.heating['climate.lounge']['temperature'] == 20 and not holiday.on
    calls = async_mock_service(hass, 'climate', 'set_temperature')
    holiday._tick(dt_util.utcnow())
    await hass.async_block_till_done()
    assert calls[-1].data['temperature'] == 20 and not holiday.heating


async def test_offline_thermostat_keeps_snapshot_and_prevents_new_holiday(hass):
    entry = await holiday_setup(hass)
    async_mock_service(hass, 'climate', 'set_temperature')
    holiday = entry.runtime_data.holiday
    await holiday.async_begin()
    hass.states.async_set('climate.lounge', 'unavailable')
    await holiday.async_end()
    assert holiday.describe()['heating_recovery_pending'] == ['climate.lounge']
    assert 'error' in await holiday.async_begin()
    assert holiday.heating['climate.lounge']['temperature'] == 20


@pytest.mark.parametrize('options', [{'phone_buttons': []}, {'phone_apps': ['mobile_app_other']}])
async def test_old_phone_answers_invalid_after_settings_change(hass, options):
    async_mock_service(hass, 'notify', 'mobile_app_pixel')
    async_mock_service(hass, 'notify', 'mobile_app_other')
    entry = await phone_setup(hass)
    handler = AsyncMock(return_value='Done')
    notifier = entry.runtime_data.notifier
    await notifier.async_ask('Run the heater?', handler)
    token = next(iter(notifier._questions))
    hass.config_entries.async_update_entry(entry, options=options)
    await hass.async_block_till_done()
    await notifier._async_answer(token, 'yes')
    handler.assert_not_awaited()


async def test_phone_answers_expire_and_unload_cancels(hass, freezer):
    async_mock_service(hass, 'notify', 'mobile_app_pixel')
    entry = await phone_setup(hass)
    handler = AsyncMock(return_value='Done')
    notifier = entry.runtime_data.notifier
    await notifier.async_ask('Run the heater?', handler)
    token = next(iter(notifier._questions))
    freezer.tick(timedelta(hours=2))
    await notifier._async_answer(token, 'yes')
    handler.assert_not_awaited()
    await notifier.async_ask('Run the heater?', handler)
    token = next(iter(notifier._questions))
    notifier.async_stop()
    await notifier._async_answer(token, 'yes')
    handler.assert_not_awaited()


async def test_holiday_recovery_survives_reload(hass):
    from custom_components.sentient.holiday import Holiday

    entry = await holiday_setup(hass)
    calls = async_mock_service(hass, 'climate', 'set_temperature')
    holiday = entry.runtime_data.holiday
    await holiday.async_begin()
    # Snapshot is saved before lowering heating, not just queued for a later flush.
    saved = await holiday._store.async_load()
    assert saved['heating']['climate.lounge']['temperature'] == 20
    holiday.async_stop()
    recovered = Holiday(hass, entry.entry_id, {'holiday_mode': False}, lambda: entry.runtime_data)
    await recovered.async_start()
    assert calls[-1].data['temperature'] == 20
    assert not recovered.heating and not recovered.on
    recovered.async_stop()


async def test_holiday_does_not_overwrite_manual_temperature(hass):
    entry = await holiday_setup(hass)
    calls = async_mock_service(hass, 'climate', 'set_temperature')
    holiday = entry.runtime_data.holiday
    await holiday.async_begin()
    hass.states.async_set('climate.lounge', 'heat', {'temperature': 22})
    await holiday.async_end()
    assert len(calls) == 1 and not holiday.heating
    assert hass.states.get('climate.lounge').attributes['temperature'] == 22


async def test_forget_during_speech_does_not_recreate_private_history(hass):
    import asyncio

    entry = await phone_setup(hass)
    started, release = asyncio.Event(), asyncio.Event()
    async def announce(call):
        started.set()
        await release.wait()
    hass.services.async_register('assist_satellite', 'announce', announce)
    task = hass.async_create_task(entry.runtime_data.speech.async_say('Private words', targets=['assist_satellite.kitchen']))
    await started.wait()
    forget_recent(entry.runtime_data, 48)
    release.set()
    await task
    speech = entry.runtime_data.speech
    assert not speech.deliveries and not speech._said and not speech._delivered_recent
    assert not any('Private words' in str(row) for row in entry.runtime_data.timeline.items)


async def test_no_thankyou_cancels_speaker_question(hass):
    entry = await phone_setup(hass)
    async_mock_service(hass, 'assist_satellite', 'start_conversation')
    handler = AsyncMock(return_value="Okay, I'll leave it.")
    speech = entry.runtime_data.speech
    await speech.async_ask('Run the heater?', targets=['assist_satellite.kitchen'], on_answer=handler)
    assert await speech.async_take_answer('assist_satellite.kitchen', 'No thankyou.') == "Okay, I'll leave it."
    assert await speech.async_take_answer('assist_satellite.kitchen', 'yes') is None
    handler.assert_awaited_once_with('no')


async def test_changed_device_invalidates_delayed_trust_answer(hass):
    from tests.test_trust import _setup, LAMP

    entry = await _setup(hass, trust_lights='ask')
    calls = async_mock_service(hass, 'light', 'turn_on')
    async_mock_service(hass, 'assist_satellite', 'start_conversation')
    await entry.runtime_data.learner._async_do(LAMP)
    hass.states.async_set('light.lamp', 'on', {'friendly_name': 'Lamp'})
    reply = await entry.runtime_data.speech.async_take_answer('assist_satellite.kitchen', 'yes')
    assert 'No action was taken' in reply and not calls


@pytest.mark.parametrize('device,user', [('other-speaker', 'owner'), ('kitchen', 'other-user')])
async def test_alarm_approval_is_bound_to_device_and_user(hass, device, user):
    from types import SimpleNamespace
    from custom_components.sentient import alarm_confirmation

    entry, _api = await alarm_setup(hass)
    context = SimpleNamespace(device_id='kitchen', context=SimpleNamespace(user_id='owner'))
    schedule = {'name': 'Heater', 'time': '21:00'}
    with patch.object(alarm_confirmation, '_turn', return_value=('chat', 1, 'Schedule the heater')):
        assert not alarm_confirmation.approved(entry.runtime_data, context, schedule, False)
    other = SimpleNamespace(device_id=device, context=SimpleNamespace(user_id=user))
    with patch.object(alarm_confirmation, '_turn', return_value=('chat', 2, 'Yes')):
        assert not alarm_confirmation.approved(entry.runtime_data, other, schedule, True)
        assert alarm_confirmation.approved(entry.runtime_data, context, schedule, True)


async def test_topic_change_cannot_revive_alarm_approval(hass):
    entry, api = await alarm_setup(hass)
    with chat_session.async_get_chat_session(hass) as session, async_get_chat_log(hass, session) as chat:
        chat.async_add_user_content(UserContent('Schedule the blanket'))
        await _alarm(api, turn_on='heated blanket')
        chat.async_add_user_content(UserContent('What is the weather?'))
        chat.async_add_user_content(UserContent('Yes'))
        await _alarm(api, turn_on='heated blanket', confirmed=True)
    assert entry.runtime_data.lists['alarms'].find('Test') is None


async def test_frost_respects_thermostat_minimum_and_still_restores(hass):
    entry = await holiday_setup(hass)
    hass.states.async_set('climate.lounge', 'heat', {'temperature': 20, 'min_temp': 10, 'max_temp': 30})
    calls = async_mock_service(hass, 'climate', 'set_temperature')
    holiday = entry.runtime_data.holiday
    await holiday.async_begin()
    assert calls[-1].data['temperature'] == 10
    hass.states.async_set('climate.lounge', 'heat', {'temperature': 10, 'min_temp': 10, 'max_temp': 30})
    await holiday.async_end()
    assert calls[-1].data['temperature'] == 20
