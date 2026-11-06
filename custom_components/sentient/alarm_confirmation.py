"""Server-owned, single-use alarm approvals; model arguments never grant permission."""
from __future__ import annotations

from copy import deepcopy
from datetime import timedelta

from homeassistant.util import dt as dt_util

from .outside import _turn
from .sessions import YES, normalise


def approved(rt, context, schedule, confirmed):
    turn = _turn()
    if turn is None:
        return False  # integrations without a chat log cannot verify a subsequent user reply
    cid, number, text = turn
    key = (cid, context.device_id, getattr(context.context, "user_id", None))
    now = dt_util.utcnow()
    pending = rt.alarm_confirmations
    for old, item in list(pending.items()):
        if item["until"] <= now:
            pending.pop(old, None)
    previous = pending.get(key)
    if confirmed:
        # Keep the original request on a same-turn model retry, but never execute it.
        if previous and number == previous["turn"]:
            return False
        previous = pending.pop(key, None)
        return bool(previous and number == previous["turn"] + 1
                    and YES.fullmatch(normalise(text)) and previous["schedule"] == schedule)
    pending[key] = {"schedule": deepcopy(schedule), "turn": number, "until": now + timedelta(minutes=2)}
    while len(pending) > 100:
        pending.pop(next(iter(pending)))
    return False
