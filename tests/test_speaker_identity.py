"""Local speaker answers use the exact voice turn and bypass the AI agent."""

from types import SimpleNamespace
from unittest.mock import AsyncMock, Mock, patch

import pytest
from homeassistant.helpers import entity_registry as er

from custom_components.sentient.const import DEFAULT_BACKUP_AGENT
from custom_components.sentient.speaker_identity import SPEAKER_FOR_TURN, VOICE_RECOGNITION_DOMAIN
from tests.test_voice_sessions_control import agent, request, response


@pytest.mark.parametrize(
    ("phrase", "speaker_id", "name", "guest", "expected"),
    [
        ("Who’s speaking?", "profile-1", "Alex", False, "Your voice matches Alex's profile."),
        ("Can you tell me who you think is speaking to you right now?", None, None, False,
         "I couldn't match your voice to a profile this time."),
        ("Who is speaking?", "profile-1", "Alex", True, "Voice identification is unavailable in guest mode."),
    ],
)
async def test_local_identity_answer(hass, phrase, speaker_id, name, guest, expected):
    obj, runtime = agent(hass, {"conversation_sessions": True})
    runtime.speech = SimpleNamespace(guest_mode=guest)
    turn = request(phrase)
    lookup = Mock(return_value=SimpleNamespace(speaker_id=speaker_id, display_name=name))
    hass.data[SPEAKER_FOR_TURN] = lookup

    with patch.object(obj, "_process_turn", new_callable=AsyncMock) as process:
        result = await obj.async_process(turn)

    lookup.assert_called_once_with(turn)
    process.assert_not_awaited()
    assert result.response.speech["plain"]["speech"] == expected
    assert result.conversation_id == turn.conversation_id
    assert not result.continue_conversation


async def test_unbound_and_unrelated_requests_keep_normal_routing(hass):
    obj, _ = agent(hass, {})
    with patch.object(obj, "_process_turn", AsyncMock(return_value=response())) as process:
        await obj.async_process(request("Who is speaking?"))
        lookup = Mock(return_value=None)
        hass.data[SPEAKER_FOR_TURN] = lookup
        await obj.async_process(request("Who is speaking?"))
        await obj.async_process(request("Who is speaking on the radio?"))

    lookup.assert_called_once()
    assert process.await_count == 3


def test_voice_adapter_is_not_a_downstream_ai_or_backup(hass):
    adapter = er.async_get(hass).async_get_or_create("conversation", VOICE_RECOGNITION_DOMAIN, "adapter")
    obj, _ = agent(hass, {"voice_agent": adapter.entity_id, "backup_agent": adapter.entity_id})
    hass.states.async_set(adapter.entity_id, "ready")

    assert obj.main_agent is None
    assert obj.backup_agent == DEFAULT_BACKUP_AGENT
