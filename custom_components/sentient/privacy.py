"""Forget recent conversation, observation and speech records across Sentient stores.

Clears, for the chosen window: the timeline, past conversations, visitors at the door, clip descriptions,
names seen by face recognition, memories made in that time (pinned ones stay — they were chosen to be
kept), speech delivery history, session traces/topics, live voice checks, security episodes, follow-up
questions, what it said lately and the record of what left the house. It can't recall anything already sent
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


async def async_forget_recent(rt: Any, hours: float = 48) -> dict[str, Any]:
    """Persist the privacy request first, then forget all stores and await SQLite."""
    brain = getattr(rt, "brain", None)
    if brain is None or not brain.started:
        return forget_recent(rt, hours)
    # A single lock covers both saving the intent and clearing its completion
    # record. Concurrent forget commands must not overwrite one another.
    async with brain._forget_lock:
        hours = max(1.0, min(float(hours), MAX_HOURS))
        since = dt_util.utcnow() - timedelta(hours=hours)
        try:
            start, end = await brain.async_prepare_forget(since)
        except Exception as err:  # noqa: BLE001 - do not clear other stores without a recovery record
            return {"database_error": f"Could not safely start forgetting: {err}. Nothing was cleared."}
        gone: dict[str, Any] = forget_recent(rt, hours, _registered=True, _since=since, _until=end)
        try:
            for key, n in (await brain.async_finish_forget()).items():
                gone[f"database_{key}"] = n
        except Exception as err:  # noqa: BLE001 - pending record survives until a successful restart replay
            gone["database_error"] = ("The memory database couldn't be cleared yet; it will finish when Sentient next "
                                      f"starts, and nothing from that period can be saved meanwhile ({err}).")
        return gone


def forget_recent(rt: Any, hours: float = 48, *, _registered: bool = False,
                  _since: Any = None, _until: float | None = None) -> dict[str, int]:
    from .language import clear_translations

    clear_translations(rt.ai)
    if house_context := getattr(rt, "house_context", None):
        house_context.clear()
    if control := getattr(rt, "control", None):
        control.clear_recovery()
    if followthrough := getattr(rt, 'followthrough', None):
        gone_checks = len(followthrough._pending)
        followthrough.clear()
    else:
        gone_checks = 0
    if timing := getattr(rt.ai, "voice_timings", None):
        timing.clear()
    if (brain := getattr(rt, "brain", None)) is not None:
        brain.case_clear()  # saved AI answers from case memory go too
    if isinstance(getattr(rt, "automation_pending", None), dict):
        rt.automation_pending.clear()  # automations read back and waiting for a yes
    hours = max(1.0, min(float(hours), MAX_HOURS))
    since = _since if _since is not None else dt_util.utcnow() - timedelta(hours=hours)
    gone: dict[str, int] = {'action_checks': gone_checks}
    if (brain := getattr(rt, "brain", None)) is not None:
        # the database's copies (house ledger, conversations, timeline) for the same period, and anything still queued
        gone['ledger_waiting'] = brain.forget_since(since, registered=_registered, until=_until)
    if cache := getattr(rt, "clip_analysis", None):
        cache.clear()
    if events := getattr(rt, "security_events", None):
        gone["security_events"] = events.forget(since)

    noticer = getattr(rt, 'noticer', None)
    if noticer is not None:
        decisions = noticer.decisions
        retained = [row for row in decisions if not _after(row.get('time'), since)]
        gone['noticing_decisions'] = len(decisions) - len(retained)
        noticer.decisions = deque(retained, maxlen=decisions.maxlen)
        noticer._pending = [change for change in noticer._pending if change.when < since]
        noticer._last_seen = {eid: stamp for eid, stamp in noticer._last_seen.items() if stamp < since}
        if not noticer._pending and noticer._flush_unsub:
            noticer._flush_unsub()
            noticer._flush_unsub = None

    watches = rt.watches
    gone['cached_camera_answers'] = len(getattr(watches, '_looks', {}))
    watches.clear_camera_cache()
    cleared = 0
    for watch in watches.watches:
        if _after(watch.last_checked, since):
            watch.last_result = watch.last_checked = None
            cleared += 1
    if cleared:
        watches._save()
    gone['camera_watch_results'] = cleared
    checks = getattr(rt, 'lists', {}).get('checks')
    if checks is not None:
        cleared = 0
        for row in list(checks.items.values()):
            if _after(row.get('last'), since):
                checks.update(row['id'], last=None, last_answer=None)
                cleared += 1
        gone['standing_camera_results'] = cleared

    tl = rt.timeline
    keep = [i for i in tl.items if not _after(i.get("time"), since)]
    gone["timeline"] = len(tl.items) - len(keep)
    tl.items = deque(keep, maxlen=tl.items.maxlen)
    tl._save()

    conv = rt.conversations
    keep_eps = [e for e in conv.episodes if not _after(e.get("last") or e.get("start"), since)]
    gone["conversations"] = len(conv.episodes) - len(keep_eps) + len(conv._open)
    conv.episodes = keep_eps
    conv.skip.update(conv._open)  # late replies from forgotten conversations must not recreate them
    conv._open.clear()
    conv._save()

    vis = rt.visitors
    keep_v = [v for v in vis.log if not _after(v.get("time"), since)]
    gone["visitors"] = len(vis.log) - len(keep_v)
    vis.log = deque(keep_v, maxlen=vis.log.maxlen)
    vis._save()

    if (cw := getattr(rt, "clipwatch", None)) is not None:
        cw.invalidate()
        keep_c = [i for i in cw.items if not _after(i.get("time"), since)]
        gone["clip_descriptions"] = len(cw.items) - len(keep_c)
        cw.items = keep_c
        cw._save()
    if (camsec := getattr(rt, "camsec", None)) is not None:
        gone["camera_journeys"] = len(camsec.journeys)
        camsec.clear()
    if (clips := getattr(rt, "clips", None)) is not None:
        clips.invalidate()
        if _after((clips.last or {}).get("time"), since):
            clips.last = {}

    if (faces := getattr(rt, "faces", None)) is not None:
        names = [n for n, s in faces.seen.items() if _after(s.get("time"), since)]
        for name in names:
            faces.seen.pop(name, None)
        gone["faces_seen"] = len(names)
        if names:
            faces._store.async_delay_save(lambda: {"seen": faces.seen}, 1)

    reflection = getattr(rt, 'reflection', None)
    if reflection is not None and reflection.last and _after(reflection.last.get('time'), since):
        reflection.last = None
        reflection._store.async_delay_save(lambda: {"last": reflection.last}, 2)
        gone['reflection'] = 1
    curiosity = getattr(rt, 'curiosity', None)
    if curiosity is not None:
        queued = [q for q in curiosity.queue if not _after(q.get('added'), since)]
        asked = {key: row for key, row in curiosity.asked.items() if not _after(row.get('time'), since)}
        gone['curiosity_records'] = len(curiosity.queue) - len(queued) + len(curiosity.asked) - len(asked)
        curiosity.queue, curiosity.asked = queued, asked
        if gone['curiosity_records']:
            curiosity._save()
    followyou = getattr(rt, 'followyou', None)
    if followyou is not None:
        queued = [q for q in followyou.queue if not _after(q.get('kept'), since)]
        gone['queued_messages'] = len(followyou.queue) - len(queued)
        followyou.queue = queued
        if gone['queued_messages']:
            followyou._save()

    mem = rt.memory
    recent = [m for m in mem.items if _after(m.created, since) and not m.pinned]
    gone['memory_changes'] = mem.forget_changes_since(since)
    for m in recent:
        mem.forget(m.id)
    gone["memories"] = len(recent)

    rt.speech._privacy_generation += 1
    if (tasks := getattr(rt, "household_tasks", None)) is not None:
        tasks.clear()
    for attr in ("_recent", "_remarks", "_delivered_recent"):
        cache = getattr(rt.speech, attr, {})
        setattr(rt.speech, attr, {k: stamp for k, stamp in cache.items() if stamp < since})
    rt.speech.save_remarks()
    said = rt.speech._said
    keep_s = [(t, text) for t, text in said if t < since]
    said.clear()
    said.extend(keep_s)

    # These short-lived stores also contain verbatim requests and replies.
    stores = [(rt.speech, "deliveries", "time", "speech_deliveries"),
              (rt.sessions.memory, "traces", "time", "session_traces"),
              (rt.sessions.memory, "topics", "saved_at", "conversation_topics")]
    for owner, attr, stamp, label in stores:
        rows = getattr(owner, attr)
        keep = [row for row in rows if not _after(row.get(stamp), since)]
        gone[label] = len(rows) - len(keep)
        setattr(owner, attr, deque(keep, maxlen=rows.maxlen) if isinstance(rows, deque) else keep)
    for key, session in list(rt.sessions.items.items()):
        if session.touched >= since:
            rt.sessions.close(session)
            session.forgotten = True
            session.topic = session.goal = session.missing_detail = ""
            session.entities.clear()
            session.last_action = session.resume_context = None
            rt.sessions.items.pop(key, None)
    checks = rt.voice_checks
    if checks.run and (_after(checks.run.get("started"), since)
                       or any(_after(s.get("time"), since) for s in checks.run.get("steps", []))):
        checks.stop()
        gone["voice_checks"] = 1
    if (incidents := getattr(rt, "incidents", None)) is not None:
        keep = [row for row in incidents.items if not _after(row.get("end"), since)]
        gone["incidents"] = len(incidents.items) - len(keep)
        incidents.items = keep
        incidents._save()
    if (threads := getattr(rt, "threads", None)) is not None:
        # Older entries have no creation timestamp: clear them conservatively.
        keep = [row for row in threads.items if row.get("created") and not _after(row["created"], since)]
        gone["follow_up_questions"] = len(threads.items) - len(keep)
        threads.items = keep
        threads._save()
    rt.alarm_confirmations.clear()
    rt.notifier.invalidate_questions()
    rt.speech._questions.clear()
    rt.speech.hass.bus.async_fire("sentient_speech_delivery")
    rt.speech.hass.bus.async_fire("sentient_voice_session")

    if (ai := getattr(rt, "ai", None)) is not None:
        gone["sent_records"] = ai.forget_sent(since.isoformat())
    return gone
