"""Fresh wake-word context carries evidence, not permission or listening."""
from datetime import timedelta
from unittest.mock import patch
import pytest
from custom_components.sentient.sessions import Sessions
from custom_components.sentient.followups import restore_recent, classify, refresh
from tests.test_voice_sessions_control import request, response


def setup():
    sessions = Sessions({'conversation_sessions': True, 'contextual_followups': True,
                         'recent_followup_context': True})
    old = sessions.get(request('Turn on the kitchen light'))
    sessions.begin(old, 'Turn on the kitchen light')
    old.entities = ['light.kitchen']
    old.last_action = {'summary': 'turn on kitchen', 'actions': [{'entity_id': 'light.kitchen', 'status': 'verified'}]}
    old.undo = [{'secret': 'permission must not carry'}]
    sessions.finish(old, response())
    new = sessions.get(request('Actually, the hallway', cid='new'))
    return sessions, old, new


async def test_fresh_wake_retains_evidence_without_authority(hass):
    sessions, old, new = setup()
    with patch('custom_components.sentient.followups.async_should_expose', return_value=True):
        await restore_recent(hass, sessions, new, 'Actually, the hallway', request('').context, lambda: True)
    sessions.begin(new, 'Actually, the hallway')
    assert new.correction['previous_action']['actions'][0]['entity_id'] == 'light.kitchen'
    assert new.goal == 'Turn on the kitchen light'
    assert not new.pending and not new.undo and not new.authorised and not new.active
    assert not old.last_action and not old.goal
    assert sessions.finish(new, response()).continue_conversation is False


@pytest.mark.parametrize('block', ['off', 'guest', 'other_owner', 'expired', 'pending', 'running', 'ended', 'hidden', 'yes', 'new_command'])
async def test_context_boundaries(hass, freezer, block):
    sessions, old, new = setup()
    text = 'Actually, the hallway'
    if block == 'off': sessions.options['recent_followup_context'] = False
    if block == 'other_owner': new.owner = ('bedroom', None, None)
    if block == 'expired': freezer.tick(timedelta(seconds=61))
    if block == 'pending': old.pending = {'actions': []}
    if block == 'running': old.running = True
    if block == 'ended': sessions.close(new)
    if block == 'yes': text = 'yes'
    if block == 'new_command': text = 'Turn on the bedroom light'
    with patch('custom_components.sentient.followups.async_should_expose', return_value=block != 'hidden'):
        await restore_recent(hass, sessions, new, text, request('').context, lambda: block != 'guest')
    assert not new.goal and not new.last_action and not new.entities


async def test_other_one_asks_instead_of_guessing(hass):
    assert classify('The other one.') == 'correction'
    sessions, old, new = setup()
    with patch('custom_components.sentient.followups.async_should_expose', return_value=True):
        await restore_recent(hass, sessions, new, 'The other one.', request('').context, lambda: True)
        sessions.begin(new, 'The other one.')
        assert await refresh(hass, new, request('').context) == 'Which device or room do you mean?'
    assert not new.entities


async def test_restored_correction_requires_new_confirmation(hass):
    from unittest.mock import AsyncMock, Mock
    from custom_components.sentient.control import Control
    sessions, old, new = setup()
    with patch('custom_components.sentient.followups.async_should_expose', return_value=True):
        await restore_recent(hass, sessions, new, 'Actually, the hallway', request('').context, lambda: True)
    sessions.begin(new, 'Actually, the hallway')
    new.running = new.authorised = True
    hass.states.async_set('light.hallway', 'off')
    control = Control(hass, {}, Mock())
    with patch.object(control, 'validate', new=AsyncMock()), patch.object(control, 'execute', new=AsyncMock()) as execute:
        result = await control.propose(new, [{'entity_id': 'light.hallway', 'action': 'turn_on'}], request('').context)
    assert result['status'] == 'awaiting_confirmation'
    assert new.pending['turn'] == new.turn
    execute.assert_not_called()
