"""Everyday sign-offs, corrections and changes of subject, behind "conversation_cues" (on by default)."""
from unittest.mock import AsyncMock, patch

import pytest

from custom_components.sentient.sessions import Sessions, is_conversation_end
from tests.test_voice_sessions_control import agent, request, response


@pytest.mark.parametrize("text", ["cheers", "cheers mate", "perfect, thanks", "that's great thanks", "lovely", "ta",
                                  "that'll do", "I'm good thanks", "no that's fine", "sorted, cheers", "brilliant"])
def test_everyday_sign_offs_end_an_idle_conversation(text):
    assert is_conversation_end(text, "Anything else?", cues=True)
    assert is_conversation_end(text, "", cues=True)


@pytest.mark.parametrize("text", ["perfect", "great", "lovely thanks", "that's fine"])
def test_a_sign_off_never_answers_a_specific_question(text):
    # "perfect" after "Shall I turn the heating up?" must reach the confirmation path, not close the conversation
    assert not is_conversation_end(text, "Shall I turn the heating up to 21?", cues=True)


def test_switched_off_only_the_plain_endings_count():
    assert not is_conversation_end("cheers", "", cues=False)
    assert is_conversation_end("that's all", "", cues=False)


def _session(options=None):
    sessions = Sessions({"conversation_sessions": True, "contextual_followups": True, **(options or {})})
    session = sessions.get(request("start"))
    return sessions, session


def test_a_change_of_subject_drops_the_old_devices():
    sessions, session = _session()
    sessions.begin(session, "is the gate open?")
    session.entities = ["binary_sensor.gate"]
    session.last_action = {"summary": "turn off hall light"}
    sessions.begin(session, "on another note, what's the weather tomorrow?")
    assert session.entities == [] and session.correction is None and session.follow_up_context is None
    assert session.topic == "what's the weather tomorrow?" and session.listen_reason == "new_subject"


def test_new_correction_phrases_refine_the_task():
    sessions, session = _session()
    sessions.begin(session, "turn on the lamp")
    session.last_action = {"actions": [{"entity_id": "light.lamp", "status": "verified"}], "summary": "turn on Lamp"}
    for phrase in ("sorry, I meant the ceiling light", "wrong light", "not the lamp, the ceiling light"):
        sessions.begin(session, phrase)
        assert session.correction is not None, phrase
        assert session.follow_up_context["kind"] == "correction", phrase


def test_cues_off_leaves_these_phrases_alone():
    sessions, session = _session({"conversation_cues": False})
    sessions.begin(session, "is the gate open?")
    session.entities = ["binary_sensor.gate"]
    sessions.begin(session, "wrong light")
    assert session.correction is None
    sessions.begin(session, "on another note, what's the weather?")
    assert session.listen_reason != "new_subject"


async def test_cheers_closes_the_microphone_end_to_end(hass):
    obj, rt = agent(hass, {"conversation_sessions": True})
    with patch.object(obj, "_async_process_legacy", AsyncMock(side_effect=[response("The gate is closed.")])) as model:
        assert (await obj.async_process(request("Is the gate open?"))).continue_conversation
        result = await obj.async_process(request("Cheers"))
    assert not result.continue_conversation and model.await_count == 1
    assert rt.sessions.describe() == []
