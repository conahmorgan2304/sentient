"""Direct explanations use recorded facts without model calls or device writes."""
from datetime import timedelta
from types import SimpleNamespace
from unittest.mock import patch, AsyncMock
import pytest
from homeassistant.util import dt as dt_util
from custom_components.sentient.behaviour import answer
from custom_components.sentient.sessions import Sessions
from custom_components.sentient.explain import _said_because
from tests.test_voice_sessions_control import request, agent


def setup():
    sessions = Sessions({'conversation_sessions': True, 'behaviour_explanations': True})
    session = sessions.get(request('test'))
    session.turn = 1
    session.listen_reason = 'completed_single_request'
    return sessions, session


async def test_direct_explanation_does_not_call_model_or_consume_confirmation(hass):
    obj, rt = agent(hass, {'conversation_sessions': True, 'behaviour_explanations': True})
    session = rt.sessions.get(request('test'))
    session.turn = 1
    session.pending = {'summary': 'turn on hallway', 'actions': [], 'turn': 1, 'expires': dt_util.utcnow() + timedelta(seconds=60)}
    session.question = 'Shall I turn on the hallway light?'
    with patch.object(obj, '_async_process_legacy', new=AsyncMock()) as model:
        result = await obj.async_process(request('Why are you still listening?'))
    assert 'waiting for confirmation' in result.response.speech['plain']['speech']
    assert result.continue_conversation and session.pending
    model.assert_not_called()


@pytest.mark.parametrize('reason,expected', [('completed_single_request', 'single request'), ('discussion_follow_up', 'discussion'), ('unexpected', 'does not give')])
async def test_listening_uses_recorded_reason(hass, reason, expected):
    sessions, session = setup()
    session.listen_reason = reason
    text = await answer(hass, sessions, session, 'Why are you still listening?', request('').context, lambda: True)
    assert expected in text and 'hardware' in text


@pytest.mark.parametrize('blocked', ['off', 'guest', 'expired', 'other_owner'])
async def test_explanation_boundaries(hass, freezer, blocked):
    sessions, session = setup()
    if blocked == 'off': sessions.options['behaviour_explanations'] = False
    if blocked == 'expired': freezer.tick(timedelta(seconds=61))
    if blocked == 'other_owner': session = sessions.get(request('test', device='bedroom'))
    text = await answer(hass, sessions, session, 'Why are you still listening?', request('').context, lambda: blocked != 'guest')
    assert text is None or 'single request' not in text


async def test_action_reports_historical_uncertainty_and_filters_devices(hass):
    sessions, session = setup()
    session.last_action = {'actions': [{'entity_id': 'light.secret', 'status': 'verified'}, {'entity_id': 'light.lamp', 'status': 'attempted'}]}
    hass.states.async_set('light.lamp', 'on', {'friendly_name': 'Lamp'})
    with patch('custom_components.sentient.behaviour.async_should_expose', side_effect=lambda h, a, e: e == 'light.lamp'):
        text = await answer(hass, sessions, session, 'Did you actually do it?', request('').context, lambda: True)
    assert 'Lamp' in text and 'secret' not in text and 'not yet confirmed' in text and 'not a fresh check' in text


def test_spoken_and_held_occurrences_are_not_mixed():
    now = dt_util.utcnow().isoformat()
    rt = SimpleNamespace(timeline=SimpleNamespace(since=lambda *a: [
        {'kind': 'held', 'time': now, 'text': 'washing done', 'why': 'finished', 'detail': 'quiet hours'},
        {'kind': 'said', 'time': now, 'text': 'washing done', 'why': 'finished'}]))
    said = _said_because(rt, 'why did you say that', set(), 1)
    held = _said_because(rt, "why didn't you say that", set(), 1)
    assert said[0]['kind'] == 'said' and 'held_back' not in said[0]
    assert held[0]['kind'] == 'held' and held[0]['held_back'] == 'quiet hours'
    assert said[0]['recorded_at'] == now
