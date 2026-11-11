"""The listening policy must not turn one-shot commands into open microphones."""
from custom_components.sentient.conversation_policy import decide_listening


def test_one_shot_command_closes():
    decision = decide_listening(reply_is_question=False, discussion_active=False)
    assert not decision.continue_listening
    assert decision.reason == "completed_single_request"


def test_question_reply_opens_a_single_followup():
    decision = decide_listening(reply_is_question=True, discussion_active=False)
    assert decision.continue_listening
    assert decision.reason == "reply_ends_in_question"


def test_discussion_does_not_force_listening_after_statement():
    assert not decide_listening(reply_is_question=False, discussion_active=True).continue_listening
    assert decide_listening(reply_is_question=True, discussion_active=True).reason == "discussion_follow_up"


def test_error_never_keeps_microphone_open():
    assert not decide_listening(reply_is_question=True, discussion_active=True, error=True).continue_listening


async def test_active_discussion_followed_by_toggle_does_not_reopen_mic(hass):
    """A one-shot action within a discussion must not inherit the stock follow-up."""
    from types import SimpleNamespace
    from homeassistant.util import dt as dt_util
    from custom_components.sentient.sessions import Sessions, VoiceSession

    class Reply:
        response_type = None

        def __init__(self, text):
            self.speech = {"plain": {"speech": text}}

        def async_set_speech(self, text):
            self.speech["plain"]["speech"] = text

    sessions = Sessions({"conversation_style": "brief", "conversation_sessions": True})
    session = VoiceSession("example", ("device", "satellite", None), dt_util.utcnow())
    session.active = True
    session.command_turn = True
    result = SimpleNamespace(response=Reply("Kitchen light turned on."), continue_conversation=True)
    sessions.finish(session, result)
    assert result.continue_conversation is False
    assert session.listen_reason == "completed_single_request"
    assert result.response.speech["plain"]["speech"] == "Kitchen light turned on."


async def test_information_discussion_can_still_offer_followup(hass):
    from types import SimpleNamespace
    from homeassistant.util import dt as dt_util
    from custom_components.sentient.sessions import Sessions, VoiceSession

    class Reply:
        response_type = None

        def __init__(self):
            self.speech = {"plain": {"speech": "The gate reports closed."}}

        def async_set_speech(self, text):
            self.speech["plain"]["speech"] = text

    sessions = Sessions({"conversation_style": "brief", "conversation_sessions": True})
    session = VoiceSession("example", ("device", "satellite", None), dt_util.utcnow())
    session.active = True
    session.command_turn = False
    result = SimpleNamespace(response=Reply(), continue_conversation=False)
    sessions.finish(session, result)
    assert result.continue_conversation is True
    assert session.listen_reason == "discussion_follow_up"
