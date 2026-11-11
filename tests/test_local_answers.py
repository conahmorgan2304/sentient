"""When the AI fails, a few everyday questions are answered from the house itself, without any AI."""
from datetime import timedelta
from types import SimpleNamespace
from unittest.mock import Mock, patch

from homeassistant.helpers import intent
from homeassistant.util import dt as dt_util

from custom_components.sentient.local_answers import LEAD, is_question
from tests.test_voice_sessions_control import agent, request, response


def _failing_agent(hass, **options):
    obj, rt = agent(hass, {'voice_agent': 'conversation.primary', **options})
    rt.timeline = Mock()
    rt.speech = SimpleNamespace(guest_mode=False)
    soon = (dt_util.utcnow() + timedelta(hours=9)).isoformat()
    rt.alarms = SimpleNamespace(listing=lambda: [{"name": "Work", "on": True, "next": soon}])
    rt.watches = SimpleNamespace(watches=lambda: [])
    rt.visitors = SimpleNamespace(today=lambda: [{"description": "a courier"}], summary=lambda limit=8: ["9am: a courier with a parcel"])
    return obj, rt


async def _ask(obj, text):
    async def converse(agent_id, user_input):
        if agent_id == 'conversation.primary':
            out = response('broken')
            out.response.async_set_error(intent.IntentResponseErrorCode.UNKNOWN, 'Internal error')
            return out
        return response('Backup here.')

    with patch.object(obj, '_converse', side_effect=converse) as calls, patch.object(obj, 'async_write_ha_state'):
        result = await obj.async_process(request(text))
    return result.response.speech['plain']['speech'], [c.args[0] for c in calls.call_args_list]


async def test_security_question_is_answered_from_the_house_when_the_ai_fails(hass):
    hass.states.async_set('binary_sensor.back_door', 'on', {'device_class': 'door', 'friendly_name': 'Back door'})
    obj, rt = _failing_agent(hass)
    with patch('homeassistant.components.homeassistant.exposed_entities.async_should_expose', return_value=True):
        text, agents = await _ask(obj, 'Is the house locked up?')
    assert text.startswith(LEAD) and 'Back door is open' in text
    assert obj.backup_agent not in agents
    assert any(c.args == ('health', 'Answered without the AI', 'the AI failed: Internal error')
               and c.kwargs['decision']['kind'] == 'offline_answer' for c in rt.timeline.add.call_args_list)


async def test_alarms_and_visitors_are_answered_locally(hass):
    obj, _rt = _failing_agent(hass)
    text, _ = await _ask(obj, "When's my next alarm?")
    assert 'Your next alarm is Work' in text
    text, _ = await _ask(obj, 'Did anyone come round today?')
    assert 'a courier with a parcel' in text


async def test_other_requests_still_go_to_the_backup(hass):
    obj, _rt = _failing_agent(hass)
    text, agents = await _ask(obj, 'What time is it?')
    assert text == 'Backup here.' and agents[-1] == obj.backup_agent


async def test_switched_off_everything_goes_to_the_backup(hass):
    obj, _rt = _failing_agent(hass, offline_answers=False)
    text, _ = await _ask(obj, "When's my next alarm?")
    assert text == 'Backup here.'


async def test_guests_only_get_the_security_answer(hass):
    obj, rt = _failing_agent(hass)
    rt.speech.guest_mode = True
    text, _ = await _ask(obj, 'Did anyone come round today?')
    assert text == 'Backup here.'


def test_commands_are_never_treated_as_questions():
    assert not is_question('lock the front door')
    assert not is_question('open the garage')
    assert is_question('is the front door locked?')
    assert is_question('have I left a window open')
