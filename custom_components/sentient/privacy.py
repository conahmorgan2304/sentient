"""Forget the last few hours: everything Sentient itself kept about them, gone in one go.

Clears, for the chosen window: the timeline, past conversations, visitors at the door, clip descriptions,
names seen by face recognition, memories made in that time (pinned ones stay — they were chosen to be
kept), what it said lately and the record of what left the house. It can't recall anything already sent
to an AI service or what Home Assistant's own history and logbook recorded.
"""

from __future__ import annotations

from collections import deque
from datetime import timedelta
from typing import Any

from homeassistant.util import dt as dt_util

MAX_HOURS = 24 * 7


def _after(stamp: Any, since) -> bool:
    when = dt_util.parse_datetime(str(stamp or ""))
    return when is not None and dt_util.as_utc(when) >= since


def forget_recent(rt: Any, hours: float = 48) -> dict[str, int]:
    hours = max(1.0, min(float(hours), MAX_HOURS))
    since = dt_util.utcnow() - timedelta(hours=hours)
    gone: dict[str, int] = {}

    tl = rt.timeline
    keep = [i for i in tl.items if not _after(i.get("time"), since)]
    gone["timeline"] = len(tl.items) - len(keep)
    tl.items = deque(keep, maxlen=tl.items.maxlen)
    tl._save()

    conv = rt.conversations
    keep_eps = [e for e in conv.episodes if not _after(e.get("start"), since)]
    gone["conversations"] = len(conv.episodes) - len(keep_eps) + len(conv._open)
    conv.episodes = keep_eps
    conv._open.clear()
    conv._save()

    vis = rt.visitors
    keep_v = [v for v in vis.log if not _after(v.get("time"), since)]
    gone["visitors"] = len(vis.log) - len(keep_v)
    vis.log = deque(keep_v, maxlen=vis.log.maxlen)
    vis._save()

    if (cw := getattr(rt, "clipwatch", None)) is not None:
        keep_c = [i for i in cw.items if not _after(i.get("time"), since)]
        gone["clip_descriptions"] = len(cw.items) - len(keep_c)
        cw.items = keep_c
        cw._save()
    if (clips := getattr(rt, "clips", None)) is not None and _after((clips.last or {}).get("time"), since):
        clips.last = {}

    if (faces := getattr(rt, "faces", None)) is not None:
        names = [n for n, s in faces.seen.items() if _after(s.get("time"), since)]
        for name in names:
            faces.seen.pop(name, None)
        gone["faces_seen"] = len(names)
        if names:
            faces._store.async_delay_save(lambda: {"seen": faces.seen}, 1)

    mem = rt.memory
    recent = [m for m in mem.items if _after(m.created, since) and not m.pinned]
    for m in recent:
        mem.forget(m.id)
    gone["memories"] = len(recent)

    said = rt.speech._said
    keep_s = [(t, text) for t, text in said if t < since]
    said.clear()
    said.extend(keep_s)

    if (ai := getattr(rt, "ai", None)) is not None:
        gone["sent_records"] = ai.forget_sent(since.isoformat())
    return gone
