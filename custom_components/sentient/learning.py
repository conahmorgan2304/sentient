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
* Links: things people do straight after something else happens ("when the
  back door opens after dark, the kitchen light goes on within a minute").
  Same rules — people only, plain statistics, offered once, done on request.
"""

from __future__ import annotations

from dataclasses import asdict, dataclass, field
from datetime import datetime, time, timedelta
import hashlib
import logging
from statistics import median
from typing import TYPE_CHECKING, Any, Callable

from homeassistant.const import EVENT_STATE_CHANGED
from homeassistant.core import Event, EventStateChangedData, HomeAssistant, State, callback
from homeassistant.helpers.event import async_track_time_change
from homeassistant.helpers.sun import get_astral_event_date
from homeassistant.helpers.storage import Store
from homeassistant.util import dt as dt_util

from .const import (
    CONF_LEARN_ENTITIES,
    CONF_LINK_TRIGGERS,
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
LINK_LAG = 120  # seconds: the follow-up has to come this soon after
LINK_MIN_SEEN = 4
LINK_MIN_CONF = 0.7
MAX_LINKS = 12
MAX_CAUSES = 40
OPEN_CLASSES = {"door", "window", "garage_door", "opening"}
_CAUSE_STATES = {
    "binary_sensor": ("on", "off"),
    "light": ("on", "off"),
    "switch": ("on", "off"),
    "fan": ("on", "off"),
    "input_boolean": ("on", "off"),
    "cover": ("open", "closed"),
    "media_player": ("playing", "off"),
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


def _verb(entity_id: str, state: str, device_class: str | None = None) -> str:
    domain = entity_id.split(".", 1)[0]
    if domain == "binary_sensor":
        if device_class in OPEN_CLASSES or device_class is None:
            return "opens" if state == "on" else "closes"
        return "turns on" if state == "on" else "turns off"
    if domain == "media_player":
        return "starts playing" if state == "playing" else "is turned off"
    return {"on": "goes on", "off": "goes off", "open": "opens", "closed": "closes"}.get(state, f"changes to {state}")


@dataclass
class Link:
    """A learned habit of doing one thing straight after another."""

    cause: str
    cause_name: str
    cause_state: str
    entity_id: str  # what people then change
    name: str
    state: str
    dark: bool
    lag: int  # median seconds between the two
    support: float
    seen: int
    cause_class: str | None = None
    id: str = ""
    learned: str = field(default_factory=lambda: dt_util.utcnow().isoformat())
    suggested: str | None = None
    adopted: bool = False
    declined: bool = False

    def __post_init__(self) -> None:
        if not self.id:
            key = f"link|{self.cause}|{self.cause_state}|{self.entity_id}|{self.state}|{self.dark}"
            self.id = hashlib.sha1(key.encode()).hexdigest()[:6]

    def describe(self) -> str:
        soon = "straight after" if self.lag <= 20 else "within a minute" if self.lag <= 60 else "within a couple of minutes"
        return (
            f"When the {self.cause_name} {_verb(self.cause, self.cause_state, self.cause_class)}"
            f"{' after dark' if self.dark else ''}, the {self.name} usually "
            f"{_verb(self.entity_id, self.state)} {soon}."
        )

    def as_dict(self) -> dict[str, Any]:
        return asdict(self)

    def summary(self) -> dict[str, Any]:
        return {
            "id": self.id,
            "routine": self.describe(),
            "how_sure": f"{round(self.support * 100)}% of the time ({self.seen} times)",
            "adopted": self.adopted,
            "kind": "link",
        }


def _state_at(states: list[State], when: datetime) -> str | None:
    found = None
    for st in states:
        if st.last_changed > when:
            break
        if st.state not in ("unavailable", "unknown"):
            found = st.state
    return found


def find_links(
    causes: dict[str, tuple[str, str | None, list[State]]],
    effects: dict[str, tuple[str, list[State]]],
    is_dark: Callable[[datetime], bool],
    automated: dict[str, set[int]] | None = None,
    today: datetime | None = None,
) -> list[Link]:
    """Pure statistics: which things people do straight after which others.

    causes: entity_id -> (name, device_class, history); effects: entity_id -> (name, history).
    """
    automated = automated or {}
    now = today or dt_util.now()
    start = now - timedelta(days=WINDOW_DAYS)
    best: dict[tuple[str, str], Link] = {}
    for effect, (ename, estates) in effects.items():
        edomain = effect.split(".", 1)[0]
        if edomain not in _SUPPORTED:
            continue
        for es in _SUPPORTED[edomain]:
            done = [t for t in _changes_to(estates, es, automated.get(effect)) if t >= start]
            if len(done) < LINK_MIN_SEEN:
                continue
            for cause, (cname, cclass, cstates) in causes.items():
                if cause == effect:
                    continue
                for cs in _CAUSE_STATES.get(cause.split(".", 1)[0], ()):
                    happened = [t for t in _changes_to(cstates, cs) if t >= start]
                    # only the times it could have been done: it wasn't already that way
                    eligible = [t for t in happened if _state_at(estates, t) not in (es, None)]
                    if len(eligible) < LINK_MIN_SEEN:
                        continue
                    lags: dict[datetime, float] = {}
                    for t in eligible:
                        after = [(d - t).total_seconds() for d in done if 0 < (d - t).total_seconds() <= LINK_LAG]
                        if after:
                            lags[t] = min(after)
                    for dark in (False, True):
                        pool = [t for t in eligible if is_dark(t)] if dark else eligible
                        hits = [lags[t] for t in pool if t in lags]
                        if len(hits) < LINK_MIN_SEEN or not pool:
                            continue
                        conf = len(hits) / len(pool)
                        if conf < LINK_MIN_CONF:
                            continue
                        link = Link(cause, cname, cs, effect, ename, es, dark, round(median(hits)), round(conf, 2), len(hits), cclass)
                        old = best.get((effect, es))
                        # 'after dark' only wins if it's clearly more reliable
                        margin = 0.15 if dark and old is not None and old.cause == cause and not old.dark else 0.0
                        if old is None or (conf, len(hits)) > (old.support + margin, old.seen):
                            best[(effect, es)] = link
    ranked = sorted(best.values(), key=lambda l: (l.support, l.seen), reverse=True)
    return ranked[:MAX_LINKS]


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
        self.links: dict[str, Link] = {}
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
        for item in data.get("links", []):
            try:
                link = Link(**item)
            except TypeError:
                continue
            self.links[link.id] = link
        self.last_run = data.get("last_run")
        self._asked_today = data.get("asked_day")
        self._unsubs.append(async_track_time_change(self.hass, self._nightly, hour=3, minute=30, second=0))
        self._unsubs.append(async_track_time_change(self.hass, self._every_minute, second=0))
        self._unsubs.append(self.hass.bus.async_listen(EVENT_STATE_CHANGED, self._on_state_changed))
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
                "links": [l.as_dict() for l in self.links.values()],
                "last_run": self.last_run,
                "asked_day": self._asked_today,
            },
            2,
        )
        self.hass.bus.async_fire(EVENT_ROUTINES_CHANGED, {"count": len(self.routines) + len(self.links)})

    def all(self) -> list[Routine | Link]:
        """Learned routines and links together."""
        return [*self.routines.values(), *self.links.values()]

    def get(self, item_id: str) -> Routine | Link | None:
        return self.routines.get(item_id) or self.links.get(item_id)

    def is_dark(self, when: datetime) -> bool:
        local = dt_util.as_local(when)
        try:
            rise = get_astral_event_date(self.hass, "sunrise", local.date())
            down = get_astral_event_date(self.hass, "sunset", local.date())
        except Exception:  # noqa: BLE001 - no location: fall back to the clock
            rise = down = None
        if rise is None or down is None:
            return local.hour >= 19 or local.hour < 7
        return when < rise or when >= down

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
        try:
            await self._async_learn_links(entity_ids, history, automated)
        except Exception:  # noqa: BLE001 - learning is best effort
            _LOGGER.warning("Sentient could not learn links", exc_info=True)
        self.last_run = dt_util.utcnow().isoformat()
        self._save()
        return list(self.routines.values())

    def _cause_candidates(self, entity_ids: list[str]) -> list[str]:
        doors = [
            st.entity_id for st in self.hass.states.async_all("binary_sensor")
            if st.attributes.get("device_class") in OPEN_CLASSES
        ]
        extra = list(self.options.get(CONF_LINK_TRIGGERS) or [])
        out: list[str] = []
        for e in [*extra, *doors, *entity_ids]:
            if e not in out and e.split(".", 1)[0] in _CAUSE_STATES:
                out.append(e)
        return out[:MAX_CAUSES]

    async def _async_learn_links(
        self, effect_ids: list[str], history: dict[str, list[State]], automated: dict[str, set[int]]
    ) -> None:
        causes = self._cause_candidates(effect_ids)
        missing = [c for c in causes if c not in history]
        if missing:
            history = {**history, **await self._async_history(missing)}

        def name(entity_id: str) -> str:
            st = self.hass.states.get(entity_id)
            return (st.name if st else entity_id).lower()

        def dclass(entity_id: str) -> str | None:
            st = self.hass.states.get(entity_id)
            return st.attributes.get("device_class") if st else None

        found = find_links(
            {c: (name(c), dclass(c), history.get(c, [])) for c in causes},
            {e: (name(e), history.get(e, [])) for e in effect_ids},
            self.is_dark,
            automated,
        )
        fresh: dict[str, Link] = {}
        for link in found:
            if old := self.links.get(link.id):
                link.adopted, link.declined, link.suggested, link.learned = old.adopted, old.declined, old.suggested, old.learned
            fresh[link.id] = link
        for lid, old in self.links.items():
            if old.adopted and lid not in fresh:
                fresh[lid] = old
        self.links = fresh
        self._save()

    # ----- doing adopted links ----------------------------------------------------------
    @callback
    def _on_state_changed(self, event: Event[EventStateChangedData]) -> None:
        new, old = event.data["new_state"], event.data["old_state"]
        if new is None or old is None or old.state in ("unavailable", "unknown") or old.state == new.state:
            return
        for link in self.links.values():
            if not link.adopted or link.cause != new.entity_id or link.cause_state != new.state:
                continue
            if link.dark and not self.is_dark(new.last_changed):
                continue
            if self.skip and link.entity_id in self.skip():
                continue
            self.hass.async_create_task(self._async_do(link))

    # ----- deciding --------------------------------------------------------------------
    def adopt(self, id_or_words: str, adopted: bool = True) -> Routine | Link | None:
        routine = self._find(id_or_words)
        if routine is not None:
            routine.adopted = adopted
            routine.declined = not adopted
            self._save()
        return routine

    def _find(self, id_or_words: str) -> Routine | Link | None:
        if item := self.get(id_or_words):
            return item
        words = {w for w in id_or_words.lower().replace(".", " ").split() if len(w) > 2}
        best, score = None, 0
        for r in self.all():
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

    async def _async_do(self, routine: Routine | Link) -> None:
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

    def next_to_offer(self) -> Routine | Link | None:
        options = [r for r in self.all() if not (r.adopted or r.declined or r.suggested)]
        return max(options, key=lambda r: (r.support, r.seen), default=None)

    async def _async_offer(self, local: datetime) -> None:
        routine = self.next_to_offer()
        if routine is None or self.speech.guest_mode or self.speech.hushed():
            return
        if self.tracker is not None and self.tracker.best_area() is None:
            return  # nobody obviously about; try again later
        if isinstance(routine, Link):
            said = routine.describe()
            question = f"I've noticed that {said[0].lower()}{said[1:-1]}. Want me to do that for you from now on?"
        else:
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
