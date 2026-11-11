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
