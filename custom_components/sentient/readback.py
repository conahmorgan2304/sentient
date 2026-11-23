"""Read back, then wait for a yes on a later turn.

Something that will switch devices later or in several steps (a watch that acts, a plan) is first read back in
plain words. It's only carried out when the voice calls again with exactly the same details *and* the person has
spoken since — the yes has to come from them, not from the model deciding it heard one. Without voice sessions
(no session to count turns in) the second call is accepted as before, the same as rules.

Nothing here is stored: a read-back not answered within a few minutes, or in a different conversation, is gone.
"""
from __future__ import annotations

from datetime import timedelta
import json
from typing import Any

from homeassistant.util import dt as dt_util

WAIT = timedelta(minutes=5)


def _signature(details: dict[str, Any]) -> str:
    return json.dumps(details, sort_keys=True, default=str)


class Readbacks:
    def __init__(self) -> None:
        self._pending: dict[tuple[str, str], dict[str, Any]] = {}

    def propose(self, kind: str, details: dict[str, Any], session: Any) -> None:
        if session is None:
            return
        self._tidy()
        self._pending[(kind, session.id)] = {"sig": _signature(details), "turn": session.turn,
                                             "until": dt_util.utcnow() + WAIT}

    def confirm(self, kind: str, details: dict[str, Any], session: Any) -> str | None:
        """None when it may go ahead; otherwise why not, for the voice."""
        if session is None:
            return None
        self._tidy()
        pending = self._pending.get((kind, session.id))
        if pending is None:
            return "Read it back first (call again with confirmed false), then wait for them to say yes."
        if pending["sig"] != _signature(details):
            return "The details changed since it was read back: read the new version back and ask again."
        if session.turn <= pending["turn"]:
            return "Wait for them to answer the question you read back. Only their yes on the next turn confirms it."
        self._pending.pop((kind, session.id), None)
        return None

    def _tidy(self) -> None:
        now = dt_util.utcnow()
        for key in [k for k, v in self._pending.items() if v["until"] < now]:
            self._pending.pop(key, None)


def readbacks(rt: Any) -> Readbacks:
    found = getattr(rt, "_readbacks", None)
    if found is None:
        found = Readbacks()
        setattr(rt, "_readbacks", found)
    return found


def current() -> Any:
    from .sessions import current_session

    return current_session.get()
