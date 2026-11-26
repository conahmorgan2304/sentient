"""Short-term memory per room: what was said, asked and done in this room in the last hour. No AI.

A person in a room remembers what just went on in it. The voice saw the conversation it was in and a few recent
conversations from anywhere, but not "in here, the last hour": earlier exchanges in this room, the lights it
switched here, what it announced here. And when someone walks from the kitchen to the living room mid-thought,
the living room didn't know what they'd just been talking about.

Each voice turn now gets, for the room it's in:

* **here, in the last hour** — earlier conversations in this room (newest first, both sides), what was switched
  here by voice, and what was announced here;
* **carried over** — when the newest conversation anywhere was in another room a few minutes ago and nothing has
  been said here since, that conversation, marked as probably the same person having moved.

All of it comes from records Sentient already keeps (conversations, the house ledger, the timeline); nothing new is
stored. In a private room or guest mode only this room's own record is given, never another room's. Off: none of it.
"""

from __future__ import annotations

from datetime import timedelta
from typing import Any

from homeassistant.util import dt as dt_util

CONF_ROOM_MEMORY = "room_memory"
WINDOW = timedelta(hours=1)
CARRY = timedelta(minutes=10)
MAX_CONVERSATIONS = 3
MAX_DONE = 6
MAX_SAID = 3


def _short(text: str, limit: int = 140) -> str:
    text = " ".join((text or "").split())
    return text if len(text) <= limit else text[: limit - 1].rstrip() + "…"


def _hm(iso: str) -> str:
    when = dt_util.parse_datetime(iso or "")
    return f"{dt_util.as_local(when):%H:%M}" if when else ""


def _exchange(ep: dict[str, Any]) -> str:
    turns = [t for t in ep.get("turns", []) if t.get("text")][-4:]
    return " / ".join(f"{'they' if t['who'] == 'them' else 'you'}: \"{_short(t['text'], 110)}\"" for t in turns)


def _same(room: str | None, other: str | None) -> bool:
    return bool(room and other and room.casefold() == other.casefold())


def conversations_here(rt: Any, room: str, exclude: str | None) -> list[dict[str, Any]]:
    convs = getattr(rt, "conversations", None)
    if convs is None:
        return []
    mine = (exclude or "")[-10:]
    return [ep for ep in convs.recent(hours=WINDOW.total_seconds() / 3600, limit=30)
            if _same(ep.get("room"), room) and ep.get("id") != mine][:MAX_CONVERSATIONS]


def done_here(rt: Any, room: str) -> list[str]:
    ledger = getattr(getattr(rt, "brain", None), "ledger", None)
    if ledger is None:
        return []
    since = (dt_util.utcnow() - WINDOW).timestamp()
    out = []
    for row in reversed(ledger.recent_rows(since)):
        if row.get("cause") != "voice" or not _same(row.get("area"), room):
            continue
        when = dt_util.as_local(dt_util.utc_from_timestamp(row["ts"]))
        out.append(f"{when:%H:%M} {row.get('name') or row['entity']} {row.get('old')} → {row.get('new')}")
        if len(out) >= MAX_DONE:
            break
    return out


def said_here(rt: Any, room: str) -> list[str]:
    timeline = getattr(rt, "timeline", None)
    if timeline is None:
        return []
    out = []
    for item in timeline.since(WINDOW.total_seconds() / 3600, ["said"], 40):
        rooms = [r.strip().casefold() for r in str(item.get("detail") or "").split(",")]
        if item.get("source") != "conversation" and room.casefold() in rooms:
            out.append(f"{_hm(item['time'])} \"{_short(str(item.get('text') or ''), 160)}\"")
            if len(out) >= MAX_SAID:
                break
    return out


def carried(rt: Any, room: str, exclude: str | None) -> dict[str, Any] | None:
    """The newest conversation anywhere, if it was in another room in the last few minutes and none since here."""
    convs = getattr(rt, "conversations", None)
    if convs is None:
        return None
    mine = (exclude or "")[-10:]
    for ep in convs.recent(hours=CARRY.total_seconds() / 3600, limit=10):
        if ep.get("id") == mine:
            continue
        if _same(ep.get("room"), room) or not ep.get("room"):
            return None  # something's been said here since: nothing to carry
        last = ep.get("last") or ep.get("start")
        when = dt_util.parse_datetime(last or "")
        if when is None or dt_util.utcnow() - when > CARRY:
            return None
        return ep
    return None


def lines(rt: Any, options: dict[str, Any], room: str | None, conversation_id: str | None, *,
          private: bool, guest: bool, query: str = "") -> list[str]:
    if not options.get(CONF_ROOM_MEMORY) or not room:
        return []
    if private or guest:
        return []
    out: list[str] = []
    # Automatic context for an actual request should contain no past speech from
    # other conversation sessions or rooms. History tools remain explicit.
    here = [] if query else conversations_here(rt, room, conversation_id)
    if here:
        out.append(f"Earlier in the {room} (last hour, newest first) — carry on from these naturally; \"it\" or "
                   "\"that\" may mean something from them:")
        out.extend(f"- {_hm(ep['start'])}: {_exchange(ep)}" for ep in here)
    if done := done_here(rt, room):
        out.append(f"Switched in the {room} by voice in the last hour: " + "; ".join(done) + ".")
    if not query and (said := said_here(rt, room)):
        out.append(f"Announced in the {room} in the last hour: " + "; ".join(said) + ".")
    if not query and (ep := carried(rt, room, conversation_id)):
        out.append(f"Probably the same person, who was just talking to you in the {ep['room']} at "
                   f"{_hm(ep.get('last') or ep['start'])} — if what they say now follows on, carry on from it: "
                   + _exchange(ep))
    return out
