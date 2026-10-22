"""Learning: spot the household's routines from history — no AI needed.

Once a night Sentient reads the last couple of weeks of history for the
entities the user chose and looks for things that happen at about the same
time most days ("the hallway light goes on around 5:30pm on weekdays").

* Plain statistics: clusters of the same change within a time window,
  separately for every day / weekdays / weekends.
* Only changes people made count: the logbook says when an automation or
  script caused a change, and those are left out. As a fallback, changes that
  happen to the minute every day are treated as automated too.
* Learned routines are shared with every voice agent, and — once each, during
  the day, when someone is home — offered: "want me to do that for you?".
  Adopted routines are then carried out at their usual time.
"""

from __future__ import annotations

from dataclasses import asdict, dataclass, field
from datetime import datetime, time, timedelta
import hashlib
import logging
from statistics import median
from typing import TYPE_CHECKING, Any

from homeassistant.core import HomeAssistant, State, callback
from homeassistant.helpers.event import async_track_time_change
from homeassistant.helpers.storage import Store
from homeassistant.util import dt as dt_util

from .const import (
    CONF_LEARN_ENTITIES,
    CONF_SUGGEST_ROUTINES,
    DEFAULT_SUGGEST_ROUTINES,
    DOMAIN,
    EVENT_ROUTINES_CHANGED,
)

if TYPE_CHECKING:
    from .presence import ActivityTracker
    from .speech import SpeechRouter

_LOGGER = logging.getLogger(__name__)

STORAGE_VERSION = 1
WINDOW_DAYS = 14
TOLERANCE_MIN = 25  # how close two days' changes must be to count as "the same time"
MIN_SUPPORT = 0.6  # share of eligible days the change must happen on
MIN_DAYS = 3
AUTOMATED_SPREAD_MIN = 1.0  # changes this precise are an automation already
MAX_ROUTINES = 20
SUGGEST_FROM, SUGGEST_UNTIL = time(10, 0), time(20, 0)

_SUPPORTED = {
    "light": ("on", "off"),
    "switch": ("on", "off"),
    "fan": ("on", "off"),
    "input_boolean": ("on", "off"),
    "cover": ("open", "closed"),
}
_DAYTYPES = {
    "every day": lambda d: True,
    "on weekdays": lambda d: d.weekday() < 5,
    "at weekends": lambda d: d.weekday() >= 5,
}


@dataclass
class Routine:
    """A learned habit."""

    entity_id: str
    name: str
    state: str
    minute: int  # minutes after local midnight
    days: str  # key of _DAYTYPES
    support: float
    seen: int
    spread: float
    id: str = ""
    learned: str = field(default_factory=lambda: dt_util.utcnow().isoformat())
    suggested: str | None = None
    adopted: bool = False
    declined: bool = False

    def __post_init__(self) -> None:
        if not self.id:
            self.id = hashlib.sha1(f"{self.entity_id}|{self.state}|{self.days}".encode()).hexdigest()[:6]

    @property
    def when(self) -> str:
        hour, minute = divmod(self.minute, 60)
        h12 = hour % 12 or 12
        return f"{h12}{'' if minute == 0 else f':{minute:02d}'}{'am' if hour < 12 else 'pm'}"

    def describe(self) -> str:
        verb = {"on": "goes on", "off": "goes off", "open": "opens", "closed": "closes"}.get(self.state, self.state)
        return f"The {self.name} usually {verb} around {self.when} {self.days}."

    def as_dict(self) -> dict[str, Any]:
        return asdict(self)

    def summary(self) -> dict[str, Any]:
        return {
            "id": self.id,
            "routine": self.describe(),
            "how_sure": f"{round(self.support * 100)}% of days",
            "adopted": self.adopted,
        }


def _changes_to(states: list[State], target: str, automated: set[int] | None = None) -> list[datetime]:
    """Local times an entity changed *to* target from some other real state, by a person."""
    out: list[datetime] = []
    previous: str | None = None
    for st in states:
        if st.state in ("unavailable", "unknown"):
            continue
        if st.state == target and previous is not None and previous != target:
            stamp = round(st.last_changed.timestamp())
            if not automated or not ({stamp - 1, stamp, stamp + 1} & automated):
                out.append(dt_util.as_local(st.last_changed))
        previous = st.state
    return out


def find_routines(
    entity_id: str,
    name: str,
    states: list[State],
    today: datetime | None = None,
    automated: set[int] | None = None,
) -> list[Routine]:
    """Pure statistics over one entity's history."""
    domain = entity_id.split(".", 1)[0]
    if domain not in _SUPPORTED:
        return []
    today = (today or dt_util.now()).date()
    window = [today - timedelta(days=i) for i in range(1, WINDOW_DAYS + 1)]
    found: list[Routine] = []
    for target in _SUPPORTED[domain]:
        events = [e for e in _changes_to(states, target, automated) if e.date() in window]
        by_day: dict[Any, list[int]] = {}
        for e in events:
            by_day.setdefault(e.date(), []).append(e.hour * 60 + e.minute)
        best: Routine | None = None
        for label, keep in _DAYTYPES.items():
            days = [d for d in window if keep(d)]
            if not days:
                continue
            candidates = sorted({m for d in days for m in by_day.get(d, [])})
            for centre in candidates:
                hits = []
                for d in days:
                    near = [m for m in by_day.get(d, []) if abs(m - centre) <= TOLERANCE_MIN]
                    if near:
                        hits.append(min(near, key=lambda m: abs(m - centre)))
                if len(hits) < MIN_DAYS:
                    continue
                support = len(hits) / len(days)
                if support < MIN_SUPPORT:
                    continue
                mid = round(median(hits))
                spread = median(abs(m - mid) for m in hits)
                routine = Routine(entity_id, name, target, mid, label, round(support, 2), len(hits), spread)
                # prefer 'every day' unless a narrower day type is clearly stronger
                if best is None or (support > best.support + 0.1) or (
                    abs(support - best.support) <= 0.1 and len(hits) > best.seen
                ):
                    best = routine
        if best is None:
            continue
        if best.spread < AUTOMATED_SPREAD_MIN and best.support >= 0.9:
            continue  # to the minute every day: an automation already does this
        found.append(best)
    return found


class Learner:
    """Owns learned routines: nightly analysis, sharing, offering, doing."""

    def __init__(
        self,
        hass: HomeAssistant,
        entry_id: str,
        options: dict[str, Any],
        speech: SpeechRouter,
        tracker: ActivityTracker | None,
    ) -> None:
        self.hass = hass
        self.options = options
        self.speech = speech
        self.tracker = tracker
        self._store: Store[dict[str, Any]] = Store(hass, STORAGE_VERSION, f"{DOMAIN}.{entry_id}.routines")
        self.routines: dict[str, Routine] = {}
        self.last_run: str | None = None
        self._unsubs: list = []
        self._asked_today: str | None = None
        self.skip = None  # callable giving entities another feature already runs

    # ----- lifecycle --------------------------------------------------------------
    async def async_start(self) -> None:
        data = await self._store.async_load() or {}
        for item in data.get("routines", []):
            try:
                routine = Routine(**item)
            except TypeError:
                continue
            self.routines[routine.id] = routine
        self.last_run = data.get("last_run")
        self._asked_today = data.get("asked_day")
        self._unsubs.append(async_track_time_change(self.hass, self._nightly, hour=3, minute=30, second=0))
        self._unsubs.append(async_track_time_change(self.hass, self._every_minute, second=0))
        last = dt_util.parse_datetime(self.last_run) if self.last_run else None
        if last is None or dt_util.utcnow() - last > timedelta(hours=26):
            self.hass.async_create_background_task(self.async_learn(), "sentient_learn")

    @callback
    def async_stop(self) -> None:
        while self._unsubs:
            self._unsubs.pop()()

    @callback
    def _save(self) -> None:
        self._store.async_delay_save(
            lambda: {
                "routines": [r.as_dict() for r in self.routines.values()],
                "last_run": self.last_run,
                "asked_day": self._asked_today,
            },
            2,
        )
        self.hass.bus.async_fire(EVENT_ROUTINES_CHANGED, {"count": len(self.routines)})

    @property
    def entities(self) -> list[str]:
        """Lights and switches to learn from — not ones motion lights already run."""
        skip = self.skip() if self.skip else set()
        return [e for e in self.options.get(CONF_LEARN_ENTITIES) or [] if e not in skip]

    # ----- learning ----------------------------------------------------------------
    @callback
    def _nightly(self, _now: datetime) -> None:
        self.hass.async_create_background_task(self.async_learn(), "sentient_learn")

    async def _async_history(self, entity_ids: list[str]) -> dict[str, list[State]]:
        if "recorder" not in self.hass.config.components or not entity_ids:
            return {}
        from homeassistant.components.recorder import get_instance, history

        start = dt_util.utcnow() - timedelta(days=WINDOW_DAYS + 1)
        return await get_instance(self.hass).async_add_executor_job(
            lambda: history.get_significant_states(
                self.hass, start, None, entity_ids, significant_changes_only=True, minimal_response=False
            )
        )

    async def _async_automated(self, entity_ids: list[str]) -> dict[str, set[int]]:
        """Times (epoch seconds) each entity was changed by an automation or script."""
        if "logbook" not in self.hass.config.components or not entity_ids:
            return {}
        from homeassistant.components.logbook.helpers import async_determine_event_types
        from homeassistant.components.logbook.processor import EventProcessor
        from homeassistant.components.recorder import get_instance

        end = dt_util.utcnow()
        start = end - timedelta(days=WINDOW_DAYS + 1)
        processor = EventProcessor(
            self.hass, async_determine_event_types(self.hass, entity_ids, None), entity_ids,
            None, None, timestamp=True, include_entity_name=False,
        )
        rows = await get_instance(self.hass).async_add_executor_job(processor.get_events, start, end)
        out: dict[str, set[int]] = {}
        for row in rows:
            if row.get("context_domain") in ("automation", "script") or row.get("context_event_type") in (
                "automation_triggered", "script_started"
            ):
                if (entity_id := row.get("entity_id")) and isinstance(row.get("when"), (int, float)):
                    out.setdefault(entity_id, set()).add(round(row["when"]))
        return out

    async def async_learn(self) -> list[Routine]:
        """Re-learn routines, keeping the user's decisions about ones seen before."""
        entity_ids = [e for e in self.entities if e.split(".", 1)[0] in _SUPPORTED]
        try:
            history = await self._async_history(entity_ids)
        except Exception:  # noqa: BLE001 - learning is best effort
            _LOGGER.warning("Sentient could not read history to learn routines", exc_info=True)
            return list(self.routines.values())
        try:
            automated = await self._async_automated(entity_ids)
        except Exception:  # noqa: BLE001 - fall back to the to-the-minute rule
            _LOGGER.debug("Could not read the logbook; using timing alone", exc_info=True)
            automated = {}
        fresh: dict[str, Routine] = {}
        for entity_id in entity_ids:
            state = self.hass.states.get(entity_id)
            name = (state.name if state else entity_id).lower()
            for routine in find_routines(
                entity_id, name, history.get(entity_id, []), automated=automated.get(entity_id)
            ):
                old = self.routines.get(routine.id)
                if old is not None:
                    routine.adopted, routine.declined, routine.suggested = old.adopted, old.declined, old.suggested
                    routine.learned = old.learned
                fresh[routine.id] = routine
        # adopted routines are kept even if a quiet fortnight made them fade
        for rid, old in self.routines.items():
            if old.adopted and rid not in fresh:
                fresh[rid] = old
        ranked = sorted(fresh.values(), key=lambda r: (r.adopted, r.support, r.seen), reverse=True)
        self.routines = {r.id: r for r in ranked[:MAX_ROUTINES]}
        self.last_run = dt_util.utcnow().isoformat()
        self._save()
        return list(self.routines.values())

    # ----- deciding --------------------------------------------------------------------
    def adopt(self, id_or_words: str, adopted: bool = True) -> Routine | None:
        routine = self._find(id_or_words)
        if routine is not None:
            routine.adopted = adopted
            routine.declined = not adopted
            self._save()
        return routine

    def _find(self, id_or_words: str) -> Routine | None:
        if id_or_words in self.routines:
            return self.routines[id_or_words]
        words = {w for w in id_or_words.lower().replace(".", " ").split() if len(w) > 2}
        best, score = None, 0
        for r in self.routines.values():
            hay = set(f"{r.describe()} {r.entity_id}".lower().replace(".", " ").replace("_", " ").split())
            s = len(words & hay)
            if s > score:
                best, score = r, s
        return best

    # ----- every minute: do adopted routines, maybe offer a new one --------------------
    @callback
    def _every_minute(self, now: datetime) -> None:
        local = dt_util.as_local(now)
        minute = local.hour * 60 + local.minute
        for routine in self.routines.values():
            if routine.adopted and routine.minute == minute and _DAYTYPES[routine.days](local.date()):
                self.hass.async_create_task(self._async_do(routine))
        if (
            self.options.get(CONF_SUGGEST_ROUTINES, DEFAULT_SUGGEST_ROUTINES)
            and local.minute % 15 == 0
            and SUGGEST_FROM <= local.time() < SUGGEST_UNTIL
            and self._asked_today != local.date().isoformat()
        ):
            self.hass.async_create_task(self._async_offer(local))

    async def _async_do(self, routine: Routine) -> None:
        state = self.hass.states.get(routine.entity_id)
        if state is None or state.state == routine.state:
            return
        domain = routine.entity_id.split(".", 1)[0]
        if domain == "cover":
            service = "open_cover" if routine.state == "open" else "close_cover"
        else:
            service = "turn_on" if routine.state == "on" else "turn_off"
        _LOGGER.debug("Doing adopted routine: %s", routine.describe())
        await self.hass.services.async_call(domain, service, target={"entity_id": routine.entity_id}, blocking=True)

    def next_to_offer(self) -> Routine | None:
        options = [r for r in self.routines.values() if not (r.adopted or r.declined or r.suggested)]
        return max(options, key=lambda r: (r.support, r.seen), default=None)

    async def _async_offer(self, local: datetime) -> None:
        routine = self.next_to_offer()
        if routine is None or self.speech.guest_mode or self.speech.hushed():
            return
        if self.tracker is not None and self.tracker.best_area() is None:
            return  # nobody obviously about; try again later
        question = (
            f"I've noticed the {routine.name} usually "
            f"{'goes ' + routine.state if routine.state in ('on', 'off') else ('opens' if routine.state == 'open' else 'closes')}"
            f" around {routine.when} {routine.days}. Want me to do that for you from now on?"
        )
        result = await self.speech.async_ask(
            question,
            source="learning",
            extra_prompt=(
                f"If they say yes, call sentient_adopt_routine with routine '{routine.id}'. "
                f"If they say no, call sentient_adopt_routine with routine '{routine.id}' and adopt false."
            ),
        )
        if result.spoken:
            routine.suggested = dt_util.utcnow().isoformat()
            self._asked_today = local.date().isoformat()
            self._save()
