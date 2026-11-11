"""Pure voice listening decision, independent of the language model.

This does not infer consent, launch an Assist pipeline, or extend a microphone
timeout. It makes the existing question-based behaviour explicit and testable.
"""
from __future__ import annotations

from dataclasses import dataclass
from typing import Literal

Reason = Literal["error_or_empty_reply", "discussion_follow_up",
                 "reply_ends_in_question", "completed_single_request"]


@dataclass(frozen=True)
class ListeningDecision:
    continue_listening: bool
    reason: Reason


def decide_listening(*, reply_is_question: bool, discussion_active: bool,
                     error: bool = False) -> ListeningDecision:
    """An answer alone must never re-open the mic.

    An information discussion can remain active as a *topic* while the speaker
    is not listening. Only a genuine spoken question keeps it open. Calls
    containing one-shot commands also follow this rule: a non-question closes.
    """
    if error:
        return ListeningDecision(False, "error_or_empty_reply")
    if reply_is_question:
        return ListeningDecision(True, "discussion_follow_up" if discussion_active
                                 else "reply_ends_in_question")
    return ListeningDecision(False, "completed_single_request")
