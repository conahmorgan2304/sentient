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

from datetime import datetime, time, timedelta
import logging
from statistics import median
from typing import TYPE_CHECKING, Any

from homeassistant.core import HomeAssistant, State, callback
from homeassistant.helpers.event import async_track_time_change
from homeassistant.helpers.storage import Store
from homeassistant.util import dt as dt_util

from .const import CONF_COMFORT_TEMP, CONF_HEATING_ENTITIES, CONF_PREHEAT, DOMAIN
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


def _num(value: Any) -> float | None:
    try:
        return float(value)
    except (TypeError, ValueError):
        return None


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
    return round(median(rates), 2), len(rates)


def comfort_target(states: list[State]) -> float | None:
    """The daytime target it spends longest at."""
    held: dict[float, float] = {}
    for st, nxt in zip(states, states[1:] + [None]):
        target = _num(st.attributes.get("temperature"))
        if target is None or st.state == "off":
            continue
        local = dt_util.as_local(st.last_updated)
        if not 7 <= local.hour < 22:
            continue
        end = nxt.last_updated if nxt is not None else dt_util.utcnow()
        held[round(target * 2) / 2] = held.get(round(target * 2) / 2, 0) + (end - st.last_updated).total_seconds()
    return max(held, key=held.get) if held else None


UP_EVENTS = 3  # movements...
UP_ROOMS = 2  # ...in at least this many sensors...
UP_SPAN = timedelta(minutes=15)  # ...within this long: people up, not a cat or someone turning over


def usual_wake(sensors: dict[str, list[State]]) -> dict[str, int | None]:
    """Median time the house gets going in the morning, weekdays and weekends.

    The first moment there are several movements in more than one place within a quarter of an
    hour — a single blip in the night (a pet, someone turning over) doesn't count.
    """
    by_day: dict[Any, list[tuple[datetime, str]]] = {}
    for entity_id, states in sensors.items():
        for st in states:
            if st.state != "on":
                continue
            local = dt_util.as_local(st.last_changed)
            if WAKE_FROM <= local.time() < WAKE_UNTIL:
                by_day.setdefault(local.date(), []).append((local, entity_id))
    first: dict[Any, int] = {}
    for day, events in by_day.items():
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

    @property
    def entities(self) -> list[str]:
        chosen = self.options.get(CONF_HEATING_ENTITIES)
        if chosen:
            return list(chosen)
        return [s.entity_id for s in self.hass.states.async_all("climate") if "current_temperature" in s.attributes]

    async def async_start(self) -> None:
        data = await self._store.async_load() or {}
        self.zones = data.get("zones", {})
        self.wake = data.get("wake", {})
        self.last_run = data.get("last_run")
        self._boosted = data.get("boosted", {})
        self._unsubs.append(async_track_time_change(self.hass, self._nightly, hour=3, minute=40, second=0))
        self._unsubs.append(async_track_time_change(self.hass, self._tick, second=5))
        last = dt_util.parse_datetime(self.last_run) if self.last_run else None
        if last is None or dt_util.utcnow() - last > timedelta(hours=26):
            self.hass.async_create_background_task(self.async_learn(), "sentient_heating_learn")

    @callback
    def async_stop(self) -> None:
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

    async def async_learn(self) -> dict[str, Any]:
        inside = inside_sensors(self.hass, self.options)
        try:
            hist = await self._async_history([*self.entities, *inside])
        except Exception:  # noqa: BLE001 - best effort
            _LOGGER.warning("Sentient could not read heating history", exc_info=True)
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
        wake = usual_wake({e: hist.get(e, []) for e in inside})
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
        return self.wake.get("weekdays" if day.weekday() < 5 else "weekends")

    def plan(self, entity_id: str, now: datetime | None = None) -> dict[str, Any]:
        now = dt_util.as_local(now or dt_util.now())
        st = self.hass.states.get(entity_id)
        zone = self.zones.get(entity_id, {})
        rate, comfort = zone.get("rate"), self.comfort(entity_id)
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
            out["people_usually_up"] = clock(wake)
            if rate and comfort and cur is not None and cur < comfort:
                minutes = round((comfort - cur) / rate * 60)
                out["needs_to_start"] = clock((wake - minutes) % 1440)
                out["minutes_to_warm"] = minutes
        return out

    def report(self) -> dict[str, Any]:
        return {
            "preheat": bool(self.options.get(CONF_PREHEAT)),
            "usually_up": {k: clock(v) for k, v in self.wake.items()},
            "zones": [self.plan(e) for e in self.entities],
            "warming_now": [self.zones_name(e) for e in self._boosted],
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
            line = f"{p['name']} warms about {p['warms_per_hour']:g}° an hour"
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
        local = dt_util.as_local(now)
        for entity_id, boost in list(self._boosted.items()):
            if now >= dt_util.parse_datetime(boost["until"]):
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
            rate, comfort = self.zones.get(entity_id, {}).get("rate"), self.comfort(entity_id)
            if st is None or st.state in ("off", "unavailable", "unknown") or not rate or not comfort:
                continue  # never switch on a zone someone switched off
            cur = _num(st.attributes.get("current_temperature"))
            target = _num(st.attributes.get("temperature"))
            if cur is None or cur >= comfort - 0.3 or (target is not None and target >= comfort - 0.2):
                continue
            needed = (comfort - cur) / rate * 60
            if minute + needed + 5 >= wake:
                self._done_day[entity_id] = today
                self.hass.async_create_task(self._async_boost(entity_id, st, comfort, wake, local))

    async def _async_boost(self, entity_id: str, st: State, comfort: float, wake: int, local: datetime) -> None:
        until = local.replace(hour=wake // 60, minute=wake % 60, second=0, microsecond=0) + RESTORE_AFTER
        self._boosted[entity_id] = {
            "preset": st.attributes.get("preset_mode"),
            "temperature": st.attributes.get("temperature"),
            "set_to": comfort,
            "until": dt_util.as_utc(until).isoformat(),
        }
        self._save()
        try:
            await self.hass.services.async_call(
                "climate", "set_temperature", {"entity_id": entity_id, "temperature": comfort}, blocking=True
            )
        except Exception as err:  # noqa: BLE001
            _LOGGER.warning("Sentient could not preheat %s: %s", entity_id, err)
            self._boosted.pop(entity_id, None)
            self._save()
            return
        self.timeline.add(
            "heating", f"Warming {st.name} to {comfort:g}° for when people are up", f"usually up around {clock(wake)}"
        )

    async def _async_restore(self, entity_id: str, boost: dict[str, Any]) -> None:
        self._boosted.pop(entity_id, None)
        self._save()
        st = self.hass.states.get(entity_id)
        if st is None or st.state == "off":
            return
        try:
            preset = boost.get("preset")
            if preset and preset in (st.attributes.get("preset_modes") or []) and st.attributes.get("preset_mode") != preset:
                await self.hass.services.async_call(
                    "climate", "set_preset_mode", {"entity_id": entity_id, "preset_mode": preset}, blocking=True
                )
            elif not preset and boost.get("temperature") is not None and _num(st.attributes.get("temperature")) == boost.get("set_to"):
                await self.hass.services.async_call(
                    "climate", "set_temperature", {"entity_id": entity_id, "temperature": boost["temperature"]}, blocking=True
                )
        except Exception as err:  # noqa: BLE001
            _LOGGER.warning("Sentient could not put %s back on its schedule: %s", entity_id, err)
