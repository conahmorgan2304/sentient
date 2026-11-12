"""The brain: Sentient's long-term memory and learning, in one place.

It owns the database (``db.py``) and runs:

* the **house ledger** — every change and its cause (``ledger.py``);
* **expectations** — what's usual when, and how surprising each change is (``expectations.py``);
* **unexplained changes** — an important device changing with nothing in Home Assistant behind it, said once
  and linked when it keeps happening ("the third time this week, always around 11am");
* **reactions** — Sentient's own changes switched straight back count against it, remarks acted on count for it;
* **beliefs** — claims formed from the ledger and tested every night (``beliefs.py``);
* **case memory** — an AI job identical to a recent one reuses its answer instead of another call;
* **voice speed** — notices when a speaker takes far longer to hear you than you took to speak;
* **situations** — a few plain lines of what's going on now, for the voice;
* copies of facts, conversations and the timeline, kept a year and searchable.

Everything except case memory works without AI. Each part has its own switch (Settings → Memory & learning).
"""
from __future__ import annotations

from datetime import datetime, timedelta
import asyncio
import hashlib
import json
import logging
from typing import Any, Callable

from homeassistant.const import EVENT_STATE_CHANGED
from homeassistant.core import Event, HomeAssistant, callback
from homeassistant.helpers import entity_registry as er
from homeassistant.helpers.event import async_track_time_change, async_track_time_interval
from homeassistant.util import dt as dt_util

from .beliefs import CONF_BELIEFS, Beliefs, day_bounds, find_by_itself
from .db import LEDGER_DAYS, MEMORY_DAYS, Database
from .expectations import BURST_SECONDS, CONF_EXPECTATIONS, Expectations, async_warm_up, information
from .ledger import CONF_LEDGER, CAUSE_WORDS, Ledger, describe, resolve_entities

_LOGGER = logging.getLogger(__name__)

CONF_UNEXPLAINED = "unexplained_changes"
CONF_UNEXPLAINED_ENTITIES = "unexplained_entities"
CONF_SURPRISE_GATING = "surprise_gating"  # off / watch / on
CONF_REACTIONS = "learn_reactions"
CONF_CASES = "ai_case_memory"
CONF_VOICE_SPEED = "voice_speed_watchdog"
CONF_VOICE_RESTART = "voice_speed_restart"
CONF_SITUATIONS = "situations"
DEFAULTS = {CONF_LEDGER: True, CONF_EXPECTATIONS: True, CONF_UNEXPLAINED: True, CONF_SURPRISE_GATING: "watch",
            CONF_REACTIONS: True, CONF_CASES: True, CONF_VOICE_SPEED: True, CONF_VOICE_RESTART: False,
            CONF_SITUATIONS: True, CONF_BELIEFS: True}
# devices whose unexplained changes matter by default (when none are chosen in Settings)
# things a person switches: putting one straight back after Sentient changed it is a reaction
TOGGLES = ("light", "switch", "fan", "cover", "climate", "humidifier", "input_boolean", "valve", "lock", "water_heater")
# things that are controlled (not sensors): one changing with nothing in Home Assistant behind it is worth counting
CONTROLLED = TOGGLES + ("media_player", "vacuum", "alarm_control_panel", "siren", "lawn_mower")
IMPORTANT_DOMAINS = ("climate", "water_heater", "valve")  # locks, doors and gates are used by hand: chosen, not default
UNEXPLAINED_GAP = 1800  # one mention per device per half hour
REVERT_SECONDS = 60  # 'straight back': longer is more likely someone leaving the room
ACTED_SECONDS = 600
CASE_HOURS = 6
CASE_MAX = 200
# jobs whose answer only depends on what they're given: wording a remark, reading a manual or recipe, translating
CASE_TASKS = ("Sentient: worth saying?", "sentient manual", "sentient recipe", "Sentient: find ", "Sentient: speech language")
VOICE_GRACE = 20.0  # seconds of listening that are never called slow (people pause before speaking)


def _local(ts: float) -> datetime:
    return dt_util.as_local(dt_util.utc_from_timestamp(ts))


def _clock(ts: float) -> str:
    return _local(ts).strftime("%H:%M")


def _hour_words(hour: int) -> str:
    return f"{12 if hour % 12 == 0 else hour % 12}{'am' if hour < 12 else 'pm'}"


def ordinal(n: int) -> str:
    return {2: "second", 3: "third", 4: "fourth", 5: "fifth"}.get(n, f"{n}th")


class Brain:
    def __init__(self, hass: HomeAssistant, entry_id: str, options: dict[str, Any], rt_getter: Callable[[], Any],
                 db_path: str | None = None) -> None:
        self.hass = hass
        self.options = options
        self._rt = rt_getter
        self.db = Database(hass, entry_id, db_path)
        self.ledger: Ledger | None = None
        self.expect = Expectations()
        self.beliefs = Beliefs(self.db)
        self.outcomes: dict[str, list[float]] = {"good": [], "bad": []}  # timestamps
        self.cases: dict[str, dict[str, Any]] = {}
        self.counters: dict[str, dict[str, int]] = {}  # day -> {ai_saved, gated, local}
        self.voice: dict[str, dict[str, Any]] = {}  # satellite -> {listening_since, samples[], warned}
        self._said_unexplained: dict[str, float] = {}
        self._remarks: list[tuple[float, list[str]]] = []
        self._unsubs: list[Callable[[], None]] = []
        self.started = False
        self.warming = False
        self._warm_task: Any = None
        self._generation = 0
        from homeassistant.helpers.storage import Store

        # forgotten periods not yet deleted from the database: kept on disk until they are
        self._forget_store: Any = Store(hass, 1, f"sentient.{entry_id}.forget")
        self._forget_pending: list[tuple[float, float]] = []
        self._forget_lock = asyncio.Lock()  # serialise durable registration, deletion and completion

    # ----- settings ---------------------------------------------------------------------------
    def on(self, key: str) -> bool:
        value = self.options.get(key, DEFAULTS.get(key))
        return value not in (False, None, "off")

    @property
    def gating(self) -> str:
        return str(self.options.get(CONF_SURPRISE_GATING, DEFAULTS[CONF_SURPRISE_GATING]) or "off")

    # ----- start and stop ---------------------------------------------------------------------
    async def async_start(self) -> None:
        await self.db.async_open()
        # a forget that was interrupted (Home Assistant stopped part-way) is finished before anything else is written
        unfinished = ((await self._forget_store.async_load()) or {}).get("pending") or []
        for start, end in unfinished:
            self.db.forget_window(float(start), float(end))
            self._forget_pending.append((float(start), float(end)))
        if self._forget_pending:
            # A crash may occur immediately after the durable intent is saved,
            # before the ordinary conversation stores have been cleared.
            # Replay that original bounded interval as well as SQLite deletion.
            from .privacy import forget_recent

            rt = self._rt()
            try:
                if rt is not None:
                    for start, end in list(self._forget_pending):
                        forget_recent(rt, (end - start) / 3600, _registered=True,
                                      _since=dt_util.utc_from_timestamp(start), _until=end)
                await self.async_finish_forget()
            except Exception:  # noqa: BLE001 - never stop the brain: the period stays blocked and is retried next start
                _LOGGER.warning("Sentient couldn't finish forgetting an interrupted period yet; it will try again at the "
                                "next start, and nothing from that period can be saved meanwhile", exc_info=True)
                if rt is not None and getattr(rt, "timeline", None) is not None:
                    rt.timeline.add("health", "An interrupted 'forget' couldn't be finished yet; it will finish at the next "
                                    "restart, and nothing from that period can be saved meanwhile.", "privacy")
        saved = await self.db.call(lambda c: {k: self.db.get_kv(c, k) for k in ("expectations", "outcomes", "cases", "counters")})
        self.expect.load(saved.get("expectations"))
        self.outcomes = saved.get("outcomes") or {"good": [], "bad": []}
        if not self.outcomes.get("v"):
            # 1.5.0 counted a script finishing as being "put straight back": start the tally again
            self.outcomes = {"good": [], "bad": [], "v": 2}
        self.cases = saved.get("cases") or {}
        self.counters = saved.get("counters") or {}
        rt = self._rt()
        if self.db.recovered and rt is not None:
            rt.timeline.add("health", "Sentient's memory database was damaged, so a fresh one was started. The old one is kept "
                            "beside it for inspection; facts and settings are unaffected.", self.db.recovered)
        self.ledger = Ledger(self.hass, self.db, getattr(rt, "own", None))
        self.ledger.score = self._score
        self.ledger.hooks.append(self._after_change)
        self.started = True
        await self.async_apply()
        self._unsubs += [
            async_track_time_change(self.hass, self._midnight, hour=0, minute=0, second=30),
            async_track_time_change(self.hass, self._nightly, hour=4, minute=40, second=0),
            async_track_time_interval(self.hass, self._every_ten, timedelta(minutes=10)),
            self.hass.bus.async_listen(EVENT_STATE_CHANGED, self._satellite, event_filter=self._is_satellite),
        ]
        from .const import EVENT_NOTICED
        from .timeline import EVENT_TIMELINE

        self._unsubs.append(self.hass.bus.async_listen(EVENT_NOTICED, self._noticed))
        self._unsubs.append(self.hass.bus.async_listen(EVENT_TIMELINE, self._timeline))
        self.hass.async_create_background_task(self.async_sync_memories(), "sentient memory copy")

    async def async_apply(self) -> None:
        """Start or stop parts to match the settings."""
        if not self.started or self.ledger is None:
            return
        if self.on(CONF_LEDGER):
            await self.ledger.async_start()
        elif self.ledger.enabled:
            await self.ledger.async_stop()
        if self.on(CONF_EXPECTATIONS) and not self.expect.warmed and not self.warming:
            from homeassistant.helpers.start import async_at_started

            self.warming = True
            generation = self._generation

            @callback
            def _go(_hass: HomeAssistant) -> None:
                # only once every integration has loaded, or most devices wouldn't exist yet to be learned
                if generation == self._generation and self.started:
                    self._warm_task = self.hass.async_create_background_task(self._warm(generation), "sentient expectations warm-up")

            self._unsubs.append(async_at_started(self.hass, _go))
        elif not self.on(CONF_EXPECTATIONS):
            self._cancel_warm()

    def _cancel_warm(self) -> None:
        self._generation += 1  # anything still warming up is thrown away
        if self._warm_task is not None and not self._warm_task.done():
            self._warm_task.cancel()
        self._warm_task = None
        self.warming = False

    async def _warm(self, generation: int) -> None:
        """Learn the past into a separate model, then add it in, only if nothing was cleared or stopped meanwhile.
        History ends where the warm-up began, and live changes from then on are counted live, so nothing is counted twice."""
        try:
            past = Expectations()
            learned = await async_warm_up(self.hass, past)
            if generation != self._generation or not self.started or not self.on(CONF_EXPECTATIONS):
                return
            self.expect.merge(past)
            _LOGGER.debug("Expectations learned %s past changes", learned)
            await self.async_save()
        finally:
            if generation == self._generation:
                self.warming = False

    async def async_stop(self) -> None:
        self._cancel_warm()
        for unsub in self._unsubs:
            unsub()
        self._unsubs = []
        if self.ledger is not None and self.ledger.enabled:
            await self.ledger.async_stop()
        if self.started:
            await self.async_save()
            await self.hass.async_add_executor_job(self.db.close)
        self.started = False

    async def async_save(self) -> None:
        # serialised here, in the event loop, so nothing changes underneath while the database thread writes it
        data = {k: json.dumps(v, default=str) for k, v in (("expectations", self.expect.dump()), ("outcomes", self.outcomes),
                                                          ("cases", self.cases), ("counters", self.counters))}
        await self.db.call(lambda c: [self.db.set_kv_text(c, k, v) for k, v in data.items()])

    async def async_prepare_forget(self, since: datetime) -> tuple[float, float]:
        """Persist a privacy deletion before touching any of the source stores.

        Call while holding _forget_lock. An interrupted operation is replayed on
        restart, even if Home Assistant stops immediately after this returns.
        """
        start, end = since.timestamp(), dt_util.utcnow().timestamp()
        pending = [*self._forget_pending, (start, end)]
        await self._forget_store.async_save({"pending": pending})
        self._forget_pending = pending
        self.db.forget_window(start, end)
        return start, end

    def forget_since(self, since: datetime, *, registered: bool = False, until: float | None = None) -> int:
        """'Forget the last N hours', the part that can't wait: the period is refused by every database write from now
        on, and changes still waiting to be saved are dropped. The database deletion itself is ``async_finish_forget``,
        which the caller awaits before saying it's done. (Expectations keep only counts, not records.)"""
        start, end = since.timestamp(), until if until is not None else dt_util.utcnow().timestamp()
        if not registered:
            # Legacy in-memory entry point. User-facing forgetting must call
            # async_prepare_forget first to guarantee restart recovery.
            self.db.forget_window(start, end)
            self._forget_pending.append((start, end))
        dropped = 0
        if self.ledger is not None:
            before = len(self.ledger._buffer)
            self.ledger._buffer = [r for r in self.ledger._buffer if not start <= r["ts"] <= end]
            dropped = before - len(self.ledger._buffer)
            for row in [r for r in self.ledger.recent if start <= r["ts"] <= end]:
                self.ledger.recent.remove(row)
        self._remarks = [(t, e) for t, e in self._remarks if t < start]
        return dropped

    async def async_finish_forget(self) -> dict[str, Any]:
        """Delete every pending forgotten period from the database, recorded first so a restart part-way finishes
        the job (see ``async_start``). Raises if the database couldn't be cleared."""
        pending = list(self._forget_pending)
        if not pending:
            return {}
        await self._forget_store.async_save({"pending": pending})
        gone: dict[str, int] = {}
        for start, end in pending:
            for key, n in (await self.db.call(lambda c, a=start, b=end: self.db.forget_since(c, a, b))).items():
                gone[key] = gone.get(key, 0) + n
        self._forget_pending = [w for w in self._forget_pending if w not in pending]
        await self._forget_store.async_save({"pending": self._forget_pending})
        return gone

    async def async_clear(self, what: str) -> None:
        """'ledger' (every recorded change) or 'learning' (expectations, beliefs, outcomes, cases)."""
        if what in ("ledger", "all") and self.ledger is not None:
            # everything up to now is gone, including what's waiting to be written or being written right now
            self.db.forget_window(0, dt_util.utcnow().timestamp(), "ledger")
            self.ledger._buffer = []
            self.ledger._last_number.clear()
        if what in ("learning", "all"):
            self._cancel_warm()
            self.expect = Expectations()
            self.expect.warmed = True  # cleared on purpose: don't refill it from history
            self.outcomes, self.cases, self.counters = {"good": [], "bad": [], "v": 2}, {}, {}
        if self.ledger is not None and what in ("ledger", "all"):
            self.ledger.recent.clear()
        await self.db.call(lambda c: self.db.clear(c, what))

    # ----- every change -----------------------------------------------------------------------
    def _score(self, row: dict[str, Any]) -> float | None:
        if not self.on(CONF_EXPECTATIONS) or self.ledger is None:
            return None
        before = self.ledger.recent_rows(row["ts"] - 120)
        return self.expect.observe(row, before)

    @callback
    def _after_change(self, row: dict[str, Any]) -> None:
        if self.on(CONF_UNEXPLAINED) and row["cause"] == "device" and self._important(row["entity"]):
            self.hass.async_create_background_task(self._unexplained(row), "sentient unexplained change")
        if self.on(CONF_REACTIONS):
            self._reaction(row)
        if (row.get("surprise") or 0) >= 0.6 and row["domain"] == "binary_sensor" and self.ledger is not None:
            st = self.hass.states.get(row["entity"])
            if st is not None and st.attributes.get("device_class") in ("door", "garage_door", "opening", "window"):
                missing = self.expect.broken_sequence(row, self.ledger.recent_rows(row["ts"] - 120))
                if missing and (rt := self._rt()) is not None:
                    first = missing.split("=")[0]
                    first_name = self.hass.states.get(first).name if self.hass.states.get(first) else first
                    rt.timeline.add("noticed", f"{row.get('name')} opened without {first_name} first, which is unusual for this time.",
                                    "broken routine", entity_id=row["entity"],
                                    decision={"kind": "broken_sequence", "usual_first": missing, "surprise": row["surprise"], "ai_used": False})

    def _important(self, entity_id: str) -> bool:
        chosen = self.options.get(CONF_UNEXPLAINED_ENTITIES) or []
        if chosen:
            return entity_id in chosen
        return entity_id.split(".", 1)[0] in IMPORTANT_DOMAINS

    async def _unexplained(self, row: dict[str, Any]) -> None:
        if row["old"] in ("unavailable", "unknown") or row["new"] in ("unavailable", "unknown"):
            return
        last = self._said_unexplained.get(row["entity"], 0)
        if row["ts"] - last < UNEXPLAINED_GAP:
            return
        self._said_unexplained[row["entity"]] = row["ts"]  # reserved now, so a quick second change can't also be said
        week = row["ts"] - 7 * 86400
        earlier = await self.db.call(lambda c: self.db.changes(c, entities=[row["entity"]], since=week,
                                                              causes=["device"], limit=200))
        hour = _local(row["ts"]).hour
        same = {(_local(r["ts"]).date()) for r in earlier if r["new"] == row["new"]
                and min((_local(r["ts"]).hour - hour) % 24, (hour - _local(r["ts"]).hour) % 24) <= 1}
        same.add(_local(row["ts"]).date())
        surprise = row.get("surprise")
        speak = surprise is not None and surprise >= 0.6  # only said when it's unusual for the time
        if not speak and len(same) < 2:
            self._said_unexplained[row["entity"]] = last  # nothing said: free the slot again
            return  # usual, or still learning this device, and not a repeat: the ledger has it, nothing to say
        text = (f"{row.get('name') or row['entity']} went from {row['old']} to {row['new']} at {_clock(row['ts'])}, "
                "and nothing in Home Assistant did it — the device itself, its app, or someone at it.")
        if len(same) >= 2:
            text += f" That's the {ordinal(len(same))} time this week, always around {_hour_words(hour)}."
        rt = self._rt()
        if rt is None:
            return
        rt.timeline.add("health", text, "unexplained change", entity_id=row["entity"],
                        decision={"kind": "unexplained_change", "evidence": [describe(row)],
                                  "surprise": surprise, "repeats": len(same), "ai_used": False})
        if not speak:
            return  # a repeat that's becoming usual: on the timeline, not said
        from .reasons import because

        with because("it changed with nothing in Home Assistant behind it"):
            await rt.speech.async_say(text, "normal", None, source="unexplained")

    def _reaction(self, row: dict[str, Any]) -> None:
        """Sentient's change switched straight back = bad; a remark followed by a person acting = good."""
        if self.ledger is None or row["cause"] == "sentient" or row["domain"] not in TOGGLES:
            return  # scripts, scenes, buttons and the like switch themselves back: that's not a reaction
        now = row["ts"]
        for prev in reversed(self.ledger.recent_rows(now - REVERT_SECONDS, row["entity"])[:-1]):
            if prev["cause"] == "sentient" and prev["old"] == row["new"]:
                self.outcomes["bad"].append(now)
                rt = self._rt()
                if rt is not None and getattr(rt, "feedback", None) is not None:
                    rt.feedback.record(row["entity"], 0.5, "switched straight back after Sentient changed it", kind="offers")
                    rt.timeline.add("mind", f"Noted: {row.get('name') or row['entity']} was put back straight after I changed it.",
                                    "learning from reactions", entity_id=row["entity"])
                break
        if row["cause"] in ("person", "voice", "device"):
            for when, entities in self._remarks:
                if 0 <= now - when <= ACTED_SECONDS and row["entity"] in entities:
                    self.outcomes["good"].append(now)
                    self._remarks.remove((when, entities))
                    break
        self._trim_outcomes()

    def _trim_outcomes(self) -> None:
        cutoff = dt_util.utcnow().timestamp() - 30 * 86400
        self.outcomes = {k: ([t for t in v if t >= cutoff] if isinstance(v, list) else v) for k, v in self.outcomes.items()}

    @callback
    def _noticed(self, event: Event) -> None:
        if str(event.data.get("result", "")).startswith("spoken") or event.data.get("result") == "spoke":
            self._remarks = [(t, e) for t, e in self._remarks if dt_util.utcnow().timestamp() - t < ACTED_SECONDS]
            self._remarks.append((dt_util.utcnow().timestamp(), list(event.data.get("entities") or [])))

    # ----- surprise for other features --------------------------------------------------------
    def surprise_now(self, entity_id: str, state: Any) -> float | None:
        if not self.on(CONF_EXPECTATIONS):
            return None
        st = self.hass.states.get(entity_id)
        row = {"entity": entity_id, "new": str(state), "old": "", "ts": dt_util.utcnow().timestamp(),
               "domain": entity_id.split(".", 1)[0]}
        if st is not None and str(state) == st.state:
            row["ts"] = st.last_changed.timestamp()
        return self.expect.surprise(row)

    def usual_now(self, changes: list[tuple[str, Any]]) -> bool | None:
        """True when every change is usual for this time (None while still learning any of them)."""
        scores = [self.surprise_now(e, s) for e, s in changes]
        if not scores or any(s is None for s in scores):
            return None
        return all(s < 0.3 for s in scores)

    def gate_remark(self, record: dict[str, Any], changes: list[tuple[str, Any]]) -> bool:
        """Surprise gating for remarks: True = skip asking the AI. In watch mode only notes what it would do."""
        mode = self.gating
        if mode == "off" or not self.on(CONF_EXPECTATIONS):
            return False
        usual = self.usual_now(changes)
        if usual is not True:
            return False
        if mode == "watch":
            record["would_skip"] = "usual for this time"
            self.count("would_gate")
            return False
        record["result"] = "skipped: usual for this time"
        self.count("gated")
        return True

    def count(self, what: str) -> None:
        day = dt_util.now().date().isoformat()
        self.counters.setdefault(day, {})
        self.counters[day][what] = self.counters[day].get(what, 0) + 1
        if len(self.counters) > 60:
            for key in sorted(self.counters)[:-60]:
                self.counters.pop(key, None)

    # ----- case memory ------------------------------------------------------------------------
    @staticmethod
    def case_key(data: dict[str, Any]) -> str | None:
        if data.get("attachments"):
            return None  # pictures and clips are never the same twice
        if not str(data.get("task_name") or "").startswith(CASE_TASKS):
            return None  # looks ahead, reflections, camera checks and the like are always asked afresh
        body = {k: data.get(k) for k in ("task_name", "instructions", "structure", "entity_id")}
        return hashlib.sha1(json.dumps(body, sort_keys=True, default=str).encode()).hexdigest()

    def case_get(self, data: dict[str, Any]) -> dict[str, Any] | None:
        if not self.on(CONF_CASES) or (key := self.case_key(data)) is None:
            return None
        case = self.cases.get(key)
        if case is None or dt_util.utcnow().timestamp() - case["ts"] > CASE_HOURS * 3600:
            return None
        self.count("ai_saved")
        return case["response"]

    def case_put(self, data: dict[str, Any], response: dict[str, Any] | None) -> None:
        if not self.on(CONF_CASES) or not response or (key := self.case_key(data)) is None:
            return
        self.cases[key] = {"ts": dt_util.utcnow().timestamp(), "response": response}
        if len(self.cases) > CASE_MAX:
            for old in sorted(self.cases, key=lambda k: self.cases[k]["ts"])[: len(self.cases) - CASE_MAX]:
                self.cases.pop(old, None)

    def case_clear(self) -> None:
        self.cases.clear()

    # ----- voice speed ------------------------------------------------------------------------
    @staticmethod
    @callback
    def _is_satellite(data: Any) -> bool:
        return str(data.get("entity_id", "")).startswith("assist_satellite.")

    @callback
    def _satellite(self, event: Event) -> None:
        new = event.data.get("new_state")
        if new is None:
            return
        info = self.voice.setdefault(new.entity_id, {"samples": [], "warned": 0.0})
        if new.state == "listening":
            info["listening_since"] = new.last_changed.timestamp()
        elif new.state == "processing" and info.get("listening_since"):
            info["heard_after"] = new.last_changed.timestamp() - info["listening_since"]

    def voice_request(self, satellite_id: str | None, text: str, context: Any = None, room: str | None = None) -> None:
        """Called for every voice request Sentient answers."""
        if self.ledger is not None and context is not None:
            self.ledger.note_context(context, "voice", room)
        if not satellite_id or not self.on(CONF_VOICE_SPEED):
            return
        info = self.voice.get(satellite_id)
        if not info or info.get("heard_after") is None:
            return
        took = info.pop("heard_after")
        words = max(1, len((text or "").split()))
        expected = 6 + words * 0.5
        slow = took > max(VOICE_GRACE, 2.5 * expected)
        info["samples"] = (info["samples"] + [{"seconds": round(took, 1), "words": words, "slow": slow}])[-5:]
        recent = info["samples"][-3:]
        now = dt_util.utcnow().timestamp()
        if slow and sum(s["slow"] for s in recent) >= 2 and now - info.get("warned", 0) > 6 * 3600:
            info["warned"] = now
            self.hass.async_create_background_task(self._slow_speaker(satellite_id, took, words), "sentient slow speaker")

    async def _slow_speaker(self, satellite_id: str, took: float, words: int) -> None:
        rt = self._rt()
        if rt is None:
            return
        st = self.hass.states.get(satellite_id)
        name = st.name if st else satellite_id
        text = (f"The {name} is slow to hear you: a {words}-word request took {round(took)} seconds to come through. "
                "Restarting it usually fixes this.")
        restart = self._restart_button(satellite_id)
        if restart and self.on(CONF_VOICE_RESTART):
            await self.hass.services.async_call("button", "press", {"entity_id": restart}, blocking=False)
            text = text.replace("Restarting it usually fixes this.", "I've restarted it.")
        rt.timeline.add("health", text, "voice speed", entity_id=satellite_id, restart=restart)

    def _restart_button(self, satellite_id: str) -> str | None:
        ents = er.async_get(self.hass)
        entry = ents.async_get(satellite_id)
        if entry is None or not entry.device_id:
            return None
        for e in er.async_entries_for_device(ents, entry.device_id):
            if e.domain == "button" and (e.original_device_class == "restart" or e.device_class == "restart"
                                         or e.entity_id.endswith("_restart")):
                return e.entity_id
        return None

    def voice_issues(self) -> list[dict[str, Any]]:
        out = []
        for sat, info in self.voice.items():
            recent = info.get("samples", [])[-3:]
            if recent and sum(s["slow"] for s in recent) >= 2:
                st = self.hass.states.get(sat)
                out.append({"entity_id": sat, "name": st.name if st else sat, "samples": recent,
                            "restart": self._restart_button(sat)})
        return out

    # ----- recall -----------------------------------------------------------------------------
    async def async_history(self, what: str | None = None, hours: float = 24, cause: str | None = None,
                            changed_to: str | None = None, limit: int = 20) -> dict[str, Any]:
        if self.ledger is None or not self.ledger.enabled:
            return {"error": "The house ledger is switched off (Settings → Memory & learning)."}
        entities = resolve_entities(self.hass, what) if what else None
        if what and not entities:
            return {"error": f"No device or room called '{what}'."}
        since = dt_util.utcnow().timestamp() - max(0.1, hours) * 3600
        rows = await self.ledger.async_changes(entities=entities, since=since,
                                               causes=[cause] if cause else None, limit=200)
        if changed_to:
            rows = [r for r in rows if str(r["new"]).casefold() == changed_to.casefold()]
        rows = rows[:limit]
        changes = [{
            "when": _local(r["ts"]).strftime("%a %d %b %H:%M"), "device": r.get("name") or r["entity"],
            "entity_id": r["entity"], "from": r["old"], "to": r["new"], "area": r.get("area"),
            "caused_by": CAUSE_WORDS.get(r["cause"], r["cause"]), "who": r.get("who"),
            "usual": information(r.get("surprise")),
        } for r in rows]
        out: dict[str, Any] = {"changes": changes, "hours": hours}
        if not changes:
            out["note"] = "Nothing recorded for that in the ledger (it only knows changes since it was switched on)."
        return out

    async def async_root_cause(self, what: str, allowed: Callable[[str], bool] | None = None) -> dict[str, Any]:
        """What changed around the last time this device changed or went wrong, oldest first."""
        if self.ledger is None or not self.ledger.enabled:
            return {"error": "The house ledger is switched off (Settings → Memory & learning)."}
        entities = resolve_entities(self.hass, what)
        if not entities:
            return {"error": f"No device called '{what}'."}
        target = entities[0]
        last = await self.ledger.async_changes(entities=[target], limit=1)
        if not last:
            return {"error": "No recorded changes for that device yet."}
        row = last[0]
        ents = er.async_get(self.hass)
        entry = ents.async_get(target)
        siblings = [e.entity_id for e in er.async_entries_for_device(ents, entry.device_id)] if entry and entry.device_id else []
        window = await self.ledger.async_changes(since=row["ts"] - 3600, until=row["ts"] + 5, limit=400, newest_first=False)
        related = []
        for r in window:
            same_device = r["entity"] in siblings
            same_area = row.get("area") and r.get("area") == row.get("area")
            dropped = r["new"] in ("unavailable", "unknown") or r["old"] in ("unavailable", "unknown")
            if allowed is not None and not allowed(r["entity"]):
                continue  # only what this person's voice assistant may see
            if r["entity"] == target or same_device or (same_area and r["domain"] not in ("sensor",)) or (dropped and same_area):
                related.append({"when": _clock(r["ts"]), "line": describe(r), "same_device": same_device,
                                "went_offline": r["new"] in ("unavailable", "unknown")})
        cause = row["cause"]
        verdict = {
            "device": "Nothing in Home Assistant changed it: it came from the device itself, its own app, or someone at it.",
            "sentient": f"Sentient changed it ({row.get('who') or 'one of its features'}).",
            "automation": f"An automation changed it: {row.get('who')}.",
            "script": f"A script changed it: {row.get('who')}.",
            "person": f"Someone in Home Assistant changed it ({row.get('who') or 'a user'}).",
            "voice": f"A voice request changed it ({row.get('who') or 'a speaker'}).",
        }.get(cause, "Home Assistant changed it (another integration's service call).")
        offline = [x for x in related if x["went_offline"]]
        if offline:
            verdict += " Shortly before, something on the same device or in the same room went offline."
        return {"device": row.get("name") or target, "last_change": describe(row), "when": _local(row["ts"]).strftime("%a %d %b %H:%M"),
                "conclusion": verdict, "timeline_before": related[-15:],
                "limits": "Built from recorded changes only; a cause outside Home Assistant can't be seen."}

    async def async_search(self, words: list[str], kinds: list[str] | None = None, limit: int = 8) -> list[dict[str, Any]]:
        rows = await self.db.call(lambda c: self.db.search(c, words, kinds, limit))
        return [{"kind": r["kind"], "when": r.get("ts"), "text": r["body"][:400]} for r in rows]

    # ----- copies of facts, conversations and the timeline -------------------------------------
    async def async_sync_memories(self) -> None:
        rt = self._rt()
        if rt is None or not self.started:
            return
        items = rt.memory.items
        facts = [(m.id, m.created, m.text, {"category": m.category, "source": m.source})
                 for m in (items() if callable(items) else items)]
        episodes = []
        for e in list(getattr(rt.conversations, "episodes", []) or []):
            body = " ".join(f"{'They' if t.get('who') == 'them' else 'You'}: {t.get('text', '')}"
                            for t in e.get("turns", []) if isinstance(t, dict))
            # timestamped by last activity (as the conversation store forgets and expires them), start kept alongside
            episodes.append((str(e.get("id") or e.get("start")), e.get("last") or e.get("start"),
                             f"{e.get('room') or ''} {body}".strip(), {"room": e.get("room"), "start": e.get("start")}))

        def _sync(conn: Any) -> None:  # forgotten periods are refused inside put_memory
            have = {r[0] for r in conn.execute("SELECT ref FROM memories WHERE kind = 'fact'")}
            keep = set()
            for ref, ts, body, data in facts:
                keep.add(ref)
                self.db.put_memory(conn, "fact", ref, ts, body, data)
            for ref in have - keep:
                self.db.drop_memory(conn, "fact", ref)
            for ref, ts, body, data in episodes:
                self.db.put_memory(conn, "conversation", ref, ts, body, data)

        try:
            await self.db.call(_sync)
        except Exception:  # noqa: BLE001
            _LOGGER.debug("Memory copy failed", exc_info=True)

    @callback
    def _timeline(self, event: Event) -> None:
        item = dict(event.data)
        if not item.get("text"):
            return
        ref = hashlib.sha1(f"{item.get('time')}{item.get('kind')}{item.get('text')}".encode()).hexdigest()[:16]
        body = " — ".join(str(x) for x in (item.get("text"), item.get("detail")) if x)
        self.hass.async_create_background_task(
            self.db.call(lambda c: self.db.put_memory(c, "timeline", ref, item.get("time"), body, {"kind": item.get("kind")})),
            "sentient timeline copy")

    # ----- what's going on now ----------------------------------------------------------------
    def situations(self) -> list[str]:
        if not self.on(CONF_SITUATIONS):
            return []
        out: list[str] = []
        rt = self._rt()
        now = dt_util.utcnow().timestamp()
        if rt is not None and getattr(rt, "sleep", None) is not None and getattr(rt.sleep, "asleep", False):
            out.append("The house is asleep.")
        if self.ledger is not None:
            recent = self.ledger.recent_rows(now - 900)
            home = sorted({r.get("name") or r["entity"] for r in recent if r["domain"] == "person" and r["new"] == "home"})
            if home:
                out.append(", ".join(home) + " just got home.")
            moving = sorted({r["area"] for r in recent if r["domain"] == "binary_sensor" and r["new"] == "on" and r.get("area")
                             and (st := self.hass.states.get(r["entity"])) is not None
                             and st.attributes.get("device_class") in ("motion", "occupancy", "presence")})
            if moving:
                out.append("Movement in the last 15 minutes: " + ", ".join(moving[:4]) + ".")
            odd: list[dict[str, Any]] = []
            for r in self.ledger.recent_rows(now - 3600):
                if (r.get("surprise") or 0) < 0.9 or r["cause"] == "sentient":
                    continue
                if any(o["entity"] == r["entity"] and 0 <= r["ts"] - o["ts"] <= BURST_SECONDS for o in odd):
                    continue  # the same device flickering: the first change says what happened
                odd.append(r)
            if odd:
                out.append("Unusual for this time in the last hour: " + "; ".join(
                    f"{r.get('name') or r['entity']} {r['new']} at {_clock(r['ts'])}" for r in odd[-3:]) + ".")
        return out[:5]

    # ----- trust in sensors -------------------------------------------------------------------
    def trust(self) -> list[dict[str, Any]]:
        """Sensors that count for less: ones that keep dropping offline, and ones that never rest."""
        rt = self._rt()
        out = []
        if rt is not None and getattr(rt, "watchdog", None) is not None:
            for device, times in rt.watchdog.drops.items():
                if len(times) >= 3:
                    out.append({"name": device, "trust": round(max(0.1, 1 - 0.03 * len(times)), 2),
                                "why": f"dropped offline {len(times)} times this week"})
        if rt is not None and getattr(rt, "sleep", None) is not None:
            for r in rt.sleep.never_rests():
                out.append({"name": r.get("name"), "trust": 0.3, "why": "on most of the time, so it can't say who's up"})
        return sorted(out, key=lambda x: x["trust"])[:10]

    # ----- the nightly thinking ---------------------------------------------------------------
    @callback
    def _midnight(self, now: datetime) -> None:
        yesterday = dt_util.as_local(now) - timedelta(hours=1)
        if self.expect.last_day != yesterday.date().isoformat():
            self.expect.new_day(yesterday)
            self.expect.last_day = yesterday.date().isoformat()

    @callback
    def _every_ten(self, _now: datetime) -> None:
        self.hass.async_create_background_task(self._tick(), "sentient brain save")

    async def _tick(self) -> None:
        try:
            await self.async_sync_memories()
            await self.async_save()
        except Exception:  # noqa: BLE001 - try again in ten minutes
            _LOGGER.debug("Brain save failed", exc_info=True)

    @callback
    def _nightly(self, now: datetime) -> None:
        self.hass.async_create_background_task(self.async_think_overnight(now), "sentient overnight learning")

    async def async_think_overnight(self, now: datetime | None = None) -> dict[str, Any]:
        now = now or dt_util.now()
        yesterday = dt_util.as_local(now) - timedelta(days=1)
        start, end = day_bounds(yesterday)
        cutoff_ledger = dt_util.utcnow().timestamp() - LEDGER_DAYS * 86400
        cutoff_memory = (dt_util.utcnow() - timedelta(days=MEMORY_DAYS)).isoformat()
        report: dict[str, Any] = {}

        def _night(conn: Any) -> dict[str, Any]:
            out: dict[str, Any] = {"pruned": self.db.prune_ledger(conn, cutoff_ledger)}
            for kind in ("conversation", "timeline"):
                self.db.prune_memories(conn, kind, cutoff_memory)
            if self.on(CONF_BELIEFS):
                day_rows = self.db.changes(conn, since=start, until=end, causes=["device"], limit=5000)
                out["tested"] = self.beliefs.test_day(conn, day_rows, yesterday.date().isoformat())
                fortnight = self.db.changes(conn, since=end - 14 * 86400, until=end, causes=["device"], limit=20000)
                found = [b for b in find_by_itself(fortnight) if self._important(b["subject"])
                         or b["subject"].split(".")[0] in ("climate", "light", "switch", "lock", "cover", "fan")]
                out["formed"] = self.beliefs.upsert_new(conn, found)
                out["held"] = [b for b in self.beliefs.all(conn) if b["status"] == "held" and not b["asked"]]
            return out

        try:
            report = await self.db.call(_night)
        except Exception:  # noqa: BLE001
            _LOGGER.warning("Sentient's overnight learning failed", exc_info=True)
            return {}
        try:  # after the night's changes are committed: a checkpoint can't run inside a write transaction
            report["checkpoint"] = await self.db.call(self.db.checkpoint)
        except Exception:  # noqa: BLE001
            _LOGGER.debug("WAL checkpoint failed", exc_info=True)
        rt = self._rt()
        for belief in report.pop("held", [])[:1]:
            if rt is not None and getattr(rt, "curiosity", None) is not None and rt.curiosity.enabled:
                p = belief["params"]
                name = (self.hass.states.get(p["entity"]).name if self.hass.states.get(p["entity"]) else p["entity"])
                question = f"Is {name} meant to go to {p['state']} by itself around {_hour_words(p['hour'])}? It keeps happening."
                if rt.curiosity.wonder(question, belief["claim"], "beliefs"):
                    await self.db.call(lambda c, bid=belief["id"]: self.beliefs.mark_asked(c, bid))
        score = self.expect.score(yesterday.date().isoformat())
        tested = report.get("tested") or {}
        if rt is not None and (tested or score.get("judged")):
            parts = []
            if score.get("percent") is not None:
                parts.append(f"I expected {score['percent']}% of yesterday's {score['judged']} changes")
            if tested:
                parts.append(f"tested beliefs: {tested.get('held', 0)} held, {tested.get('missed', 0)} missed"
                             + (f", {tested['dropped']} dropped" if tested.get("dropped") else ""))
            if report.get("formed"):
                parts.append(f"{report['formed']} new pattern{'s' if report['formed'] != 1 else ''} to test")
            rt.timeline.add("mind", "Overnight: " + "; ".join(parts) + ".", "learning", decision={"kind": "overnight_learning", "ai_used": False})
        await self.async_save()
        return report

    # ----- what the Mind tab shows ------------------------------------------------------------
    async def async_status(self) -> dict[str, Any]:
        today = dt_util.now().date().isoformat()
        rt = self._rt()
        ai = getattr(rt, "ai", None) if rt is not None else None
        ai_today = ai.used if ai is not None and isinstance(getattr(type(ai), "used", None), property) else None
        counts = self.counters.get(today, {})
        saved = counts.get("ai_saved", 0) + counts.get("gated", 0) + counts.get("local", 0)
        good, bad = len(self.outcomes.get("good", [])), len(self.outcomes.get("bad", []))

        def _counts(conn: Any) -> dict[str, Any]:
            return {
                "ledger": conn.execute("SELECT COUNT(*) FROM ledger").fetchone()[0],
                # controlled devices only: a motion sensor or thermometer changes by itself all the time, by design
                "unexplained_week": conn.execute(
                    f"SELECT COUNT(*) FROM ledger WHERE cause = 'device' AND ts > ? AND domain IN ({','.join('?' * len(CONTROLLED))}) "
                    "AND old NOT IN ('unavailable', 'unknown') AND new NOT IN ('unavailable', 'unknown')",  # not restarts
                    (dt_util.utcnow().timestamp() - 7 * 86400, *CONTROLLED)).fetchone()[0],
                "memories": dict(conn.execute("SELECT kind, COUNT(*) FROM memories GROUP BY kind").fetchall()),
                "beliefs": self.beliefs.all(conn)[:12],
            }

        if self.ledger is not None:
            await self.ledger.async_flush()
        stored = await self.db.call(_counts) if self.started else {}
        calls = ai_today if isinstance(ai_today, int) else None
        return {
            "ledger_on": bool(self.ledger and self.ledger.enabled), "expectations_on": self.on(CONF_EXPECTATIONS),
            "gating": self.gating, "warming": self.warming, "search": "full-text" if self.db.fts else "words",
            "prediction": self.expect.score(), "trend": self.expect.trend(),
            "outcome": {"good": good, "bad": bad, "percent": round(100 * good / (good + bad)) if good + bad else None},
            "independence": {"ai_calls": calls, "without_ai": saved,
                             "percent": round(100 * saved / (saved + calls)) if calls is not None and saved + calls else None},
            "recall_misses": counts.get("recall_miss", 0), "would_skip": counts.get("would_gate", 0), "disk_mb": await self.hass.async_add_executor_job(self.db.size_mb),
            "voice": self.voice_issues(), "trust": self.trust(), "situations": self.situations(), **stored,
        }
