"""Never offer to arm, disarm or unlock: a sentence that does is taken out of the voice's reply.

15 Nov: asked "is the alarm on?", the voice said "The alarm is currently disarmed. Shall I arm it in away mode?" —
though the house rules say never to offer. A rule in the prompt can be missed, so with this setting on, a sentence
offering to arm, disarm or unlock is removed from the reply before it's spoken: from the finished reply, and from a
reply spoken as it's written (each sentence is held until it's complete, then passed on or dropped).

It never touches a reply to someone who asked to arm, disarm, lock, unlock or open — "arm the alarm" still gets its
confirmation question — and a reply that is nothing but the offer is left as it is rather than leaving silence.
Off by default.
"""

from __future__ import annotations

import re

CONF_NO_SECURITY_OFFERS = "no_security_offers"

# an offer ("shall I…", "would you like me to…", "I can…") to arm, disarm, unlock or set the alarm
SECURITY_OFFER = re.compile(
    r"\b(?:shall i|should i|do you want me to|would you like me to|want me to|i can\b(?!['’]t)|i could\b(?!['’]?n)|let me|i'?ll|i will|"
    r"happy to|you (?:can|could) (?:ask|tell) me to)\b[^.?!]{0,40}?"
    r"\b(?:arm|disarm|unlock|unset|set (?:the|your) (?:house )?alarm|switch (?:the|your) (?:house )?alarm (?:on|off)|"
    r"turn (?:the|your) (?:house )?alarm (?:on|off)|activate (?:the|your) (?:house )?alarm|"
    r"deactivate (?:the|your) (?:house )?alarm|put (?:the|your) (?:house )?alarm on)\b",
    re.I)
# the person asked for a change themselves: their confirmation question must still be asked
ASKED_TO_CHANGE = re.compile(
    r"\b(?:arm|disarm|unlock|lock|unset|activate|deactivate|open|let \w+ in)\b|\b(?:set|switch|turn|put)\b[^.?!]{0,20}\balarm\b",
    re.I)
# where a sentence ends: . ! ? (or a line break) followed by space, or the end of the text
_END = re.compile(r"(?<=[.!?])\s+|\n+")


def applies(enabled: bool, asked: str | None) -> bool:
    """Whether to take offers out of this reply: switched on, and the person didn't ask for a change."""
    return bool(enabled) and not ASKED_TO_CHANGE.search(_question_only(asked or ""))


def _question_only(asked: str) -> str:
    """'Is the alarm on?' asks about the alarm; it doesn't ask to change it."""
    asked = " ".join(asked.split())
    if re.match(r"^(?:is|are|was|has|have|did|does|do|what|when|who|how)\b", asked, re.I):
        return ""
    return asked


def sentences(text: str) -> list[str]:
    return [s for s in (p.strip() for p in _END.split(text or "")) if s]


def _offer(sentence: str, security: bool, extra: re.Pattern[str] | None) -> bool:
    return bool((security and SECURITY_OFFER.search(sentence)) or (extra is not None and extra.search(sentence)))


def remove(text: str, *, security: bool = True, extra: re.Pattern[str] | None = None) -> tuple[str, list[str]]:
    """The reply without any sentence offering to arm, disarm or unlock (and, with ``extra``, to do something no
    device here can, such as closing a window), and the sentences taken out.

    A reply that is only the offer is left alone (silence would be worse) — that's returned with nothing removed."""
    parts = sentences(text)
    kept = [s for s in parts if not _offer(s, security, extra)]
    if not kept or len(kept) == len(parts):
        return text, []
    return " ".join(kept), [s for s in parts if _offer(s, security, extra)]


class StreamFilter:
    """For a reply spoken as it's written: each sentence is held until it's complete, then passed on — unless it
    offers to arm, disarm or unlock, when it's dropped. ``flush`` decides on the last, unfinished sentence; if every
    sentence was an offer, it gives them back so the reply isn't silent (as ``remove`` does)."""

    def __init__(self, *, security: bool = True, extra: re.Pattern[str] | None = None, blocked=None, defer=None) -> None:
        self.security, self.extra = security, extra
        self.blocked = blocked
        self.defer = defer
        self._deferred = False
        self._held = ""
        self.passed: list[str] = []
        self.dropped: list[str] = []

    def feed(self, content: str) -> str:
        text = self._held + content
        out = []
        start = 0
        for m in _END.finditer(text):
            out.append(self._decide(text[start:m.start()], m.group(0)))
            start = m.end()
        self._held = text[start:]
        return "".join(out)

    def flush(self) -> str:
        text, self._held = self._held, ""
        out = self._decide(text, "") if text.strip() else text
        if self.blocked is None and self.defer is None and not self.passed and self.dropped:  # the reply was only the offer: say it rather than nothing
            out, self.dropped = " ".join(self.dropped), []
        return out

    def _decide(self, sentence: str, gap: str) -> str:
        if self._deferred or (self.defer and self.defer(sentence)):
            self._deferred = True
            return ""
        if not sentence.strip():
            return sentence + gap
        if _offer(sentence, self.security, self.extra) or (self.blocked and self.blocked(sentence)):
            self.dropped.append(sentence.strip())
            return ""
        self.passed.append(sentence.strip())
        return sentence + gap
