"""Heating that learns — plain statistics, no AI.

Every night, from the last two weeks of history:

* how fast each heating zone warms up (degrees an hour, from the times its target
  was well above the room and it climbed to it);
* the temperature people like (the target it's usually set to during the day);
* when the house usually gets up — the first movement inside, weekdays and
  weekends separately.

From that it can say what the heating will be like when people get up, and —
only if preheat is switched on — start warming early so it's comfortable by
then. A thermostat with its own schedule is put back on that schedule
afterwards, and a zone someone has switched off is never switched on.
"""

from __future__ import annotations

from copy import deepcopy
from datetime import datetime, time, timedelta
import logging
from statistics import median
from typing import TYPE_CHECKING, Any

from homeassistant.const import EVENT_CALL_SERVICE, EVENT_STATE_CHANGED
from homeassistant.core import Event, HomeAssistant, State, callback
from homeassistant.helpers.event import async_track_time_change
from homeassistant.helpers.storage import Store
from homeassistant.util import dt as dt_util

from .own import is_own, own_context
from .const import (
    CONF_COMFORT_TEMP,
    CONF_HEATING_ENTITIES,
    CONF_HEATING_LIMIT,
    DEFAULT_HEATING_LIMIT,
    CONF_LEARN_ENTITIES,
    CONF_PREHEAT,
    CONF_WARM_BY_WEEKDAYS,
    CONF_WARM_BY_WEEKENDS,
    DOMAIN,
)
from .presence import inside_sensors

if TYPE_CHECKING:
    from .timeline import Timeline

_LOGGER = logging.getLogger(__name__)

STORAGE_VERSION = 1
WINDOW_DAYS = 14
MIN_GAP = 0.8  # target this far above the room counts as "warming up"
MIN_RISE = 0.5
MIN_EPISODE = timedelta(minutes=20)
MAX_EPISODE = timedelta(hours=6)
WAKE_FROM, WAKE_UNTIL = time(4, 30), time(11, 0)
LOOKAHEAD = timedelta(hours=4)
RESTORE_AFTER = timedelta(minutes=45)
CONFIRM_WITHIN = timedelta(minutes=10)
RESTORE_GRACE = timedelta(hours=1)


def _num(value: Any) -> float | None:
    from .numeric import finite_float

    return finite_float(value)


def warmup_rate(states: list[State]) -> tuple[float | None, int]:
    """Median degrees an hour while warming up to a target well above the room."""
    rates: list[float] = []
    start: tuple[datetime, float, float] | None = None
    for st in states:
        cur = _num(st.attributes.get("current_temperature"))
        target = _num(st.attributes.get("temperature"))
        when = st.last_updated
        if st.state in ("off", "unavailable", "unknown") or cur is None or target is None:
            start = None
            continue
        if start is None:
            if target - cur >= MIN_GAP:
                start = (when, cur, target)
            continue
        began, cur0, target0 = start
        if abs(target - target0) > 0.3 or when - began > MAX_EPISODE:
            start = (when, cur, target) if target - cur >= MIN_GAP else None
            continue
        if cur >= target0 - 0.2:
            hours = (when - began).total_seconds() / 3600
            if when - began >= MIN_EPISODE and cur - cur0 >= MIN_RISE:
                rates.append((cur - cur0) / hours)
            start = None
    if not rates:
        return None, 0
    return round(median(rates), 1), len(rates)


def comfort_target(states: list[State]) -> float | None:
    """The daytime target it spends longest at."""
    held: dict[float, float] = {}
    for st, nxt in zip(states, states[1:] + [None]):
        target = _num(st.attributes.get("temperature"))
        if target is None or st.state in ("off", "unknown", "unavailable"):
            continue
        local = dt_util.as_local(st.last_updated)
        if not 7 <= local.hour < 22:
            continue
        end = nxt.last_updated if nxt is not None else dt_util.utcnow()
        duration = (end - st.last_updated).total_seconds()
        if duration > 0:
            held[round(target * 2) / 2] = held.get(round(target * 2) / 2, 0) + duration
    return max(held, key=held.get) if held else None


UP_EVENTS = 3  # movements...
UP_ROOMS = 2  # ...in at least this many sensors...
UP_SPAN = timedelta(minutes=15)  # ...within this long: people up, not a cat or someone turning over


REJOIN = timedelta(seconds=15)  # "on" again this soon after "off" is the same movement (flapping sensors)


def usual_wake(
    sensors: dict[str, list[State]], switched: dict[str, list[datetime]] | None = None
) -> dict[str, int | None]:
    """Median time the house gets going in the morning, weekdays and weekends.

    Best evidence: the first light or switch someone turned on by hand (pets can't). On days
    without one, the first moment there are several movements in more than one place within a
    quarter of an hour — a lone blip in the night (a pet, someone turning over) doesn't count.
    """
    by_day: dict[Any, list[tuple[datetime, str]]] = {}
    for entity_id, states in sensors.items():
        last_off: datetime | None = None
        for st in states:
            if st.state == "off":
                last_off = st.last_changed
                continue
            if st.state != "on":
                continue
            if last_off is not None and st.last_changed - last_off < REJOIN:
                continue  # a flap, not a new movement
            local = dt_util.as_local(st.last_changed)
            if WAKE_FROM <= local.time() < WAKE_UNTIL:
                by_day.setdefault(local.date(), []).append((local, entity_id))
    first: dict[Any, int] = {}
    for times in (switched or {}).values():
        for t in times:
            local = dt_util.as_local(t)
            if WAKE_FROM <= local.time() < WAKE_UNTIL:
                minute = local.hour * 60 + local.minute
                if minute < first.get(local.date(), 1440):
                    first[local.date()] = minute
    for day, events in by_day.items():
        if day in first:
            continue
        events.sort()
        for i, (t, _e) in enumerate(events):
            burst = [e for (u, e) in events[i:] if u - t <= UP_SPAN]
            if len(burst) >= UP_EVENTS and len(set(burst)) >= UP_ROOMS:
                first[day] = t.hour * 60 + t.minute
                break
    out: dict[str, int | None] = {}
    for label, keep in (("weekdays", lambda d: d.weekday() < 5), ("weekends", lambda d: d.weekday() >= 5)):
        mins = [m for d, m in first.items() if keep(d)]
        out[label] = round(median(mins)) if len(mins) >= 3 else None
    return out


def on_share(states: list[State], end: datetime) -> float:
    """How much of the time a sensor was on across its history (0 to 1)."""
    on = total = 0.0
    for st, nxt in zip(states, [*states[1:], None]):
        until = nxt.last_changed if nxt is not None else end
        span = max(0.0, (until - st.last_changed).total_seconds())
        total += span
        if st.state == "on":
            on += span
    return on / total if total else 0.0


def clock(minute: int | None) -> str | None:
    if minute is None:
        return None
    h, m = divmod(int(minute), 60)
    return f"{h:02d}:{m:02d}"


class Heating:
    """Learns the heating and, if asked, warms the house before people get up."""

    def __init__(self, hass: HomeAssistant, entry_id: str, options: dict[str, Any], timeline: Timeline) -> None:
        self.hass = hass
        self.options = options
        self.timeline = timeline
        self._store: Store[dict[str, Any]] = Store(hass, STORAGE_VERSION, f"{DOMAIN}.{entry_id}.heating")
        self.zones: dict[str, dict[str, Any]] = {}  # entity_id -> {rate, episodes, comfort}
        self.wake: dict[str, int | None] = {}
        self.last_run: str | None = None
        self._boosted: dict[str, dict[str, Any]] = {}
        self._done_day: dict[str, str] = {}
        self._unsubs: list = []
        self.learner = None  # set by the runtime: tells it which changes automations made
        self.trust = None  # set by the runtime: ask or say before warming up
        self.own_contexts: set[str] = set()
        self._stopped = False
        self._preheating: set[str] = set()
        self._learning = False

    @property
    def entities(self) -> list[str]:
        chosen = self.options.get(CONF_HEATING_ENTITIES)
        if chosen:
            return list(chosen)
        return [s.entity_id for s in self.hass.states.async_all("climate") if "current_temperature" in s.attributes]

    async def async_start(self) -> None:
        self._stopped = False
        data = await self._store.async_load() or {}
        self.zones = data.get("zones", {})
        self.wake = data.get("wake", {})
        self.last_run = data.get("last_run")
        self._boosted = data.get("boosted", {})
        self._unsubs.append(async_track_time_change(self.hass, self._nightly, hour=3, minute=40, second=0))
        self._unsubs.append(async_track_time_change(self.hass, self._tick, second=5))
        self._unsubs.append(self.hass.bus.async_listen(EVENT_CALL_SERVICE, self._on_call))
        self._unsubs.append(self.hass.bus.async_listen(EVENT_STATE_CHANGED, self._state_changed))
        last = dt_util.parse_datetime(self.last_run) if self.last_run else None
        if last is None or dt_util.utcnow() - last > timedelta(hours=26):
            from homeassistant.helpers.start import async_at_started

            @callback
            def _started(_hass: HomeAssistant) -> None:  # once every thermostat exists
                self.hass.async_create_background_task(self.async_learn(), "sentient_heating_learn")

            self._unsubs.append(async_at_started(self.hass, _started))

    @callback
    def async_stop(self) -> None:
        self._stopped = True
        while self._unsubs:
            self._unsubs.pop()()

    @callback
    def _save(self) -> None:
        self._store.async_delay_save(
            lambda: {"zones": self.zones, "wake": self.wake, "last_run": self.last_run, "boosted": self._boosted}, 2
        )

    # ----- learning --------------------------------------------------------------
    @callback
    def _nightly(self, _now: datetime) -> None:
        self.hass.async_create_background_task(self.async_learn(), "sentient_heating_learn")

    async def _async_history(self, entity_ids: list[str]) -> dict[str, list[State]]:
        if "recorder" not in self.hass.config.components or not entity_ids:
            return {}
        from homeassistant.components.recorder import get_instance, history

        start = dt_util.utcnow() - timedelta(days=WINDOW_DAYS)
        return await get_instance(self.hass).async_add_executor_job(
            lambda: history.get_significant_states(
                self.hass, start, None, entity_ids, significant_changes_only=False, minimal_response=False
            )
        )

    async def _async_switched_on(self) -> dict[str, list[datetime]]:
        """When people (not automations) turned lights and switches on."""
        ids = [e for e in self.options.get(CONF_LEARN_ENTITIES) or [] if e.split(".", 1)[0] in ("light", "switch")]
        if self.learner is not None and self.learner.skip:
            ids = [e for e in ids if e not in self.learner.skip()]
        if not ids:
            return {}
        hist = await self._async_history(ids)
        automated: dict[str, set[int]] = {}
        if self.learner is not None:
            try:
                automated = await self.learner._async_automated(ids)
            except Exception:  # noqa: BLE001
                automated = {}
        from .learning import _changes_to
        out: dict[str, list[datetime]] = {}
        for e, states in hist.items():
            out[e] = _changes_to(states, 'on', automated.get(e))
        return out

    async def async_learn(self) -> dict[str, Any]:
        if self._learning or self._stopped:
            return self.report()
        self._learning = True
        options, values, stamp = self.options, deepcopy(self.options), self._stamp()
        def current():
            return not self._stopped and self.options is options and self.options == values and self._stamp() == stamp
        try:
            return await self._async_learn(current)
        finally:
            self._learning = False

    async def _async_learn(self, current) -> dict[str, Any]:
        # bedroom sensors that fire while people sleep (Settings → Home → Sensors to ignore for bedtime) would make
        # the house look up at 5am: leave them out of when people get up too
        ignore = set(self.options.get("sleep_ignore_sensors") or [])
        inside = [e for e in inside_sensors(self.hass, self.options) if e not in ignore]
        try:
            switched = await self._async_switched_on()
        except Exception:  # noqa: BLE001
            switched = {}
        if not current():
            return self.report()
        try:
            hist = await self._async_history([*self.entities, *inside])
        except Exception:  # noqa: BLE001 - best effort
            _LOGGER.warning("Sentient could not read heating history", exc_info=True)
            return self.report()
        if not current():
            return self.report()
        for entity_id in self.entities:
            states = hist.get(entity_id, [])
            rate, episodes = warmup_rate(states)
            old = self.zones.get(entity_id, {})
            self.zones[entity_id] = {
                "rate": rate if rate is not None else old.get("rate"),
                "episodes": episodes or old.get("episodes", 0),
                "comfort": comfort_target(states) or old.get("comfort"),
            }
        # a sensor that is on most of the time (one that never rests) can't say when anyone got up
        now = dt_util.utcnow()
        wake = usual_wake({e: hist.get(e, []) for e in inside if on_share(hist.get(e, []), now) < 0.8}, switched)
        self.wake = {k: v if v is not None else self.wake.get(k) for k, v in wake.items()}
        self.last_run = dt_util.utcnow().isoformat()
        self._save()
        return self.report()

    # ----- planning ----------------------------------------------------------------
    def comfort(self, entity_id: str) -> float | None:
        if (fixed := _num(self.options.get(CONF_COMFORT_TEMP))) is not None:
            return fixed
        return self.zones.get(entity_id, {}).get("comfort")

    def wake_minute(self, day: datetime) -> int | None:
        """When it should be warm by: the time set in the settings, else the learned getting-up time."""
        weekday = day.weekday() < 5
        if set_by := self.options.get(CONF_WARM_BY_WEEKDAYS if weekday else CONF_WARM_BY_WEEKENDS):
            h, m = (int(x) for x in str(set_by).split(":")[:2])
            return h * 60 + m
        return self.wake.get("weekdays" if weekday else "weekends")

    def plan(self, entity_id: str, now: datetime | None = None) -> dict[str, Any]:
        now = dt_util.as_local(now or dt_util.now())
        st = self.hass.states.get(entity_id)
        zone = self.zones.get(entity_id, {})
        rate, comfort = _num(zone.get("rate")), _num(self.comfort(entity_id))
        rate = rate if rate is not None and rate > 0 else None
        out: dict[str, Any] = {
            "name": st.name if st else entity_id,
            "warms_per_hour": rate,
            "comfortable_at": comfort,
            "learned_from": zone.get("episodes", 0),
        }
        cur = _num(st.attributes.get("current_temperature")) if st else None
        if cur is not None:
            out["now"] = cur
        day = now if (self.wake_minute(now) or 0) > now.hour * 60 + now.minute else now + timedelta(days=1)
        wake = self.wake_minute(day)
        if wake is not None:
            out["warm_by"] = clock(wake)
            if rate and comfort and cur is not None and cur < comfort:
                minutes = round((comfort - cur) / rate * 60)
                out["needs_to_start"] = clock((wake - minutes) % 1440)
                out["minutes_to_warm"] = minutes
        return out

    def report(self) -> dict[str, Any]:
        return {
            "preheat": bool(self.options.get(CONF_PREHEAT)),
            "usually_up": {k: clock(v) for k, v in self.wake.items()},
            "warm_by": {
                "weekdays": str(self.options.get(CONF_WARM_BY_WEEKDAYS) or "")[:5] or None,
                "weekends": str(self.options.get(CONF_WARM_BY_WEEKENDS) or "")[:5] or None,
            },
            "zones": [self.plan(e) for e in self.entities],
            "warming_now": [self.zones_name(e) for e, b in self._boosted.items() if b.get('status') == 'active'],
            "pending_requests": [self.zones_name(e) for e, b in self._boosted.items() if b.get('status') == 'pending'],
            "last_learned": self.last_run,
        }

    def zones_name(self, entity_id: str) -> str:
        st = self.hass.states.get(entity_id)
        return st.name if st else entity_id

    def describe(self) -> list[str]:
        lines = []
        for p in self.report()["zones"]:
            if not p.get("warms_per_hour"):
                continue
            line = f"{p['name']} warms about {p['warms_per_hour']:.1f}° an hour"
            if p.get("comfortable_at"):
                line += f" and is usually set to {p['comfortable_at']:g}°"
            lines.append(line + ".")
        if self.wake.get("weekdays"):
            lines.append(f"People are usually up around {clock(self.wake['weekdays'])} on weekdays"
                         + (f" and {clock(self.wake['weekends'])} at weekends." if self.wake.get("weekends") else "."))
        return lines

    # ----- preheat ---------------------------------------------------------------------
    @callback
    def _tick(self, now: datetime) -> None:
        if self._stopped:
            return
        local = dt_util.as_local(now)
        for entity_id, boost in list(self._boosted.items()):
            until = dt_util.parse_datetime(boost.get('until') or '')
            if until is None or not self._allowed(entity_id, boost) or now > until + RESTORE_GRACE:
                self._drop(entity_id, boost)
                continue
            self._observe(entity_id, boost, self.hass.states.get(entity_id), now)
            if self._boosted.get(entity_id) is boost and now >= until:
                self.hass.async_create_task(self._async_restore(entity_id, boost))
        if not self.options.get(CONF_PREHEAT):
            return
        wake = self.wake_minute(local)
        if wake is None:
            return
        minute = local.hour * 60 + local.minute
        if not 0 < wake - minute <= LOOKAHEAD.total_seconds() / 60:
            return
        today = local.date().isoformat()
        for entity_id in self.entities:
            if self._done_day.get(entity_id) == today or entity_id in self._boosted:
                continue
            st = self.hass.states.get(entity_id)
            rate, comfort = _num(self.zones.get(entity_id, {}).get("rate")), _num(self.comfort(entity_id))
            if st is None or st.state in ("off", "unavailable", "unknown") or rate is None or rate <= 0 or comfort is None:
                continue  # never switch on a zone someone switched off
            cur = _num(st.attributes.get("current_temperature"))
            target = _num(st.attributes.get("temperature"))
            if cur is None or cur >= comfort - 0.3 or (target is not None and target >= comfort - 0.2):
                continue
            needed = (comfort - cur) / rate * 60
            if minute + needed + 5 >= wake:
                self._done_day[entity_id] = today
                self.hass.async_create_task(self._async_boost(entity_id, st, comfort, wake, local))

    def _stamp(self):
        speech = getattr(self.learner, 'speech', None)
        return (getattr(speech, '_privacy_generation', 0), getattr(speech, '_guest_generation', 0))

    @staticmethod
    def _controls(state: State) -> dict:
        return {'state': state.state, 'temperature': _num(state.attributes.get('temperature')),
                'preset': state.attributes.get('preset_mode')}

    def _allowed(self, entity_id, boost):
        from .never import blocked
        if self._stopped or blocked(self.hass, entity_id):
            return False
        if boost.get('origin') == 'preheat':
            return bool(self.options.get(CONF_PREHEAT)) and entity_id in self.entities
        if boost.get('origin') == 'limit':
            return (_num(self.options.get(CONF_HEATING_LIMIT, DEFAULT_HEATING_LIMIT)) or 0) > 0 and entity_id in (self.options.get(CONF_HEATING_ENTITIES) or [])
        return False  # old records lack the ownership evidence needed for a safe restore

    def _drop(self, entity_id, boost):
        if self._boosted.get(entity_id) is boost:
            self._boosted.pop(entity_id)
            self._save()

    def _unconfirmed(self, entity_id, boost):
        if self._boosted.get(entity_id) is boost:
            self._drop(entity_id, boost)
            self.timeline.add('health', f'Temporary heating not confirmed for {self.zones_name(entity_id)}',
                'No matching thermostat update arrived. Automatic restoration was cancelled; check the thermostat.')

    def _observe(self, entity_id, boost, state, now):
        if self._boosted.get(entity_id) is not boost:
            return False
        if state is None or state.state in ('unknown', 'unavailable'):
            deadline = dt_util.parse_datetime(boost.get('pending_until') or '')
            if boost.get('status') == 'pending' and (deadline is None or now > deadline):
                self._unconfirmed(entity_id, boost)
            return False
        controls = self._controls(state)
        if boost.get('status') == 'pending':
            deadline = dt_util.parse_datetime(boost.get('pending_until') or '')
            when = state.last_updated
            target = boost.get('set_to')
            mode = boost.get('requested_mode')
            matches = (state.state != 'off' and (target is None or controls['temperature'] == target)
                       and (mode is None or state.state == mode))
            if matches and deadline is not None and deadline - CONFIRM_WITHIN <= when <= deadline:
                boost['status'], boost['owned'] = 'active', controls
                self._save()
            elif deadline is None or now > deadline:
                self._unconfirmed(entity_id, boost)
                return False
        elif boost.get('status') != 'active' or controls != boost.get('owned'):
            self._drop(entity_id, boost)
            return False
        return boost.get('status') == 'active'

    @callback
    def _state_changed(self, event: Event) -> None:
        if self._stopped:
            return
        entity_id = event.data.get('entity_id')
        boost = self._boosted.get(entity_id)
        if boost is not None:
            self._observe(entity_id, boost, event.data.get('new_state'), dt_util.utcnow())

    async def _async_boost(self, entity_id: str, st: State, comfort: float, wake: int, local: datetime) -> None:
        from .never import blocked
        options, values, before, stamp = self.options, deepcopy(self.options), self._controls(st), self._stamp()
        comfort = _num(comfort)
        def valid():
            state = self.hass.states.get(entity_id)
            cur = _num(state.attributes.get('current_temperature')) if state else None
            low = _num(state.attributes.get('min_temp')) if state else None
            high = _num(state.attributes.get('max_temp')) if state else None
            return (not self._stopped and self.options is options and self.options == values
                    and self._stamp() == stamp
                    and dt_util.utcnow() < dt_util.as_utc(local.replace(hour=wake // 60, minute=wake % 60, second=0, microsecond=0))
                    and options.get(CONF_PREHEAT) and entity_id in self.entities and not blocked(self.hass, entity_id)
                    and entity_id not in self._boosted and state is not None and state.state not in ('off', 'unknown', 'unavailable')
                    and self._controls(state) == before and comfort is not None and cur is not None and cur < comfort - 0.3
                    and before['temperature'] is not None and before['temperature'] < comfort - 0.2
                    and (low is None or comfort >= low) and (high is None or comfort <= high))
        if entity_id in self._preheating or not valid():
            return
        self._preheating.add(entity_id)
        until = local.replace(hour=wake // 60, minute=wake % 60, second=0, microsecond=0) + RESTORE_AFTER
        async def run():
            if not valid():
                return False
            boost = {'origin': 'preheat', 'status': 'pending', 'preset': st.attributes.get('preset_mode'),
                'temperature': _num(st.attributes.get('temperature')), 'set_to': comfort,
                'until': dt_util.as_utc(until).isoformat(), 'pending_until': (dt_util.utcnow() + CONFIRM_WITHIN).isoformat()}
            self._boosted[entity_id] = boost  # only a dispatched request may acquire an observed restore record
            self._save()
            context = own_context(self.hass, 'heating')
            self.own_contexts.add(context.id)
            try:
                await self.hass.services.async_call('climate', 'set_temperature',
                    {'entity_id': entity_id, 'temperature': comfort}, blocking=True, context=context)
            except BaseException as err:
                self._drop(entity_id, boost)
                if isinstance(err, Exception) and not self._stopped and self.options is options and self._stamp() == stamp:
                    self.timeline.add('health', f'Preheat request unconfirmed for {st.name}', 'Check the thermostat. The request has not been retried.')
                raise  # the caller must report an uncertain request, never a successful "Done"
            if self._boosted.get(entity_id) is boost and self.options is options and not self._stopped and self._stamp() == stamp:
                self.timeline.add('heating', f'Requested {st.name} at {comfort:g}° for when people are up', f'usually up around {clock(wake)}')
            return True
        try:
            if self.trust is None:
                await run()
            else:
                await self.trust.async_act(entity_id, f'Warming the {st.name.lower()} to {comfort:g} degrees',
                    f'Shall I warm the {st.name.lower()} to {comfort:g} degrees?', run,
                    f'people are usually up around {clock(wake)}', valid=valid,
                    state_matches=lambda old, new: self._controls(old) == self._controls(new))
        except Exception as err:
            _LOGGER.warning('Sentient could not preheat %s: %s', entity_id, err)
        finally:
            self._preheating.discard(entity_id)

    async def _async_restore(self, entity_id: str, boost: dict[str, Any]) -> None:
        if self._boosted.get(entity_id) is not boost:
            return
        until = dt_util.parse_datetime(boost.get('until') or '')
        if until is None or not self._allowed(entity_id, boost) or dt_util.utcnow() > until + RESTORE_GRACE:
            self._drop(entity_id, boost)
            return
        if dt_util.utcnow() < until:
            return
        options, stamp = self.options, self._stamp()
        st = self.hass.states.get(entity_id)
        if not self._observe(entity_id, boost, st, dt_util.utcnow()):
            return  # unavailable: retain briefly; changed/unconfirmed: never restore blindly
        self._drop(entity_id, boost)  # consume this exact record before awaiting; never remove a newer one
        context = own_context(self.hass, 'heating')
        self.own_contexts.add(context.id)
        try:
            if boost.get('was_off'):
                service, data, restore_to = 'turn_off', {'entity_id': entity_id}, 'off'
            elif boost.get('preset') in (st.attributes.get('preset_modes') or []) and boost.get('preset'):
                service, data, restore_to = 'set_preset_mode', {'entity_id': entity_id, 'preset_mode': boost['preset']}, f"preset {boost['preset']}"
            elif _num(boost.get('temperature')) is not None:
                target = _num(boost['temperature'])
                low, high = _num(st.attributes.get('min_temp')), _num(st.attributes.get('max_temp'))
                if (low is not None and target < low) or (high is not None and target > high):
                    self.timeline.add('health', f'Heating restore skipped for {st.name}', 'The previous target is outside the thermostat’s current limits.')
                    return
                service, data, restore_to = 'set_temperature', {'entity_id': entity_id, 'temperature': target}, f'{target:g}°'
            else:
                return
            await self.hass.services.async_call('climate', service, data, blocking=True, context=context)
            if not self._stopped and self.options is options and self._stamp() == stamp:
                self.timeline.add('heating', f'Restore request sent for {st.name}', f"Return to {restore_to}; {boost.get('why') or 'temporary heating ended'}")
        except Exception as err:
            _LOGGER.warning('Sentient could not restore %s; no automatic retry: %s', entity_id, err)
            if not self._stopped and self.options is options and self._stamp() == stamp:
                self.timeline.add('health', f'Heating restore unconfirmed for {st.name}', f'Could not confirm a return to {restore_to}. Please check the thermostat. The request has not been retried.')

    # ----- time limit on heating turned up by hand or by voice ----------------------------
    @callback
    def _on_call(self, event: Event) -> None:
        if (self._stopped or event.data.get("domain") != "climate" or event.context.id in self.own_contexts
                or is_own(self.hass, event.context)):
            return  # the house's own changes, automations and scripts aren't limited
        hours = _num(self.options.get(CONF_HEATING_LIMIT, DEFAULT_HEATING_LIMIT)) or 0
        service = event.data.get("service")
        data = event.data.get("service_data") or {}
        ids = data.get("entity_id") or []
        ids = [ids] if isinstance(ids, str) else list(ids)
        chosen = set(self.options.get(CONF_HEATING_ENTITIES) or [])
        for entity_id in ids:
            previous = self._boosted.get(entity_id)
            state_before = self.hass.states.get(entity_id)
            old = previous if (previous and previous.get('status') == 'active' and state_before is not None
                               and previous.get('owned') == self._controls(state_before)) else None
            if service in ('set_temperature', 'set_preset_mode', 'set_hvac_mode', 'turn_off', 'turn_on', 'toggle') and previous is not None:
                self._drop(entity_id, previous)
            if hours <= 0 or event.context.parent_id or entity_id not in chosen:
                continue  # only thermostats someone picked in Sentient, never every one in the house
            st = self.hass.states.get(entity_id)
            if st is None or st.state in ('unknown', 'unavailable'):
                continue
            target, baseline = _num(data.get('temperature')), _num(st.attributes.get('temperature'))
            raising = (
                (service == "set_temperature" and target is not None and baseline is not None and target > baseline
                 and data.get('hvac_mode') != 'off')
                or (service == "turn_on" and st.state == "off")
                or (service == "set_hvac_mode" and data.get("hvac_mode") not in (None, "off") and st.state == "off")
            )
            if not raising:
                continue
            until = dt_util.utcnow() + timedelta(hours=hours)
            self._boosted[entity_id] = {
                "origin": "limit", "status": "pending",
                "pending_until": (dt_util.utcnow() + CONFIRM_WITHIN).isoformat(),
                "requested_mode": data.get('hvac_mode'),
                "preset": old["preset"] if old else st.attributes.get("preset_mode"),
                "temperature": old["temperature"] if old else st.attributes.get("temperature"),
                "set_to": _num(data.get("temperature")),
                "was_off": old.get("was_off") if old else st.state == "off",
                "until": until.isoformat(),
                "why": f"after {hours:g} hours, as asked in the settings",
            }
            self._save()
            if old is None:  # pressed again while already turned up: no new line
                self.timeline.add("heating", f"{st.name} adjustment requested — temporary limit {hours:g} hours", "time limit")
