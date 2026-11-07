"""Generic phrase persistence, bounded translation work and final-response diagnostics."""
import asyncio
from copy import deepcopy
from unittest.mock import AsyncMock, Mock, patch

import pytest
from homeassistant.helpers import intent

from custom_components.sentient.ai import AIGate, AIUnavailable
from custom_components.sentient import language
from custom_components.sentient.const import DOMAIN
from custom_components.sentient.voice_checks import VoiceChecks
from custom_components.sentient.voice_timing import VoiceTimings, measure
from tests.test_automatic_language import setup, PACK
from tests.test_voice_sessions_control import agent, request, response
from tests.test_live_voice_checks import turn


async def test_saved_pack_restores_without_saving_personal_sentences(hass):
    rt, _ = await setup(hass, remember_language_phrases=True)
    with patch.object(language, 'async_generate', AsyncMock(return_value={'data': PACK})):
        await language.async_prepare_language(hass, rt.options)
    cache = rt.ai.speech_language_cache
    cache.kept[('es', 'Your private appointment is tomorrow')] = 'secret'
    with patch.object(rt.ai._store, 'async_delay_save') as save:
        rt.ai._save()
        data = save.call_args.args[0]()
    assert 'secret' not in str(data['language_phrases'])
    restored = AIGate(hass, 'restored', rt.options)
    with patch.object(restored._store, 'async_load', AsyncMock(return_value=data)):
        await restored.async_start()
    assert restored.speech_language_cache.pack == PACK
    assert not restored.speech_language_cache.kept
    with patch.object(language, 'gate', return_value=restored), patch.object(language, 'async_generate', AsyncMock(side_effect=AssertionError('no new call'))) as model:
        assert await language.async_prepare_language(hass, rt.options) == PACK
        assert await language.async_in_house_language(hass, rt.options, 'Smoke detected: Kitchen!', urgent=True) == 'Humo detectado: Kitchen!'
    model.assert_not_awaited()


@pytest.mark.parametrize('change', [{'remember_language_phrases': False}, {'automatic_language': False}, {'voice_language': 'de'}])
async def test_incompatible_saved_pack_is_not_restored(hass, change):
    rt, _ = await setup(hass, remember_language_phrases=True)
    saved = {'language_phrases': {'version': 1, 'language': 'es', 'pack': PACK}}
    restored = AIGate(hass, 'other', rt.options | change)
    with patch.object(restored._store, 'async_load', AsyncMock(return_value=saved)), patch.object(restored._store, 'async_delay_save') as save:
        await restored.async_start()
    assert getattr(restored, 'speech_language_cache', None) is None
    assert save.call_args.args[0]()['language_phrases'] is None


async def test_unrelated_settings_keep_generic_pack_but_forget_removes_it(hass):
    rt, _ = await setup(hass, remember_language_phrases=True)
    with patch.object(language, 'async_generate', AsyncMock(return_value={'data': PACK})):
        await language.async_prepare_language(hass, rt.options)
    rt.ai.speech_language_cache.kept[('es', 'Personal')] = 'Personal'
    entry = hass.config_entries.async_entries(DOMAIN)[0]
    hass.config_entries.async_update_entry(entry, options={'chime': False})
    await hass.async_block_till_done()
    assert rt.ai.speech_language_cache.pack == PACK
    assert not rt.ai.speech_language_cache.kept
    from custom_components.sentient.privacy import forget_recent
    forget_recent(rt)
    assert language.saved_phrases(rt.ai) is None
    with patch.object(rt.ai._store, 'async_delay_save') as save:
        rt.ai._save()
    assert save.call_args.args[0]()['language_phrases'] is None


async def test_disabling_remember_erases_saved_pack(hass):
    rt, _ = await setup(hass, remember_language_phrases=True)
    with patch.object(language, 'async_generate', AsyncMock(return_value={'data': PACK})):
        await language.async_prepare_language(hass, rt.options)
    entry = hass.config_entries.async_entries(DOMAIN)[0]
    hass.config_entries.async_update_entry(entry, options={'remember_language_phrases': False})
    await hass.async_block_till_done()
    assert language.saved_phrases(rt.ai) is None
    assert rt.ai.speech_language_cache.pack is None


async def test_concurrent_identical_sentences_use_one_translation(hass):
    rt, _ = await setup(hass)
    rt.options['automatic_language'] = False
    entered, release = asyncio.Event(), asyncio.Event()
    async def translate(*args):
        entered.set()
        await release.wait()
        return {'data': 'La puerta está abierta.'}
    with patch.object(language, 'async_generate', side_effect=translate) as ai:
        one = hass.async_create_task(language.async_in_house_language(hass, rt.options, 'The door is open.'))
        await entered.wait()
        two = hass.async_create_task(language.async_in_house_language(hass, rt.options, 'The door is open.'))
        await asyncio.sleep(0)
        release.set()
        assert await one == await two == 'La puerta está abierta.'
        ai.assert_awaited_once()
    assert not rt.ai.speech_language_cache.translation_locks


async def test_failure_cooldown_avoids_repeat_calls_then_retries(hass):
    rt, _ = await setup(hass)
    rt.options['automatic_language'] = False
    with patch.object(language, 'async_generate', AsyncMock(side_effect=[AIUnavailable('offline'), {'data': 'Lista.'}])) as ai:
        assert await language.async_in_house_language(hass, rt.options, 'The house is ready.') == 'The house is ready.'
        assert await language.async_in_house_language(hass, rt.options, 'The house is ready.') == 'The house is ready.'
        ai.assert_awaited_once()
        rt.ai.speech_language_cache.failed[('es', 'The house is ready.')] = 0
        assert await language.async_in_house_language(hass, rt.options, 'The house is ready.') == 'Lista.'
        assert ai.await_count == 2


async def test_final_translated_ending_is_seen_by_live_check(hass):
    rt, _ = await setup(hass)
    obj, fake = agent(hass, rt.options | {'conversation_sessions': True})
    checks = fake.voice_checks = VoiceChecks(hass, fake.options)
    checks.start(question='How is the system?', ending='No gracias', language='es')
    with patch.object(language, 'async_generate', AsyncMock(return_value={'data': PACK})), patch.object(obj, '_async_process_legacy', AsyncMock(return_value=response('Todo funciona correctamente.'))):
        first = await obj.async_process(request('How is the system?'))
        await obj.async_process(request('No gracias', cid=first.conversation_id))
    run = checks.status()
    assert run['status'] == 'passed_agent_checks'
    assert len(run['steps']) == 2
    assert run['steps'][0]['reply'].endswith('¿Algo más?')
    assert run['steps'][1]['reply'] == 'De acuerdo.'


@pytest.mark.parametrize('mode,phrase', [('single', 'Say ready.'), ('cancel', 'Cancel that.')])
def test_single_turn_live_checks_allow_playback_feedback(hass, mode, phrase):
    checks = VoiceChecks(hass)
    run = checks.start(mode)
    checks.observe(*turn(phrase, False, reply='Ready.'))
    assert checks.status()['status'] == 'passed_agent_checks'
    assert checks.confirm_heard(run['id'], True)['heard_replies'] is True


async def test_timing_records_final_response_without_content(hass):
    obj, rt = agent(hass, {'voice_timing_diagnostics': True})
    async def process(user_input):
        with measure('main_agent'):
            with measure('sentient_tools'):
                await asyncio.sleep(0)
        return response('Private result', listen=False)
    with patch.object(obj, '_async_process_impl', side_effect=process):
        await obj.async_process(request('Private request'))
    rows = rt.ai.voice_timings.describe()
    assert len(rows) == 1 and rows[0]['outcome'] == 'completed'
    assert rows[0]['stages_ms'].keys() == {'main_agent', 'sentient_tools'}
    assert 'Private' not in str(rows) and 'kitchen' not in str(rows)


async def test_timing_disabled_and_guest_do_not_record(hass):
    from types import SimpleNamespace
    for opts, guest in [({}, False), ({'voice_timing_diagnostics': True}, True)]:
        obj, rt = agent(hass, opts)
        rt.ai.voice_timings = VoiceTimings()
        rt.speech = SimpleNamespace(guest_mode=guest)
        with patch.object(obj, '_async_process_impl', AsyncMock(return_value=response())):
            await obj.async_process(request('Private request'))
        assert not rt.ai.voice_timings.describe()


def test_timing_forget_during_turn_cannot_recreate_history():
    timing = VoiceTimings()
    with timing.turn():
        with measure('main_agent'):
            timing.clear()
    assert not timing.describe()


def test_timing_cancellation_and_bounded_history():
    timing = VoiceTimings()
    for _ in range(25):
        with timing.turn():
            pass
    assert len(timing.describe()) == 20
    with pytest.raises(asyncio.CancelledError):
        with timing.turn():
            with measure('main_agent'):
                raise asyncio.CancelledError
    assert timing.describe()[0]['outcome'] == 'cancelled'


def test_settings_expose_optional_controls():
    from custom_components.sentient.config_flow import _speech_schema
    from custom_components.sentient.websocket import GROUPS
    keys = {'remember_language_phrases', 'voice_timing_diagnostics'}
    assert keys <= {str(k) for k in _speech_schema({}).schema}
    assert keys <= set(next(row[4] for row in GROUPS if row[0] == 'voice'))


async def test_real_unload_reload_restores_pack_before_first_urgent_speech(hass):
    rt, _ = await setup(hass, remember_language_phrases=True)
    with patch.object(language, 'async_generate', AsyncMock(return_value={'data': PACK})):
        await language.async_prepare_language(hass, rt.options)
    entry = hass.config_entries.async_entries(DOMAIN)[0]
    assert await hass.config_entries.async_unload(entry.entry_id)
    assert await hass.config_entries.async_setup(entry.entry_id)
    await hass.async_block_till_done()
    assert entry.runtime_data.ai.speech_language_cache.pack == PACK
    with patch.object(language, 'async_generate', AsyncMock(side_effect=AssertionError('urgent must not call AI'))):
        assert await language.async_in_house_language(hass, entry.runtime_data.options, 'Gas detected: Kitchen!', urgent=True) == 'Gas detectado: Kitchen!'


async def test_settings_clear_invalidates_inflight_pack_with_persistence(hass):
    rt, _ = await setup(hass, remember_language_phrases=True)
    entered, release = asyncio.Event(), asyncio.Event()
    async def generate(*args):
        entered.set()
        await release.wait()
        return {'data': PACK}
    with patch.object(language, 'async_generate', side_effect=generate):
        task = hass.async_create_task(language.async_prepare_language(hass, rt.options))
        await entered.wait()
        language.clear_translations(rt.ai)
        release.set()
        assert await task is None
    assert rt.ai._storage_data()['language_phrases'] is None


async def test_clear_during_waiting_translation_leaves_no_new_calls(hass):
    rt, _ = await setup(hass)
    rt.options['automatic_language'] = False
    entered, release = asyncio.Event(), asyncio.Event()
    async def generate(*args):
        entered.set()
        await release.wait()
        return {'data': 'Lista.'}
    with patch.object(language, 'async_generate', side_effect=generate) as ai:
        one = hass.async_create_task(language.async_in_house_language(hass, rt.options, 'The house is ready.'))
        await entered.wait()
        two = hass.async_create_task(language.async_in_house_language(hass, rt.options, 'The house is ready.'))
        await asyncio.sleep(0)
        language.clear_translations(rt.ai)
        release.set()
        assert await one == await two == 'The house is ready.'
        ai.assert_awaited_once()
    assert not rt.ai.speech_language_cache.kept


async def test_cancel_during_live_check_preparation_does_not_recreate_run(hass):
    rt, _ = await setup(hass)
    checks = VoiceChecks(hass, rt.options)
    entered, release = asyncio.Event(), asyncio.Event()
    async def translate(*args, **kwargs):
        entered.set()
        await release.wait()
        return '¿Cómo está el sistema?'
    with patch.object(language, 'async_in_house_language', side_effect=translate), patch.object(language, 'async_prepare_language', AsyncMock(return_value=PACK)):
        task = hass.async_create_task(checks.async_start())
        await entered.wait()
        checks.stop()
        release.set()
        with pytest.raises(ValueError):
            await task
    assert checks.status() is None


async def test_translated_live_check_matches_its_actual_displayed_phrases(hass):
    rt, _ = await setup(hass)
    checks = VoiceChecks(hass, rt.options)
    with patch.object(language, 'async_in_house_language', AsyncMock(return_value='¿Cómo está el sistema?')), patch.object(language, 'async_prepare_language', AsyncMock(return_value=PACK)):
        run = await checks.async_start()
    checks.observe(*turn(run['question'], True, reply='¿Algo más?'))
    checks.observe(*turn(run['ending'], False, reply='De acuerdo.'))
    assert checks.status()['status'] == 'passed_agent_checks'


async def test_reply_from_old_request_cannot_populate_new_live_check(hass):
    obj, rt = agent(hass, {'conversation_sessions': True})
    checks = rt.voice_checks = VoiceChecks(hass)
    entered, release = asyncio.Event(), asyncio.Event()
    async def process(user_input):
        entered.set()
        await release.wait()
        return response('Anything else?', listen=True)
    with patch.object(obj, '_async_process_impl', side_effect=process):
        task = hass.async_create_task(obj.async_process(request('How is the system?')))
        await entered.wait()
        checks.start()
        release.set()
        await task
    assert not checks.status()['steps']


@pytest.mark.parametrize('bad', [[], 'bad provider reply'])
async def test_malformed_provider_envelope_falls_back_without_repeated_calls(hass, bad):
    rt, _ = await setup(hass)
    rt.options['automatic_language'] = False
    with patch.object(language, 'async_generate', AsyncMock(return_value=bad)) as ai:
        for _ in range(2):
            assert await language.async_in_house_language(hass, rt.options, 'The door is open.') == 'The door is open.'
        ai.assert_awaited_once()


async def test_generic_pack_never_persists_unexpected_model_fields(hass):
    rt, _ = await setup(hass, remember_language_phrases=True)
    with patch.object(language, 'async_generate', AsyncMock(return_value={'data': PACK | {'extra': 'unexpected detail'}})):
        await language.async_prepare_language(hass, rt.options)
    assert 'unexpected detail' not in str(rt.ai._storage_data()['language_phrases'])
