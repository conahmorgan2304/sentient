"""Correct a task without silent replay and investigate only recorded evidence."""
from datetime import timedelta
from unittest.mock import AsyncMock, patch

import pytest
from homeassistant.core import Context
from homeassistant.helpers import llm
from homeassistant.util import dt as dt_util
from pytest_homeassistant_custom_component.common import async_mock_service

from custom_components.sentient.sessions import current_session, Sessions
from custom_components.sentient.troubleshoot import report
from custom_components.sentient.explain import async_explain
from tests.test_voice_sessions_control import agent, request, response
from tests.test_phase3 import _setup


@pytest.mark.parametrize('correction', ['No, the landing', 'I meant the landing', 'Not that light', 'Actually, the landing'])
async def test_correction_preserves_original_goal_and_targets(correction):
    sessions = Sessions({'conversation_sessions': True})
    session = sessions.get(request('Turn off the kitchen light'))
    sessions.begin(session, 'Turn off the kitchen light')
    session.entities = ['light.kitchen']
    sessions.begin(session, correction)
    assert session.goal == 'Turn off the kitchen light'
    assert session.correction['previous_entities'] == ['light.kitchen']
    assert session.correction['request'] == correction
    sessions.begin(session, 'What is the weather?')
    assert session.correction is None


async def test_guarded_correction_acknowledges_prior_action_and_waits(hass):
    obj, rt = agent(hass, {'conversation_sessions': True, 'guarded_control': True,
                           'voice_undo': True, 'voice_agent': 'conversation.ai'})
    for eid in ['light.kitchen', 'light.landing']:
        hass.states.async_set(eid, 'on')
    calls = []
    async def change(call):
        calls.append(call.data['entity_id'])
        hass.states.async_set(call.data['entity_id'], 'off')
    hass.services.async_register('light', 'turn_off', change)
    async def model(_agent, user_input):
        eid = 'light.landing' if user_input.text.startswith('No') else 'light.kitchen'
        await rt.control.propose(current_session.get(), [{'entity_id': eid, 'action': 'turn_off'}], user_input.context)
        return response('Done.')  # never trust an unsupported completion claim
    with patch.object(obj, '_guarded_configuration', return_value=True), patch.object(obj, '_converse', side_effect=model), patch('custom_components.sentient.control.async_should_expose', return_value=True):
        await obj.async_process(request('Turn off the kitchen light'))
        result = await obj.async_process(request('No, I meant the landing'))
        text = result.response.speech['plain']['speech']
        assert 'kitchen: requested state reported' in text and 'undo that' in text
        assert 'Shall I turn off landing?' in text
        assert calls == ['light.kitchen']
        await obj.async_process(request('yes'))
        assert calls == ['light.kitchen', 'light.landing']


async def test_correcting_pending_lock_never_executes_old_target(hass):
    obj, rt = agent(hass, {'guarded_control': True, 'voice_agent': 'conversation.ai'})
    for eid in ['lock.front', 'lock.back']:
        hass.states.async_set(eid, 'locked')
    calls = async_mock_service(hass, 'lock', 'unlock')
    async def model(_agent, user_input):
        session = current_session.get()
        eid = 'lock.back' if session.correction else 'lock.front'
        if session.correction:
            assert session.correction['previous_pending'][0]['entity_id'] == 'lock.front'
        await rt.control.propose(session, [{'entity_id': eid, 'action': 'unlock'}], user_input.context)
        return response()
    with patch.object(obj, '_guarded_configuration', return_value=True), patch.object(obj, '_converse', side_effect=model), patch('custom_components.sentient.control.async_should_expose', return_value=True):
        await obj.async_process(request('Unlock the front door'))
        await obj.async_process(request('No, the back door'))
        assert not calls
        await obj.async_process(request('yes'))
    assert len(calls) == 1 and calls[0].data['entity_id'] == 'lock.back'


async def test_undo_offer_cancels_replacement_and_preserves_manual_changes(hass):
    obj, rt = agent(hass, {'guarded_control': True, 'voice_undo': True, 'voice_agent': 'conversation.ai'})
    hass.states.async_set('light.kitchen', 'on')
    hass.states.async_set('light.landing', 'on')
    async def off(call):
        hass.states.async_set(call.data['entity_id'], 'off')
    hass.services.async_register('light', 'turn_off', off)
    restores = async_mock_service(hass, 'light', 'turn_on')
    async def model(_agent, user_input):
        eid = 'light.landing' if current_session.get().correction else 'light.kitchen'
        await rt.control.propose(current_session.get(), [{'entity_id': eid, 'action': 'turn_off'}], user_input.context)
        return response()
    with patch.object(obj, '_guarded_configuration', return_value=True), patch.object(obj, '_converse', side_effect=model), patch('custom_components.sentient.control.async_should_expose', return_value=True):
        await obj.async_process(request('Turn off the kitchen light'))
        await obj.async_process(request('No, the landing'))
        hass.states.async_set('light.kitchen', 'on', {'brightness': 100})
        result = await obj.async_process(request('undo that'))
    assert not restores
    assert 'changed since' in result.response.speech['plain']['speech']
    assert hass.states.get('light.landing').state == 'on'
    assert rt.sessions.get(request('next')).pending is None


async def test_evidence_distinguishes_held_sent_and_current_state(hass):
    entry = await _setup(hass)
    rt = entry.runtime_data
    rt.timeline.add('held', 'Someone at the gate', 'held back: quiet hours', reason='quiet_hours')
    rt.timeline.add('said', 'Someone at the gate', 'Kitchen')
    rt.timeline.add('held', 'Garden cat', 'held back: duplicate', reason='duplicate')
    hass.states.async_set('binary_sensor.gate', 'unavailable')
    out = report(hass, rt, "Why didn't the gate announcement play?", 1)
    assert len(out['evidence']) == 2
    assert out['current_readings'][0]['status'] == 'unknown'
    assert 'audible' in out['findings'][1]['finding'] or 'audible' in out['findings'][0]['finding']
    assert out['next_question'] == 'Which time or occurrence do you mean?'
    assert any('Current device state' in x for x in out['unknowns'])


async def test_missing_evidence_does_not_claim_no_event(hass):
    entry = await _setup(hass)
    entry.runtime_data.timeline.add('held', 'Aggregate report', 'quiet hours')
    out = report(hass, entry.runtime_data, 'gate', 1)
    assert not out['evidence'] and not out['findings']
    assert any('missing records do not prove' in x for x in out['unknowns'])
    assert out['next_question'].startswith('Roughly when')
    assert report(hass, entry.runtime_data, 'why did that happen', 1)['next_question'].startswith('Which device')


async def test_incidents_and_delivery_failures_are_not_assumed_causal(hass, freezer):
    entry = await _setup(hass, security_incidents=True)
    rt = entry.runtime_data
    rt.incidents.add('camera.gate', 'Person at gate', 'detection')
    rt.speech.failures['Kitchen'] = (dt_util.utcnow(), 'Connection failed')
    out = report(hass, rt, 'gate', 1)
    assert out['evidence'][0]['source'] == 'incident'
    assert 'not linked' in out['delivery_failures'][0]['relation']
    freezer.tick(timedelta(hours=2))
    out = report(hass, rt, 'gate', 1)
    assert not out['evidence'] and not out['delivery_failures']


async def test_diagnostic_records_are_bounded_and_unknown_context_stays_unknown(hass):
    entry = await _setup(hass)
    for n in range(50):
        entry.runtime_data.timeline.add('held', f'Gate report {n}', 'quiet hours')
    hass.states.async_set('light.landing', 'on', context=Context())
    out = await async_explain(hass, entry.runtime_data, 'light.landing', float('inf'))
    assert out['by'].startswith('unknown:')
    assert len(report(hass, entry.runtime_data, 'gate', 1)['evidence']) == 20


async def test_why_tool_retains_investigation_subject_for_followup(hass):
    from custom_components.sentient.llm_api import WhyTool
    entry = await _setup(hass, conversation_sessions=True)
    session = entry.runtime_data.sessions.get(request('Why did the gate alert fail?'))
    session.running = True
    token = current_session.set(session)
    ctx = llm.LLMContext(platform='test', context=Context(), language='en', assistant='conversation', device_id=None)
    try:
        await WhyTool().async_call(hass, llm.ToolInput(tool_name='sentient_why', tool_args={'what': 'gate'}), ctx)
    finally:
        current_session.reset(token)
    assert session.investigation['subject'] == 'gate'
    assert 'fresh evidence' in session.investigation['note']


async def test_ambiguous_correction_still_acknowledges_previous_guarded_result(hass):
    obj, rt = agent(hass, {'conversation_sessions': True})
    session = rt.sessions.get(request('start'))
    session.goal = 'Turn off kitchen'
    session.last_action = {'summary': 'turn off kitchen', 'actions': [
        {'entity_id': 'light.kitchen', 'status': 'attempted'}]}
    with patch.object(obj, '_async_process_legacy', AsyncMock(return_value=response('Which light did you mean?'))):
        result = await obj.async_process(request('No, not that light'))
    text = result.response.speech['plain']['speech']
    assert 'result unverified' in text
    assert text.endswith('Which light did you mean?')
    assert result.continue_conversation


async def test_health_issues_are_labelled_cached_not_historical_cause(hass):
    entry = await _setup(hass)
    with patch.object(entry.runtime_data.health, 'describe', return_value=['Gate camera is offline']):
        out = report(hass, entry.runtime_data, 'system health', 1)
    assert out['current_health']['issues'] == ['Gate camera is offline']
    assert 'cached' in out['current_health']['note']
    assert not out['findings']
