"""Language lifecycle, immediate alerts and cancellation/replay boundaries."""
import asyncio
from dataclasses import replace
from types import SimpleNamespace
from unittest.mock import AsyncMock, Mock, patch

import pytest
from homeassistant.helpers import chat_session
from homeassistant.components.conversation.chat_log import UserContent, async_get_chat_log
from homeassistant.helpers import llm
from pytest_homeassistant_custom_component.common import async_mock_service

from custom_components.sentient.language import async_in_house_language, clear_translations
from custom_components.sentient.privacy import forget_recent
from custom_components.sentient.voice_locale import safety_sentence
from custom_components.sentient.answers import classify
from custom_components.sentient.const import DOMAIN
from custom_components.sentient.voice_actions import speech_attempt
from custom_components.sentient.config_flow import _speech_schema
from custom_components.sentient.websocket import GROUPS
from tests.test_language import _setup
from tests.test_voice_sessions_control import agent, request, response
from tests.test_alarm_guard import _setup as alarm_setup, _alarm


def runtime(hass):
    return hass.config_entries.async_entries(DOMAIN)[0].runtime_data


@pytest.mark.parametrize('text,lang,expected', [
    ('Smoke detected: Keittiö!', 'fi', 'Savua havaittu: Keittiö!'),
    ('Carbon monoxide detected in the bedroom!', 'fr', 'Monoxyde de carbone détecté: bedroom!'),
    ('Water leak detected: Hall!', 'fi', 'Vesivuoto havaittu: Hall!'),
    ('The house alarm has gone off!', 'fr', 'Alarme: house alarm!'),
])
def test_fixed_alerts_keep_names(text, lang, expected):
    assert safety_sentence(text, lang) == expected


async def test_urgent_unknown_sentence_never_calls_or_waits_for_ai(hass):
    ai = await _setup(hass)
    out = await async_in_house_language(hass, runtime(hass).options, 'The oven is dangerously hot.', urgent=True)
    assert out == 'The oven is dangerously hot.' and not ai


async def test_privacy_and_settings_clear_translation_cache(hass):
    ai = await _setup(hass)
    rt = runtime(hass)
    await async_in_house_language(hass, rt.options, 'The parcel is here.')
    assert rt.ai.speech_language_cache.kept
    forget_recent(rt, 48)
    assert not rt.ai.speech_language_cache.kept
    await async_in_house_language(hass, rt.options, 'The parcel is here.')
    assert len(ai) == 2
    entry = hass.config_entries.async_entries(DOMAIN)[0]
    hass.config_entries.async_update_entry(entry, options={'speak_house_language': False})
    await hass.async_block_till_done()
    assert not rt.ai.speech_language_cache.kept


async def test_forgetting_during_translation_cannot_recreate_cache_or_question(hass):
    await _setup(hass)
    rt = runtime(hass)
    hass.states.async_set('assist_satellite.kitchen', 'idle', {'supported_features': 3})
    entered, release = asyncio.Event(), asyncio.Event()
    async def translate(*args):
        entered.set()
        await release.wait()
        return {'data': 'Yksityinen kysymys?'}
    asked = async_mock_service(hass, 'assist_satellite', 'start_conversation')
    with patch('custom_components.sentient.language.async_generate', side_effect=translate):
        task = hass.async_create_task(rt.speech.async_ask('Shall I remember your secret?', on_answer=AsyncMock()))
        await entered.wait()
        forget_recent(rt, 48)
        release.set()
        result = await task
    assert result.reason == 'cancelled' and not asked
    assert not rt.ai.speech_language_cache.kept and not rt.speech._questions


async def test_question_without_listening_translates_only_once_on_failure(hass):
    ai = await _setup(hass, reply=None)
    said = async_mock_service(hass, 'assist_satellite', 'announce')
    out = await runtime(hass).speech.async_ask('Is there anything else?')
    assert len(ai) == 1 and said and out.message == 'Is there anything else?'


async def test_question_records_and_returns_spoken_translation(hass):
    await _setup(hass, reply='Voinko auttaa vielä jossakin?')
    hass.states.async_set('assist_satellite.kitchen', 'idle', {'supported_features': 3})
    asked = async_mock_service(hass, 'assist_satellite', 'start_conversation')
    rt = runtime(hass)
    handler = AsyncMock(return_value='Okay')
    result = await rt.speech.async_ask('Is there anything else?', on_answer=handler)
    assert result.message == 'Voinko auttaa vielä jossakin?'
    assert result.message == rt.speech.recent_said()[0] == rt.speech._questions['assist_satellite.kitchen']['asked']
    assert result.message in asked[0].data['extra_system_prompt']
    await rt.speech.async_take_answer('assist_satellite.kitchen', 'Ei kiitos')
    handler.assert_awaited_once_with('no')


@pytest.mark.parametrize('text,lang,kind', [('kyllä', 'fi', 'yes'), ('ei kiitos', 'fi', 'no'),
    ('oui', 'fr', 'yes'), ('non merci', 'fr', 'no'), ('oui mais demain', 'fr', None),
    ('kyllä huomenna', 'fi', None), ('kyllä', None, None)])
def test_local_consent_requires_exact_reply(text, lang, kind):
    assert classify(text, language=lang) == kind


@pytest.mark.parametrize('lang,ending', [('fi', 'Ei'), ('fr', 'Non merci')])
async def test_local_discussion_ends_without_calling_model_again(hass, lang, ending):
    obj, rt = agent(hass, {'conversation_sessions': True, 'speak_house_language': True})
    with patch.object(obj, '_async_process_legacy', AsyncMock(return_value=response('Report.'))) as model:
        result = await obj.async_process(replace(request('How is the system?'), language=lang))
        assert result.continue_conversation
        result = await obj.async_process(replace(request(ending), language=lang))
    assert not result.continue_conversation and model.await_count == 1


async def test_finnish_alarm_confirmation_is_separate_user_turn(hass):
    entry, _ = await alarm_setup(hass)
    entry.runtime_data.options['speak_house_language'] = True
    api = await llm.async_get_api(hass, DOMAIN, llm.LLMContext(platform='t', context=None, language='fi', assistant=None, device_id=None))
    with chat_session.async_get_chat_session(hass) as session, async_get_chat_log(hass, session) as chat:
        chat.async_add_user_content(UserContent('Schedule the blanket'))
        await _alarm(api, turn_on='heated blanket')
        assert 'set' not in await _alarm(api, turn_on='heated blanket', confirmed=True)
        chat.async_add_user_content(UserContent('kyllä'))
        assert (await _alarm(api, turn_on='heated blanket', confirmed=True))['set'] == 'Test'


@pytest.mark.parametrize("ending", ["Actually, leave it", "Stop listening", "No thankyou"])
async def test_running_request_can_be_cancelled_without_backup_or_undo(hass, ending):
    obj, rt = agent(hass, {'conversation_sessions': True, 'conversation_recovery': True})
    entered = asyncio.Event()
    async def model(turn):
        entered.set()
        await asyncio.Event().wait()
    with patch.object(obj, '_async_process_legacy', side_effect=model) as model_call:
        first = hass.async_create_task(obj.async_process(request('Turn on the lamp')))
        await entered.wait()
        result = await obj.async_process(request(ending))
        with pytest.raises(asyncio.CancelledError):
            await first
    assert model_call.await_count == 1
    assert 'may already have happened' in result.response.speech['plain']['speech']
    assert not result.continue_conversation and not obj._turn_tasks and not rt.sessions.describe()


async def test_other_device_cannot_cancel_running_request(hass):
    obj, rt = agent(hass, {'conversation_sessions': True, 'conversation_recovery': True})
    entered, release = asyncio.Event(), asyncio.Event()
    async def model(turn):
        entered.set()
        await release.wait()
        return response('Done')
    with patch.object(obj, '_async_process_legacy', side_effect=model):
        first = hass.async_create_task(obj.async_process(request('Turn on the lamp')))
        await entered.wait()
        await obj.async_process(request('Cancel that', device='bedroom'))
        assert not first.done()
        release.set()
        await first


async def test_cancellation_off_preserves_model_path(hass):
    obj, rt = agent(hass, {'conversation_sessions': True, 'conversation_recovery': False})
    with patch.object(obj, '_async_process_legacy', AsyncMock(return_value=response('Okay'))) as model:
        await obj.async_process(request('Actually, leave it'))
    assert model.await_count == 1


@pytest.mark.parametrize('marked,option', [(True, False), (False, True)])
async def test_uncertain_action_is_not_sent_to_backup(hass, marked, option):
    obj, rt = agent(hass, {'voice_agent': 'conversation.primary', 'conversation_sessions': True, 'conversation_recovery': option})
    async def fail(*args):
        if marked:
            speech_attempt.get()['action_started'] = True
        raise TimeoutError()
    with patch.object(obj, '_converse', side_effect=fail) as calls:
        result = await obj.async_process(request('Turn on the lamp'))
    assert calls.await_count == 1
    assert "haven't sent it to the backup" in result.response.speech['plain']['speech']


async def test_ambiguous_other_light_asks_without_executing(hass):
    obj, rt = agent(hass, {'conversation_sessions': True, 'contextual_followups': True})
    rt.sessions.get(request('')).goal = 'Turn off the lamp'
    with patch.object(obj, '_async_process_legacy', AsyncMock()) as model:
        result = await obj.async_process(request('No, the other light'))
    assert result.continue_conversation and result.response.speech['plain']['speech'] == 'Which device or room do you mean?'
    model.assert_not_awaited()


def test_recovery_has_settings_control():
    assert 'conversation_recovery' in {str(key) for key in _speech_schema({}).schema}
    assert 'conversation_recovery' in next(row[4] for row in GROUPS if row[0] == 'voice')


async def test_late_action_tool_cannot_run_after_session_cancel(hass):
    import voluptuous as vol
    from custom_components.sentient.llm_api import _Tool
    from custom_components.sentient.sessions import current_session

    ran = []
    class LateAction(_Tool):
        name = 'late_action'
        acts = True
        parameters = vol.Schema({})
        async def async_call(self, hass, tool_input, llm_context):
            ran.append(True)
            return {'done': True}
    obj, rt = agent(hass, {'conversation_sessions': True})
    session = rt.sessions.get(request(''))
    rt.sessions.close(session)
    token = current_session.set(session)
    try:
        result = await LateAction().async_call(hass, SimpleNamespace(tool_args={}), None)
    finally:
        current_session.reset(token)
    result = getattr(result, 'data', result)
    assert 'not started' in result['error'] and not ran


async def test_translated_delivery_cancellation_keeps_original_deduplication_key(hass):
    await _setup(hass)
    router = runtime(hass).speech
    entered = asyncio.Event()
    async def announce(call):
        if call.data['entity_id'] == ['assist_satellite.slow']:
            entered.set()
            await asyncio.Event().wait()
    hass.services.async_register('assist_satellite', 'announce', announce)
    task = hass.async_create_task(router._speak('Pesukone on valmis.', ['assist_satellite.ready', 'assist_satellite.slow'], original="The washing machine is finished."))
    await entered.wait()
    await asyncio.sleep(0)
    task.cancel()
    with pytest.raises(asyncio.CancelledError):
        await task
    assert ('the washing machine is finished.', 'assist_satellite.ready') in router._delivered_recent
    assert router.deliveries[0]['message'] == 'Pesukone on valmis.'


async def test_unload_clears_runtime_translation_cache(hass):
    await _setup(hass)
    rt = runtime(hass)
    await async_in_house_language(hass, rt.options, 'The parcel is here.')
    assert rt.ai.speech_language_cache.kept
    entry = hass.config_entries.async_entries(DOMAIN)[0]
    assert await hass.config_entries.async_unload(entry.entry_id)
    assert not rt.ai.speech_language_cache.kept
