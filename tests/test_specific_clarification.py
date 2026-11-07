"""Selections keep meaning and ownership, never authorisation or an executable plan."""
from unittest.mock import AsyncMock, patch

import pytest
from homeassistant.core import Context

from custom_components.sentient.clarification import offer, consume, selection, CONF
from custom_components.sentient.sessions import current_session
from tests.test_voice_sessions_control import agent, request, response

OPTIONS = {'conversation_sessions': True, 'contextual_followups': True, CONF: True}
IDS = ['light.ceiling', 'light.counter']


def devices(hass):
    hass.states.async_set(IDS[0], 'on', {'friendly_name': 'Ceiling light'})
    hass.states.async_set(IDS[1], 'on', {'friendly_name': 'Counter lights'})


async def asking(hass, kind='device'):
    obj, rt = agent(hass, OPTIONS)
    devices(hass)
    session = rt.sessions.get(request(''))
    rt.sessions.begin(session, 'Turn off the kitchen light')
    session.running = True
    with patch('custom_components.sentient.clarification.async_should_expose', return_value=True):
        result = await offer(hass, session, IDS if kind == 'device' else [], Context(), kind)
    return obj, rt, session, result


@pytest.mark.parametrize('reply,expected', [('the second one', [IDS[1]]), ('2', [IDS[1]]), ('Counter lights', [IDS[1]]),
                                           ('both', IDS), ('all of them', IDS), ('No, I meant the counter lights', [IDS[1]])])
async def test_exact_selection_keeps_original_request_without_action_approval(hass, reply, expected):
    _, _, session, offered = await asking(hass)
    assert '1, Ceiling light; 2, Counter lights?' in offered['question']
    with patch('custom_components.sentient.clarification.async_should_expose', return_value=True):
        assert await consume(hass, session, reply, Context()) == {'resolved': True}
    assert session.entities == expected and session.pending is None
    assert session.command_turn  # a short answer still belongs to the original single command
    assert session.clarification_resolution['request'] == 'Turn off the kitchen light'
    assert 'selection only' in session.clarification_resolution['permission']


async def test_yes_is_not_a_device_choice_and_retries_are_bounded(hass):
    _, _, session, _ = await asking(hass)
    with patch('custom_components.sentient.clarification.async_should_expose', return_value=True):
        assert 'question' in await consume(hass, session, 'yes', Context())
        assert 'stop' in await consume(hass, session, 'yes please', Context())
    assert session.clarification is None and session.pending is None


async def test_changed_or_unexposed_choices_do_not_reindex_ordinals(hass):
    _, _, session, _ = await asking(hass)
    with patch('custom_components.sentient.clarification.async_should_expose', side_effect=lambda h, a, eid: eid == IDS[1]):
        assert 'stop' in await consume(hass, session, 'first', Context())
    assert not session.entities


async def test_renamed_choice_requires_fresh_question(hass):
    _, _, session, _ = await asking(hass)
    hass.states.async_set(IDS[0], 'on', {'friendly_name': 'Changed light'})
    with patch('custom_components.sentient.clarification.async_should_expose', return_value=True):
        assert 'stop' in await consume(hass, session, '1', Context())


async def test_permission_denial_hides_candidate_names(hass):
    obj, rt = agent(hass, OPTIONS)
    devices(hass)
    session = rt.sessions.get(request(''))
    session.running = True
    with patch.object(hass.auth, 'async_get_user', AsyncMock(return_value=None)):
        result = await offer(hass, session, IDS, Context(user_id='missing'))
    assert 'error' in result and 'Ceiling' not in str(result)
    assert session.clarification is None


@pytest.mark.parametrize('reply,value,unit', [('20 degrees', 20, None), ('20.5 C', 20.5, 'C'), ('68 Fahrenheit', 68, 'Fahrenheit')])
async def test_temperature_is_a_value_not_a_service_call(hass, reply, value, unit):
    _, _, session, _ = await asking(hass, 'temperature')
    assert await consume(hass, session, reply, Context()) == {'resolved': True}
    assert session.clarification_resolution['temperature'] == value
    assert session.clarification_resolution['unit'] == unit
    assert session.pending is None


async def test_new_request_discards_old_choices(hass):
    _, _, session, _ = await asking(hass)
    assert await consume(hass, session, 'What is the weather?', Context()) is None
    assert session.clarification is None and not session.question


async def test_real_agent_short_answer_reaches_model_with_original_request(hass):
    obj, rt = agent(hass, OPTIONS)
    devices(hass)
    calls = []
    async def model(turn):
        calls.append(turn)
        if len(calls) == 1:
            await offer(hass, current_session.get(), IDS, turn.context)
            return response('A different model question?')
        return response('Done.')
    with patch.object(obj, '_async_process_legacy', side_effect=model), patch('custom_components.sentient.clarification.async_should_expose', return_value=True):
        first = await obj.async_process(request('Turn off the kitchen light'))
        assert first.continue_conversation and 'Ceiling light' in first.response.speech['plain']['speech']
        last = await obj.async_process(request('the second one'))
    assert not last.continue_conversation
    assert 'Turn off the kitchen light' in calls[1].extra_system_prompt
    assert IDS[1] in calls[1].extra_system_prompt and 'selection only' in calls[1].extra_system_prompt
    assert len(calls) == 2  # no separate classification call


async def test_invalid_answers_do_not_call_model_or_repeat_forever(hass):
    obj, rt, session, _ = await asking(hass)
    session.running = False
    with patch.object(obj, '_async_process_legacy', AsyncMock()) as model, patch('custom_components.sentient.clarification.async_should_expose', return_value=True):
        first = await obj.async_process(request('perhaps'))
        last = await obj.async_process(request('not sure'))
    assert first.continue_conversation and not last.continue_conversation
    assert session.closed and session.pending is None
    model.assert_not_awaited()


async def test_different_device_cannot_use_choices(hass):
    obj, rt, session, _ = await asking(hass)
    with patch.object(obj, '_async_process_legacy', AsyncMock(return_value=response('Which device?'))):
        await obj.async_process(request('the second one', device='bedroom'))
    assert session.clarification and session.clarification_resolution is None


async def test_guarded_control_refuses_action_while_clarifying(hass):
    _, rt, session, _ = await asking(hass)
    session.authorised = True
    result = await rt.control.propose(session, [{'entity_id': IDS[0], 'action': 'turn_off'}], Context())
    assert 'error' in result and hass.states.get(IDS[0]).state == 'on'


async def test_option_off_leaves_model_path_unchanged(hass):
    obj, rt = agent(hass, {'conversation_sessions': True})
    with patch.object(obj, '_async_process_legacy', AsyncMock(return_value=response('Which device?'))) as model:
        await obj.async_process(request('the second one'))
    model.assert_awaited_once()


async def test_ambiguous_followup_offers_real_choices_locally(hass):
    obj, rt = agent(hass, OPTIONS)
    devices(hass)
    session = rt.sessions.get(request(''))
    session.goal = 'Adjust the lights'
    session.entities = IDS.copy()
    with patch.object(obj, '_async_process_legacy', AsyncMock()) as model, patch('custom_components.sentient.followups.async_should_expose', return_value=True), patch('custom_components.sentient.clarification.async_should_expose', return_value=True):
        result = await obj.async_process(request('Make that brighter'))
    assert 'Ceiling light' in result.response.speech['plain']['speech']
    assert result.continue_conversation and session.clarification
    model.assert_not_awaited()


def test_ambiguous_room_and_qualified_replies_are_not_selected():
    rows = [{'name': 'Ceiling', 'area': 'Kitchen', 'label': 'Ceiling (Kitchen)'}, {'name': 'Counter', 'area': 'Kitchen', 'label': 'Counter (Kitchen)'}]
    assert selection('kitchen', rows) is None
    assert selection('second one tomorrow', rows) is None
    assert selection('both', rows) == [0, 1]


async def test_close_discards_choices_and_resolutions(hass):
    _, rt, session, _ = await asking(hass)
    rt.sessions.close(session)
    assert session.clarification is None and session.clarification_resolution is None


async def test_temperature_reply_keeps_previously_selected_target(hass):
    obj, rt = agent(hass, OPTIONS)
    hass.states.async_set('climate.kitchen', 'heat', {'friendly_name': 'Kitchen heating'})
    session = rt.sessions.get(request(''))
    rt.sessions.begin(session, 'Set the kitchen heating')
    session.entities = ['climate.kitchen']
    session.running = True
    with patch('custom_components.sentient.clarification.async_should_expose', return_value=True):
        assert 'question' in await offer(hass, session, [], Context(), 'temperature')
        assert await consume(hass, session, '20 degrees', Context()) == {'resolved': True}
    assert session.entities == ['climate.kitchen']


async def test_tool_available_only_when_enabled_including_focused_and_guarded(hass):
    from homeassistant.helpers import llm
    from tests.test_phone_answers import _setup
    from custom_components.sentient.llm_api import SentientAPI
    from custom_components.sentient.voice_api import GuardedAPI
    from custom_components.sentient.focus import current_request
    entry = await _setup(hass, **(OPTIONS | {'focused_tools': True}))
    ctx = llm.LLMContext(platform='test', context=Context(), language='en', assistant='conversation', device_id=None)
    token = current_request.set('Turn off the kitchen light')
    try:
        for cls in (SentientAPI, GuardedAPI):
            api = await cls(hass).async_get_api_instance(ctx)
            assert 'sentient_clarify' in {t.name for t in api.tools}
        entry.runtime_data.options[CONF] = False
        api = await SentientAPI(hass).async_get_api_instance(ctx)
        assert 'sentient_clarify' not in {t.name for t in api.tools}
    finally:
        current_request.reset(token)


async def test_settings_change_discards_pending_choices(hass):
    from tests.test_phone_answers import _setup
    entry = await _setup(hass, **OPTIONS)
    session = entry.runtime_data.sessions.get(request(''))
    session.clarification = {'request': 'old choice'}
    hass.config_entries.async_update_entry(entry, options={CONF: False})
    await hass.async_block_till_done()
    assert session.closed and session.clarification is None


async def test_clarification_cannot_repopulate_after_session_closes_during_lookup(hass):
    from custom_components.sentient import clarification
    obj, rt = agent(hass, OPTIONS)
    session = rt.sessions.get(request(''))
    session.running = True
    async def lookup(*args):
        rt.sessions.close(session)
        return [{'entity_id': x, 'name': x, 'label': x, 'area': ''} for x in IDS]
    with patch.object(clarification, 'candidates', side_effect=lookup):
        assert 'error' in await offer(hass, session, IDS, Context())
    assert session.clarification is None


def test_setting_is_available_in_voice_settings():
    from custom_components.sentient.config_flow import _speech_schema
    from custom_components.sentient.websocket import GROUPS
    assert CONF in {str(k) for k in _speech_schema({}).schema}
    assert CONF in next(row[4] for row in GROUPS if row[0] == 'voice')


async def test_guarded_dispatch_rechecks_clarification_after_permission_await(hass):
    from unittest.mock import Mock
    from custom_components.sentient.control import Control
    from tests.test_voice_sessions_control import active
    from pytest_homeassistant_custom_component.common import async_mock_service
    _, session = active()
    hass.states.async_set('light.test', 'on')
    service = async_mock_service(hass, 'light', 'turn_off')
    control = Control(hass, {}, Mock())
    count = 0
    async def validate(*args):
        nonlocal count
        count += 1
        if count == 2:
            session.clarification = {'question': 'Which one?'}
    with patch.object(control, 'validate', side_effect=validate):
        result = await control.execute(session, [{'entity_id': 'light.test', 'action': 'turn_off'}], Context())
    assert not service
    assert result['actions'][0]['status'] == 'skipped'


async def test_sentient_acting_tool_is_blocked_during_choice(hass):
    from types import SimpleNamespace
    from custom_components.sentient.llm_api import _Tool
    import voluptuous as vol
    _, _, session, _ = await asking(hass)
    ran = []
    class Action(_Tool):
        name = 'test_action'
        acts = True
        parameters = vol.Schema({})
        async def async_call(self, hass, tool_input, llm_context):
            ran.append(True)
            return {}
    token = current_session.set(session)
    try:
        result = await Action().async_call(hass, SimpleNamespace(tool_args={}), None)
    finally:
        current_session.reset(token)
    assert getattr(result, 'data', result).get('error') and not ran


def test_device_name_cannot_conflict_with_an_ordinal():
    rows = [{'name': 'Second', 'area': '', 'label': 'Second'}, {'name': 'Other', 'area': '', 'label': 'Other'}]
    assert selection('second', rows) is None
    assert selection('2', rows) == [1]


async def test_completed_action_correction_retains_goal_and_prior_action(hass):
    obj, rt = agent(hass, OPTIONS)
    devices(hass)
    session = rt.sessions.get(request(''))
    rt.sessions.begin(session, 'Turn off the light')
    session.goal = 'Turn off the light'
    session.last_action = {'actions': [{'entity_id': IDS[0], 'status': 'verified'}]}
    rt.sessions.begin(session, 'No, the other light')
    session.running = True
    with patch('custom_components.sentient.clarification.async_should_expose', return_value=True):
        await offer(hass, session, IDS, Context())
        rt.sessions.begin(session, 'Counter lights')
        await consume(hass, session, 'Counter lights', Context())
    assert 'Turn off the light' in session.clarification_resolution['request']
    assert session.last_action['actions'][0]['entity_id'] == IDS[0]
    assert session.correction['previous_action'] == session.last_action
    assert session.pending is None
