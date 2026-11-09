"""Inner life: noticing its own mistakes, an ordinary day, and where people go next. No AI.

Three small, plain-rule habits that make the house feel aware rather than only scripted:

* **Corrections.** When someone changes back something the house did a moment ago (a routine it took
  over, an alarm's action, the heating), that is a correction. A learned routine that keeps being undone
  (three times in two weeks) is handed back — switched off, and the timeline says so. Movement-driven
  switching (motion lights, auto-off) is never counted: people walking past at odd times is not feedback.
* **A normal day.** Today is held up against what's usual. Today that means one thing: nobody up yet when
  someone usually is by now. It is noted, never said aloud and nothing is switched.
* **Next room.** From which room people went to next, by time of day, it guesses where someone goes after
  entering a room, then checks itself. The score is on the Mind page.

Everything it concludes is kept as a short first-person thought for the Mind page (and can be dismissed).
Each part has its own switch in Settings → Mind; switching one off stops its watching and hides what it
found, and keeps what was learned.
"""

from __future__ import annotations

from datetime import datetime, timedelta
import logging
from typing import Any, Callable

from homeassistant.const import EVENT_STATE_CHANGED, STATE_ON
from homeassistant.core import Event, EventStateChangedData, HomeAssistant, callback
from homeassistant.helpers.event import async_track_time_change, async_track_time_interval
from homeassistant.helpers.storage import Store
from homeassistant.util import dt as dt_util

from .const import CONF_LEARN_CORRECTIONS, CONF_NORMAL_DAY, CONF_SHARE_LEARNING, CONF_THOUGHTS, DOMAIN
from .own import own_actions

_LOGGER = logging.getLogger(__name__)

STORAGE_VERSION = 1
UNDO_WITHIN = 600  # seconds: a change back this soon after the house acted is a correction
CORRECTIONS_TO_STOP = 3  # undone this many times in LOOKBACK_DAYS and the routine is handed back
LOOKBACK_DAYS = 14
KEEP_DAYS = 45
WATCHED_WHY = ("learned routine", "alarm", "routine", "heating")  # what a correction means something for
ROOM_DEBOUNCE = 15  # seconds in a room before a hop to another counts (a sensor flickering is not a journey)
NEXT_WITHIN = 600  # the next room must be entered this soon to count
MIN_SEEN = 6  # journeys seen from a room (at this time of day) before it dares guess
MIN_SHARE = 0.4
SCORES_KEPT = 200
TRUST_AT = 0.6
TRUST_MIN_TRIES = 30
LATE_AFTER = 90  # minutes past the usual getting-up time before "nobody's up" is noted
LATE_UNTIL = 300
THOUGHTS_KEPT = 40
THOUGHT_DAYS = 7


def bucket(when: datetime) -> str:
    """Weekday/weekend and a three-hour slot: 'wd6' is a weekday between 06:00 and 09:00."""
    return f"{'we' if when.weekday() >= 5 else 'wd'}{when.hour // 3}"


class InnerLife:
    def __init__(self, hass: HomeAssistant, entry_id: str, options: dict[str, Any], rt_getter: Callable[[], Any]) -> None:
        self.hass = hass
        self.options = options
        self._rt = rt_getter
        self._store: Store[dict[str, Any]] = Store(hass, STORAGE_VERSION, f"{DOMAIN}.{entry_id}.inner")
        self.corrections: list[dict[str, Any]] = []
        self.transitions: dict[str, dict[str, int]] = {}
        self.scores: list[int] = []
        self.thoughts: list[dict[str, Any]] = []
        self.dismissed: list[str] = []
        self._mine: dict[str, tuple[float, str, str]] = {}  # entity -> (when, state, why) the house last set
        self._room: str | None = None
        self._room_t = 0.0
        self._pred: tuple[str, float] | None = None
        self._unsubs: list[Callable[[], None]] = []
        self._inside: set[str] = set()
        self._inside_at = 0.0
        self.shared: list[str] = []  # learnings already said aloud
        self.shared_day: str | None = None

    # ----- switches ----------------------------------------------------------------------
    @property
    def learn_on(self) -> bool:
        return bool(self.options.get(CONF_LEARN_CORRECTIONS, True))

    @property
    def day_on(self) -> bool:
        return bool(self.options.get(CONF_NORMAL_DAY, True))

    @property
    def thoughts_on(self) -> bool:
        return bool(self.options.get(CONF_THOUGHTS, True))

    # ----- life cycle --------------------------------------------------------------------
    async def async_start(self) -> None:
        data = await self._store.async_load() or {}
        self.corrections = list(data.get("corrections") or [])
        self.transitions = {k: dict(v) for k, v in (data.get("transitions") or {}).items()}
        self.scores = [int(x) for x in data.get("scores") or []][-SCORES_KEPT:]
        self.thoughts = list(data.get("thoughts") or [])
        self.dismissed = list(data.get("dismissed") or [])
        self.shared = list(data.get("shared") or [])
        self.shared_day = data.get("shared_day")
        self._unsubs = [
            self.hass.bus.async_listen(EVENT_STATE_CHANGED, self._on_state),
            async_track_time_change(self.hass, self._nightly, hour=4, minute=50, second=0),
            async_track_time_interval(self.hass, self._every_ten_minutes, timedelta(minutes=10)),
        ]

    @callback
    def async_stop(self) -> None:
        while self._unsubs:
            self._unsubs.pop()()

    def _save(self) -> None:
        self._store.async_delay_save(self._data, 30)

    def _data(self) -> dict[str, Any]:
        return {"corrections": self.corrections[-300:], "transitions": self.transitions, "scores": self.scores[-SCORES_KEPT:],
                "thoughts": self.thoughts[-THOUGHTS_KEPT:], "dismissed": self.dismissed[-200:],
                "shared": self.shared[-200:], "shared_day": self.shared_day}

    # ----- watching ----------------------------------------------------------------------
    @callback
    def _on_state(self, event: Event[EventStateChangedData]) -> None:
        new = event.data["new_state"]
        if new is None or new.state in ("unavailable", "unknown"):
            return
        if self.learn_on:
            self._watch_corrections(new)
        if self.thoughts_on and new.entity_id.startswith("binary_sensor.") and new.state == STATE_ON:
            self._watch_rooms(new.entity_id, new.last_changed.timestamp())

    def _watch_corrections(self, new) -> None:
        own = own_actions(self.hass)
        if own is None:
            return
        entity_id, now = new.entity_id, new.last_changed.timestamp()
        if own.is_own(new.context):
            why = own.why(new.context) or ""
            if why.startswith(WATCHED_WHY):  # motion lights, auto-off and light shows are deliberately not here
                self._mine[entity_id] = (now, new.state, why)
            else:
                self._mine.pop(entity_id, None)
            return
        mine = self._mine.get(entity_id)
        if mine is None:
            return
        when, state, why = mine
        if now - when > UNDO_WITHIN:
            del self._mine[entity_id]
            return
        if new.state == state or new.context.parent_id is not None:
            return  # nothing changed, or another automation did it: not a person
        del self._mine[entity_id]
        name = (self.hass.states.get(entity_id) or new).name
        self.corrections.append({"t": now, "entity": entity_id, "name": name, "why": why, "to": new.state})
        self._save()

    def _inside_sensors(self) -> set[str]:
        now = dt_util.utcnow().timestamp()
        if not self._inside or now - self._inside_at > 3600:
            from .presence import inside_sensors

            self._inside, self._inside_at = set(inside_sensors(self.hass, self.options)), now
        return self._inside

    def _watch_rooms(self, entity_id: str, ts: float) -> None:
        if entity_id not in self._inside_sensors():
            return
        from .presence import area_of

        area = area_of(self.hass, entity_id)
        if not area:
            return
        if area == self._room:
            self._room_t = ts
            return
        if self._room is not None and ts - self._room_t < ROOM_DEBOUNCE:
            return  # a flicker between two sensors, not somebody walking
        when = dt_util.as_local(datetime.fromtimestamp(ts, dt_util.UTC))
        if self._pred is not None:
            guess, made = self._pred
            if ts - made <= NEXT_WITHIN:
                self.scores.append(1 if guess == area else 0)
                self.scores = self.scores[-SCORES_KEPT:]
            self._pred = None
        if self._room is not None and ts - self._room_t <= NEXT_WITHIN:
            slot = self.transitions.setdefault(f"{bucket(when)}|{self._room}", {})
            slot[area] = slot.get(area, 0) + 1
        self._room, self._room_t = area, ts
        slot = self.transitions.get(f"{bucket(when)}|{area}") or {}
        total = sum(slot.values())
        if total >= MIN_SEEN:
            best = max(slot, key=slot.get)
            if slot[best] / total >= MIN_SHARE:
                self._pred = (best, ts)
        self._save()

    # ----- lessons -----------------------------------------------------------------------
    @callback
    def _nightly(self, _now) -> None:
        cutoff = dt_util.utcnow().timestamp() - KEEP_DAYS * 86400
        self.corrections = [c for c in self.corrections if c["t"] >= cutoff]
        if self.learn_on:
            self.evaluate_lessons()
        self.tidy_memory()
        self._save()

    def tidy_memory(self) -> list[str]:
        """Let go of memories whose day has passed, and link older ones to the devices and rooms they name."""
        rt = self._rt()
        memory = getattr(rt, "memory", None) if rt is not None else None
        if memory is None:
            return []
        gone = memory.prune_expired()
        for m in memory.items:
            if not m.links and (links := memory.links_for(m.text)):
                m.links = links
        if gone:
            names = "; ".join(m.text.rstrip(".") for m in gone[:3])
            self._think(f"expired:{dt_util.now().date().isoformat()}", f"I've let go of {len(gone)} thing{'s' if len(gone) != 1 else ''} whose day has passed: {names}.",
                        "dated memories are kept until the day after", "lesson")
            rt.timeline.add("mind", f"Forgot {len(gone)} out-of-date memor{'ies' if len(gone) != 1 else 'y'}", names[:200])
        return [m.text for m in gone]

    def evaluate_lessons(self) -> list[str]:
        """Look at the last fortnight's corrections; hand back a routine that keeps being undone."""
        rt = self._rt()
        since = dt_util.utcnow().timestamp() - LOOKBACK_DAYS * 86400
        counts: dict[tuple[str, str], list[dict[str, Any]]] = {}
        for c in self.corrections:
            if c["t"] >= since:
                counts.setdefault((c["why"], c["entity"]), []).append(c)
        done: list[str] = []
        for (why, entity_id), items in counts.items():
            if len(items) < CORRECTIONS_TO_STOP:
                continue
            name = items[-1]["name"]
            key = f"undo:{entity_id}:{why}"
            if why.startswith("learned routine") and rt is not None and getattr(rt, "learner", None) is not None:
                routine = next((r for r in rt.learner.all() if r.entity_id == entity_id and r.adopted), None)
                if routine is not None:
                    rt.learner.adopt(routine.id, False)
                    text = f"I've stopped doing this one: {routine.describe().rstrip('.')}. You put it back {len(items)} times in two weeks."
                    if self._think(key, text, "a routine you keep undoing is one I got wrong", "lesson"):
                        rt.timeline.add("mind", f"Handed back a routine: {name}", f"changed back {len(items)} times in {LOOKBACK_DAYS} days")
                    done.append(text)
                    # forget these so a fresh start isn't judged by old mistakes
                    self.corrections = [c for c in self.corrections if (c["why"], c["entity"]) != (why, entity_id)]
                    continue
            text = f"The {name.lower()} keeps being changed back after the house does it ({len(items)} times in two weeks). I'm not sure what I'm getting wrong."
            self._think(key, text, why, "lesson")
            done.append(text)
        return done

    # ----- an unusual day ----------------------------------------------------------------
    @callback
    def _every_ten_minutes(self, now) -> None:
        if self.day_on:
            self.check_day(dt_util.as_local(now))
        if self.options.get(CONF_SHARE_LEARNING, True):
            local = dt_util.as_local(now)
            if (pick := self.to_share(local)) is not None:
                self.hass.async_create_background_task(self.async_share(pick, local), "sentient_share_learning")

    def to_share(self, local: datetime) -> dict[str, Any] | None:
        """Once an evening: the freshest thing learned in the last two days that hasn't been said yet."""
        if not (17 * 60 <= local.hour * 60 + local.minute <= 20 * 60 + 30) or self.shared_day == local.date().isoformat():
            return None
        since = dt_util.utcnow().timestamp() - 2 * 86400
        fresh = [t for t in self.thoughts if t["t"] >= since and t["key"] not in self.shared and t["kind"] in ("lesson", "day")]
        rt = self._rt()
        memory = getattr(rt, "memory", None) if rt is not None else None
        for c in (getattr(memory, "changed", None) or []):
            when = dt_util.parse_datetime(c.get("time") or "")
            if c.get("kind") == "replaced" and when and when.timestamp() >= since and f"mem:{c['time']}" not in self.shared:
                fresh.append({"key": f"mem:{c['time']}", "t": when.timestamp(), "text": f"I've updated what I know: {c['new'].rstrip('.')}."})
        learner = getattr(rt, "learner", None) if rt is not None else None
        for r in (learner.all() if learner is not None else []):
            when = dt_util.parse_datetime(getattr(r, "learned", "") or "")
            if when and when.timestamp() >= since and f"habit:{r.id}" not in self.shared and not getattr(r, "declined", False):
                fresh.append({"key": f"habit:{r.id}", "t": when.timestamp(), "text": f"I've spotted a habit: {r.describe().rstrip('.')}."})
        p = self.prediction()
        if p["trusted"] and "guess:trusted" not in self.shared:
            fresh.append({"key": "guess:trusted", "t": dt_util.utcnow().timestamp(),
                          "text": f"I can now usually tell which room someone's heading to next — right {round(p['rate'] * 100)}% of the time."})
        return max(fresh, key=lambda t: t["t"]) if fresh else None

    async def async_share(self, pick: dict[str, Any], local: datetime) -> bool:
        rt = self._rt()
        if rt is None:
            return False
        try:
            result = await rt.speech.async_say(f"Something I've learned: {pick['text']}", "normal", source="learning")
        except Exception as err:  # noqa: BLE001 - no speaker answered: try again later this evening
            _LOGGER.debug("Couldn't say what it learned: %s", err)
            return False
        if not getattr(result, "spoken", False):
            return False  # quiet time, nobody about or hushed: try again later this evening
        self.shared_day = local.date().isoformat()
        self.shared = (self.shared + [pick["key"]])[-200:]
        self._save()
        return True

    def check_day(self, local: datetime) -> bool:
        """Nobody up yet when someone usually is by now: noted once a day, nothing said or switched."""
        rt = self._rt()
        if rt is None or getattr(rt, "sleep", None) is None or not rt.sleep.enabled or not rt.sleep.asleep:
            return False
        wake = rt.heating.wake_minute(local) if getattr(rt, "heating", None) is not None else None
        if wake is None:
            return False
        minute = local.hour * 60 + local.minute
        if not wake + LATE_AFTER <= minute <= wake + LATE_UNTIL:
            return False
        away = getattr(rt, "away", None)
        if away is not None and away.everyone_away():
            return False
        if getattr(getattr(rt, "holiday", None), "active", False):
            return False
        usual = f"{wake // 60:02d}:{wake % 60:02d}"
        text = f"Nobody's up yet, and someone usually is by {usual}. I'm leaving everyone be, but noting it."
        if self._think(f"late:{local.date().isoformat()}", text, f"usually up by {usual}; still asleep at {local:%H:%M}", "day"):
            rt.timeline.add("mind", "Later than usual: nobody's up yet", f"usually up by {usual}")
        return True

    # ----- thoughts ----------------------------------------------------------------------
    def _think(self, key: str, text: str, why: str, kind: str) -> bool:
        """Add a thought unless it (or the same one) was had in the last week or dismissed. True if new."""
        now = dt_util.utcnow().timestamp()
        if key in self.dismissed or any(t["key"] == key and now - t["t"] < THOUGHT_DAYS * 86400 for t in self.thoughts):
            return False
        self.thoughts.append({"key": key, "text": text, "why": why, "kind": kind, "t": now})
        self.thoughts = self.thoughts[-THOUGHTS_KEPT:]
        self._save()
        return True

    def dismiss(self, key: str) -> bool:
        if not any(t["key"] == key for t in self.thoughts):
            return False
        self.thoughts = [t for t in self.thoughts if t["key"] != key]
        if key not in self.dismissed:
            self.dismissed.append(key)
        self._save()
        return True

    # ----- reporting ---------------------------------------------------------------------
    def noisy(self, days: int = 7, at_least: int = 12) -> list[dict[str, Any]]:
        """Things it's watching that it keeps deciding aren't worth mentioning (each look is an AI call)."""
        rt = self._rt()
        timeline = getattr(rt, "timeline", None) if rt is not None else None
        if timeline is None:
            return []
        watched = set(self.options.get("notice_entities") or [])
        counts: dict[str, int] = {}
        for item in timeline.since(days * 24, ["noticed"], 1000):
            if "wasn't worth saying" in str(item.get("detail") or ""):
                for eid in item.get("entities") or []:
                    if eid in watched:
                        counts[eid] = counts.get(eid, 0) + 1
        out = []
        for eid, n in sorted(counts.items(), key=lambda kv: -kv[1]):
            if n >= at_least:
                st = self.hass.states.get(eid)
                out.append({"entity_id": eid, "name": st.name if st else eid, "times": n})
        return out[:4]

    def prediction(self) -> dict[str, Any]:
        tried = len(self.scores)
        right = sum(self.scores)
        rate = round(right / tried, 2) if tried else None
        return {"tried": tried, "right": right, "rate": rate, "trusted": bool(rate is not None and tried >= TRUST_MIN_TRIES and rate >= TRUST_AT),
                "next": self._pred[0] if self._pred else None}

    def summary(self) -> dict[str, Any]:
        out: dict[str, Any] = {"learn": self.learn_on, "day": self.day_on, "thoughts_on": self.thoughts_on}
        if not (self.learn_on or self.day_on or self.thoughts_on):
            return out
        since = dt_util.utcnow().timestamp() - THOUGHT_DAYS * 86400
        shown = [t for t in self.thoughts if t["t"] >= since and (
            (t["kind"] == "lesson" and self.learn_on) or (t["kind"] == "day" and self.day_on) or (t["kind"] == "guess" and self.thoughts_on))]
        live: list[dict[str, Any]] = []
        if self.thoughts_on:
            p = self.prediction()
            if p["tried"] >= 5:
                pct = round(p["rate"] * 100)
                live.append({"key": "guess", "kind": "guess", "t": dt_util.utcnow().timestamp(),
                             "text": (f"I guessed which room someone goes to next and was right {pct}% of the time ({p['right']} of {p['tried']})"
                                      + (". That's good enough to rely on." if p["trusted"] else ". Still learning, so I'm not acting on it yet.")),
                             "why": "checked after every journey between rooms"})
        rt = self._rt()
        memory = getattr(rt, "memory", None) if rt is not None else None
        if self.thoughts_on and memory is not None:
            for c in reversed(getattr(memory, "changed", [])[-3:]):
                if c.get("kind") == "replaced":
                    live.append({"key": "mem:" + c["time"], "kind": "memory", "t": dt_util.parse_datetime(c["time"]).timestamp(),
                                 "text": f"I've updated what I know: \"{c['new']}\"", "why": f"it replaces \"{c['old']}\""})
        since14 = dt_util.utcnow().timestamp() - LOOKBACK_DAYS * 86400
        return {**out, "noisy": self.noisy() if self.thoughts_on else [],
                "thoughts": sorted(shown, key=lambda t: t["t"], reverse=True)[:10] + live,
                "corrections": sum(1 for c in self.corrections if c["t"] >= since14) if self.learn_on else None,
                "prediction": self.prediction() if self.thoughts_on else None}
