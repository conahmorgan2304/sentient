"""Why the house said or asked something, kept with the timeline item so 'why did you say that?' has an answer.

A caller that knows the reason wraps its speech in ``because("…")``; the speech router picks it up from
this context variable when it fires its spoken / held events. Anything said without one still gets a
plain reason from its source (an alarm, an appliance finishing, someone arriving home…).
"""

from __future__ import annotations

from collections.abc import Iterator
from contextlib import contextmanager
from contextvars import ContextVar

_WHY: ContextVar[str | None] = ContextVar("sentient_speaking_because", default=None)

MAX_LENGTH = 300

# source prefix → plain reason; the part after ':' is the name it was about
_BY_SOURCE: dict[str, str] = {
    "alarm": "the '{name}' alarm was due",
    "announce": "the '{name}' announcement was due",
    "appliance": "the {name} looked to have finished",
    "autooff": "{name} had been left on with nobody about",
    "rule": "the '{name}' rule matched",
    "watch": "you asked me to keep an eye on {name}",
    "reminder": "you asked to be reminded: {name}",
    "offer": "the '{name}' offer's conditions were met",
    "bins": "the bins go out soon",
    "rain": "rain was on the way",
    "left_open": "something had been left open",
    "welcome": "someone had just come home",
    "visitors": "someone was at the door",
    "parcel": "a parcel tracker said so",
    "maintenance": "{name} was due by how much it's been used",
    "animals": "the animal cameras spotted something",
    "night_watch": "the night watch saw something",
    "critical": "a safety sensor went off",
    "followthrough": "a device didn't do what was asked",
    "followyou": "it was following someone from room to room",
    "followup": "a follow-up you'd set was due",
    "thought": "a look at tomorrow",
    "curious": "I was curious and it seemed a good moment to ask",
    "learning": "I'd spotted a pattern",
    "notice": "something changed that seemed worth mentioning",
    "briefing": "the briefing was due",
    "digest": "the digest was due",
}


def current() -> str | None:
    return _WHY.get()


@contextmanager
def because(why: str | None) -> Iterator[None]:
    """Speech inside this block carries *why* (one short plain sentence, no trailing full stop)."""
    token = _WHY.set(str(why)[:MAX_LENGTH] if why else None)
    try:
        yield
    finally:
        _WHY.reset(token)


def from_source(source: str | None) -> str | None:
    if not source:
        return None
    kind, _, name = str(source).partition(":")
    template = _BY_SOURCE.get(kind)
    if template is None:
        return None
    if "{name}" in template and not name:
        return None
    return template.format(name=name)


def reason_for(source: str | None) -> str | None:
    """The reason in scope, or one from the source."""
    return current() or from_source(source)
