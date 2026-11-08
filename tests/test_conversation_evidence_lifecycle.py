"""Conversation explanations must not lose recovery evidence or revive consent."""
import asyncio
from datetime import timedelta
from unittest.mock import AsyncMock, patch
import pytest
from homeassistant.util import dt as dt_util
from custom_components.sentient.behaviour import repeat_question
from tests.test_voice_sessions_control import agent, request


async def test_explanation_preserves_recovery_evidence(hass):
    obj, rt = agent(hass, {'conversation_sessions': True, 'behaviour_explanations': True, 'action_recovery': True})
    session = rt.sessions.get(request('test'))
    session.turn = 1
    record = {'rows': [], 'expires': dt_util.utcnow() + timedelta(minutes=2)}
    rt.control.recovery_records[session.owner] = record
    await obj.async_process(request('Did you actually do it?'))
    assert rt.control.recovery_records[session.owner] is record


@pytest.mark.parametrize('block', ['expired', 'guest', 'hidden', 'closed'])
async def test_stale_or_private_question_is_not_repeated(hass, block):
    obj, rt = agent(hass, {'conversation_sessions': True})
    session = rt.sessions.get(request('test'))
    session.pending = {'actions': [{'entity_id': 'light.private'}], 'expires': dt_util.utcnow() + timedelta(seconds=-1 if block == 'expired' else 60)}
    session.question = 'Shall I turn on the private light?'
    session.closed = block == 'closed'
    with patch('custom_components.sentient.behaviour.async_should_expose', return_value=block != 'hidden'):
        result = await repeat_question(hass, session, request('').context, lambda: block != 'guest')
    assert result == '' and session.pending is None


@pytest.mark.parametrize('during', ['lock', 'validation'])
async def test_confirmation_expiring_while_awaiting_never_dispatches(hass, freezer, during):
    obj, rt = agent(hass, {})
    session = rt.sessions.get(request('test'))
    session.turn = 2
    session.running = session.authorised = True
    session.pending = {'actions': [], 'before': {}, 'turn': 1, 'expires': dt_util.utcnow() + timedelta(seconds=5)}
    async def validate(*args):
        if during == 'validation': freezer.tick(timedelta(seconds=6))
    with patch.object(rt.control, 'validate', side_effect=validate), patch.object(rt.control, '_execute', new=AsyncMock()) as execute:
        if during == 'lock':
            await rt.control._lock.acquire()
            task = asyncio.create_task(rt.control.confirm(session, request('').context))
            await asyncio.sleep(0)
            freezer.tick(timedelta(seconds=6))
            rt.control._lock.release()
            result = await task
        else:
            result = await rt.control.confirm(session, request('').context)
    assert 'expired' in result['error']
    execute.assert_not_called()
    assert session.pending is None


async def test_expiry_during_final_device_validation_skips_dispatch(hass, freezer):
    from custom_components.sentient.control import snapshot
    obj, rt = agent(hass, {})
    session = rt.sessions.get(request('test'))
    session.turn = 2
    session.running = session.authorised = True
    hass.states.async_set('light.lamp', 'off')
    actions = [{'entity_id': 'light.lamp', 'action': 'turn_on'}]
    session.pending = {'actions': actions, 'before': {'light.lamp': snapshot(hass.states.get('light.lamp'))},
                       'turn': 1, 'expires': dt_util.utcnow() + timedelta(seconds=5)}
    calls = 0
    async def validate(*args):
        nonlocal calls
        calls += 1
        if calls == 3: freezer.tick(timedelta(seconds=6))
    from pytest_homeassistant_custom_component.common import async_mock_service
    service = async_mock_service(hass, 'light', 'turn_on')
    with patch.object(rt.control, 'validate', side_effect=validate):
        result = await rt.control.confirm(session, request('').context)
    assert result['actions'][0]['status'] == 'skipped'
    assert 'expired' in result['actions'][0]['reason']
    assert not service
