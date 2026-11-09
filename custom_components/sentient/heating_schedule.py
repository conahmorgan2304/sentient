"""Heating schedules: set a thermostat to a temperature (or off) at a time, on chosen days.

The programme scheduler runs from a minute timer. Older single heating times still use the
alarms store. Both rebuild climate commands from validated settings and check Never touch.
When a thermostat confirms that the programme switched it on, the scheduler records the
requested controls so it can safely catch a missed switch-off after a restart.
"""

from __future__ import annotations

import asyncio
from datetime import datetime, timedelta
import logging
import re
from typing import Any

from homeassistant.const import EVENT_STATE_CHANGED
from homeassistant.core import HomeAssistant, callback
from homeassistant.helpers.storage import Store
from homeassistant.util import dt as dt_util

from .const import CONF_HEATING_ENTITIES, CONF_HEATING_SCHEDULE, CONF_NEVER_ENTITIES, DOMAIN
from .never import blocked

_LOGGER = logging.getLogger(__name__)

MIN_TEMP = 5.0
MAX_TEMP = 28.0
ON_MODES = ("heat", "heat_cool", "auto")  # what to switch an 'off' thermostat to, in order of preference


def enabled(options: dict[str, Any]) -> bool:
    return bool(options.get(CONF_HEATING_SCHEDULE, False))


def heating_actions(hass: HomeAssistant, options: dict[str, Any], spec: Any) -> tuple[list[dict[str, Any]], str | None]:
    """(the actions, None) or ([], why not)."""
    if not isinstance(spec, dict):
        return [], "No heating instructions"
    entity_id = str(spec.get("entity_id") or "")
    if entity_id not in (options.get(CONF_HEATING_ENTITIES) or []):
        return [], "That thermostat isn't one of the heating zones chosen in Sentient"
    if not entity_id.startswith("climate."):
        return [], "Only thermostats can be scheduled"
    if blocked(hass, entity_id) or entity_id in (options.get(CONF_NEVER_ENTITIES) or []):
        return [], "That thermostat is on the never-touch list"
    target = {"entity_id": entity_id}
    temperature = spec.get("temperature")
    if temperature in (None, "", "off"):
        return [{"action": "climate.turn_off", "target": target}], None
    try:
        degrees = float(temperature)
    except (TypeError, ValueError):
        return [], "The temperature must be a number"
    if not MIN_TEMP <= degrees <= MAX_TEMP:
        return [], f"The temperature must be between {MIN_TEMP:g}° and {MAX_TEMP:g}°"
    data: dict[str, Any] = {"temperature": degrees}
    st = hass.states.get(entity_id)
    if st is not None and st.state == "off":
        # a temperature alone doesn't wake a thermostat that is off (an earlier "off" time may have done that)
        modes = list(st.attributes.get("hvac_modes") or [])
        if mode := next((m for m in ON_MODES if m in modes), None):
            data["hvac_mode"] = mode
    return [{"action": "climate.set_temperature", "target": target, "data": data}], None


def describe(hass: HomeAssistant, spec: dict[str, Any]) -> str:
    st = hass.states.get(str(spec.get("entity_id") or ""))
    zone = st.name if st else str(spec.get("entity_id"))
    t = spec.get("temperature")
    return f"{zone} off" if t in (None, "", "off") else f"{zone} to {float(t):g}°"


# ----- programmes: a proper schedule ------------------------------------------------------------
# A programme is a set of heaters, the days it runs, and any number of on/off periods a day, each with
# its own temperature: on 06:00 off 09:00 at 21°, on 15:00 off 17:00, on 20:00 off 21:00. At each "on"
# the heaters are set to the temperature (and switched on if they are off); at each "off" they are
# switched off, unless another period still wants that heater on. A period that ends after midnight
# ("on 22:00 off 01:00") ends on the next morning.

DAYS = ("mon", "tue", "wed", "thu", "fri", "sat", "sun")

TIME = re.compile(r"^([01]\d|2[0-3]):[0-5]\d$")


def _minute(text: str) -> int:
    h, m = str(text)[:5].split(":")
    return int(h) * 60 + int(m)


def clean_programme(options: dict[str, Any], data: dict[str, Any]) -> dict[str, Any]:
    """A programme from the page, checked; raises ValueError with words a person can read."""
    zones = list(options.get(CONF_HEATING_ENTITIES) or [])
    raw_heaters, raw_days, raw_periods = (data.get(key) for key in ("heaters", "days", "periods"))
    if not isinstance(raw_heaters, list) or any(not isinstance(h, str) for h in raw_heaters):
        raise ValueError("Choose the heaters from the list")
    if not isinstance(raw_days, list):
        raise ValueError("Choose the days from the list")
    if not isinstance(raw_periods, list) or any(not isinstance(period, dict) for period in raw_periods):
        raise ValueError("Each period needs an on and an off time")
    if type(data.get("enabled", True)) is not bool:
        raise ValueError("Enabled must be true or false")
    heaters = list(dict.fromkeys(raw_heaters))
    if not heaters:
        raise ValueError("Choose at least one heater")
    if bad := [h for h in heaters if h not in zones or not h.startswith("climate.")]:
        raise ValueError(f"{bad[0]} isn't one of the heating zones chosen in Sentient")
    days = [d for d in DAYS if d in raw_days]
    if not days:
        raise ValueError("Choose at least one day")
    periods = []
    for p in raw_periods:
        on, off = str(p.get("on") or "")[:5], str(p.get("off") or "")[:5]
        if not TIME.match(on) or not TIME.match(off):
            raise ValueError("Each period needs an on and an off time")
        if on == off:
            raise ValueError(f"The period at {on} switches on and off at the same time")
        try:
            temp = float(p.get("temp"))
        except (TypeError, ValueError):
            raise ValueError("Each period needs a temperature") from None
        if not MIN_TEMP <= temp <= MAX_TEMP:
            raise ValueError(f"Temperatures must be between {MIN_TEMP:g}° and {MAX_TEMP:g}°")
        periods.append({"on": on, "off": off, "temp": temp})
    if not periods:
        raise ValueError("Add at least one on/off period")
    periods.sort(key=lambda p: p["on"])
    name = str(data.get("name") or "").strip() or "Heating"
    return {"name": name, "heaters": heaters, "days": days, "periods": periods, "enabled": bool(data.get("enabled", True))}


DAY_MINUTES = 1440
WEEK_MINUTES = 7 * DAY_MINUTES


def _weekly_periods(programme):
    """Start and exclusive end in minutes from Monday, including overnight endings."""
    for day in programme["days"]:
        base = DAYS.index(day) * DAY_MINUTES
        for period in programme["periods"]:
            on, off = _minute(period["on"]), _minute(period["off"])
            yield base + on, base + off + (DAY_MINUTES if off < on else 0), period


def _overlap(first, second) -> bool:
    start, end, period = first
    other_start, other_end, other = second
    if float(period["temp"]) == float(other["temp"]):
        return False
    # Check either side of the Sunday/Monday boundary as well as the same week.
    return any(start < other_end + shift and other_start + shift < end
               for shift in (0, -WEEK_MINUTES, WEEK_MINUTES))


def programme_conflict(programme, others) -> str | None:
    """Why this programme cannot be enabled alongside the others."""
    if not programme.get("enabled", True):
        return None
    windows = list(_weekly_periods(programme))
    for index, window in enumerate(windows):
        if any(_overlap(window, other) for other in windows[index + 1:]):
            return "This programme has overlapping periods with different temperatures"
    for other in others:
        if not other.get("enabled", True):
            continue
        if programme.get("id") and programme["id"] == other.get("id"):
            continue
        shared = set(programme["heaters"]) & set(other["heaters"])
        if shared and any(_overlap(a, b) for b in _weekly_periods(other) for a in windows):
            return f"Conflicts with {other['name']} on {', '.join(sorted(shared))}: overlapping times have different temperatures"
    return None


def _week_minute(when: datetime) -> int:
    return when.weekday() * DAY_MINUTES + when.hour * 60 + when.minute


def _active_period(programmes, heater, when):
    """Most recently started period wins for existing overlaps. Saved order breaks ties."""
    now = _week_minute(when)
    best = None
    for programme in programmes:
        if not programme.get("enabled", True) or heater not in programme["heaters"]:
            continue
        for start, end, period in _weekly_periods(programme):
            elapsed = (now - start) % WEEK_MINUTES
            if elapsed < end - start and (best is None or elapsed < best[2]):
                best = programme, period, elapsed
    return best


def wanted_on(programmes: list[dict[str, Any]], heater: str, when: datetime) -> dict[str, Any] | None:
    """The same winning period used to plan thermostat commands."""
    active = _active_period(programmes, heater, when)
    return active[1] if active else None


def due(programmes: list[dict[str, Any]], when: datetime, *, by_id: bool = False) -> list[tuple[str, str, float | None, str]]:
    """Return at most one command per thermostat at a programme boundary."""
    minute = _week_minute(when)
    heaters: dict[str, None] = {}
    for programme in programmes:
        if programme.get("enabled", True) and any(
            minute in (start, end % WEEK_MINUTES) for start, end, _ in _weekly_periods(programme)
        ):
            heaters.update(dict.fromkeys(programme["heaters"]))
    out = []
    for heater in heaters:
        before = _active_period(programmes, heater, when - timedelta(minutes=1))
        after = _active_period(programmes, heater, when)
        if after:
            programme, period, elapsed = after
            temp = float(period["temp"])
            if before and elapsed and float(before[1]["temp"]) == temp:
                continue
            what = "on"
        elif before:
            programme, _, _ = before
            what, temp = "off", None
        else:
            continue
        key = programme.get("id", programme["name"]) if by_id else programme["name"]
        out.append((heater, what, temp, key))
    return out


def next_change(programmes: list[dict[str, Any]], now: datetime) -> dict[str, Any] | None:
    """Check the actual programme boundaries in the next week."""
    base = now.replace(second=0, microsecond=0)
    minute = _week_minute(base)
    offsets = {
        (boundary - minute) % WEEK_MINUTES or WEEK_MINUTES
        for programme in programmes if programme.get("enabled", True)
        for start, end, _ in _weekly_periods(programme)
        for boundary in (start, end)
    }
    for offset in sorted(offsets):
        when = base + timedelta(minutes=offset)
        if events := due(programmes, when):
            _, what, temp, name = events[0]
            return {"time": when.isoformat(), "what": what, "temp": temp, "name": name, "count": len(events)}
    return None


class HeatingProgrammes:
    """Runs the programmes each minute. Switched off in Settings (Heating → heating schedule), they stay
    saved and nothing runs."""

    def __init__(self, hass: HomeAssistant, items: Any, rt_getter: Any, entry_id: str) -> None:
        self.hass = hass
        self.items = items
        self._rt = rt_getter
        self._unsubs: list = []
        self._stopped = False
        self._lock = asyncio.Lock()
        self._store = Store(hass, 1, f"{DOMAIN}.{entry_id}.heating_programme_ownership")
        self._owned: dict[str, dict[str, Any]] = {}
        self._pending: dict[str, dict[str, Any]] = {}
        self.last_tick: str | None = None
        self.ran: dict[str, dict[str, Any]] = {}

    async def async_start(self) -> None:
        from homeassistant.helpers.event import async_track_time_change

        stored = await self._store.async_load() or {}
        self._owned = dict(stored.get("owned") or {})
        self._stopped = False
        self._unsubs = [
            async_track_time_change(self.hass, self._tick, second=0),
            self.hass.bus.async_listen(EVENT_STATE_CHANGED, self._state_changed),
        ]

    @callback
    def async_stop(self) -> None:
        self._stopped = True
        self._pending.clear()
        while self._unsubs:
            self._unsubs.pop()()

    def programmes(self) -> list[dict[str, Any]]:
        return self.items.listing()

    def _save(self) -> None:
        self._store.async_delay_save(lambda: {"owned": self._owned}, 1)

    def _drop(self, heater: str) -> None:
        if self._owned.pop(heater, None) is not None:
            self._save()

    @staticmethod
    def _controls(state) -> tuple:
        return state.state, state.attributes.get("temperature"), state.attributes.get("preset_mode")

    def _observe(self, heater: str, state) -> None:
        if state is None or state.state in ("unknown", "unavailable"):
            return
        controls = self._controls(state)
        if pending := self._pending.get(heater):
            if dt_util.utcnow() > pending["confirm_by"]:
                self._pending.pop(heater)
            elif pending["context"] in (state.context.id, state.context.parent_id):
                if controls[:2] == (pending["mode"], pending["temperature"]):
                    self._owned[heater] = {"key": pending["key"], "end": pending["end"], "controls": list(controls)}
                    self._pending.pop(heater)
                    self._save()
                    return
        if (owned := self._owned.get(heater)) and list(controls) != owned["controls"]:
            self._drop(heater)

    @callback
    def _state_changed(self, event) -> None:
        heater = event.data["entity_id"]
        if heater not in self._owned and heater not in self._pending:
            return
        old, new = event.data.get("old_state"), event.data.get("new_state")
        if old is not None and new is not None and self._controls(old) != self._controls(new):
            if heater in self._pending and new.context.user_id is not None:
                self._pending.pop(heater)
            self._observe(heater, new)

    def _prepare_ownership(self, heater, key, local, action, context) -> None:
        programme = self.items.items.get(key)
        state = self.hass.states.get(heater)
        if programme is None or state is None or state.state in ("unknown", "unavailable"):
            return
        mode = action["data"].get("hvac_mode", state.state)
        if mode not in ON_MODES or (state.state != "off" and heater not in self._owned):
            return
        active = _active_period([programme], heater, local)
        if active is None:
            return
        _, period, elapsed = active
        on, off = _minute(period["on"]), _minute(period["off"])
        remaining = (off - on) % DAY_MINUTES - elapsed
        end = local.replace(second=0, microsecond=0) + timedelta(minutes=remaining)
        self._pending[heater] = {
            "mode": mode, "temperature": action["data"]["temperature"], "key": key,
            "context": context.id, "confirm_by": dt_util.utcnow() + timedelta(minutes=2),
            "end": dt_util.as_utc(end).isoformat(),
        }

    @callback
    def _tick(self, now: datetime) -> None:
        rt = self._rt()
        if self._stopped or rt is None or not enabled(rt.options):
            return
        self.last_tick = now.isoformat()
        self._pending = {heater: row for heater, row in self._pending.items() if now <= row["confirm_by"]}
        local = dt_util.as_local(now)
        programmes = self.programmes()
        events = due(programmes, local, by_id=True)
        scheduled = {heater for heater, *_ in events}
        for heater, record in list(self._owned.items()):
            programme = self.items.items.get(record["key"])
            if programme is None or not programme.get("enabled", True) or heater not in programme["heaters"]:
                self._drop(heater)
                continue
            if heater in scheduled or wanted_on(programmes, heater, local):
                continue
            end = dt_util.parse_datetime(record["end"])
            if end is not None and dt_util.as_utc(local) >= end:
                state = self.hass.states.get(heater)
                self._observe(heater, state)
                if heater in self._owned and state is not None and state.state not in ("unknown", "unavailable"):
                    events.append((heater, "recover_off", None, record["key"]))
        if events:
            self.hass.async_create_background_task(self.async_run(events, local), "sentient_heating_programme")

    async def async_run(self, events: list[tuple[str, str, float | None, str]], local: datetime) -> list[str]:
        async with self._lock:
            return await self._async_run(events, local)

    async def _async_run(self, events: list[tuple[str, str, float | None, str]], local: datetime) -> list[str]:
        from .own import own_context

        rt = self._rt()
        done: list[str] = []
        rows: dict[str, dict[str, Any]] = {}
        for heater, what, temp, key in events:
            if self._stopped or rt is None or not enabled(rt.options):
                break
            programme = self.items.items.get(key)
            name = programme["name"] if programme else key
            if programme is not None and not programme.get("enabled", True) and what != "recover_off":
                continue
            state = self.hass.states.get(heater)
            label = state.name if state else heater
            row = rows.setdefault(key, {"time": dt_util.as_utc(local).isoformat(), "zones": {}})
            zone = row["zones"].setdefault(heater, {"name": label, "ok": None, "why": "running"})
            self.ran[key] = row
            if what != "on" and wanted_on(self.programmes(), heater, local):
                zone.update(ok=True, why="left on: another period wants it")
                continue
            if what == "recover_off":
                if programme is None or not programme.get("enabled", True) or heater not in programme["heaters"]:
                    self._drop(heater)
                    continue
                record = self._owned.get(heater)
                if state is None or state.state in ("unknown", "unavailable"):
                    zone.update(ok=None, why="waiting for thermostat state")
                    continue
                if not record or list(self._controls(state)) != record["controls"]:
                    self._drop(heater)
                    zone.update(ok=None, why="left alone: thermostat settings changed")
                    continue
            actions, why = heating_actions(self.hass, rt.options, {"entity_id": heater, "temperature": temp})
            if why:
                zone.update(ok=False, why=why)
                if what == "recover_off":
                    self._drop(heater)
                rt.timeline.add("health", f"Heating schedule didn't change {heater}", why)
                continue
            action = actions[0]
            domain, service = action["action"].split(".", 1)
            context = own_context(self.hass, f"heating schedule: {name}")
            if what == "on":
                self._prepare_ownership(heater, key, local, action, context)
            try:
                async with asyncio.timeout(30):
                    await self.hass.services.async_call(
                        domain, service, {**action.get("data", {}), **action["target"]},
                        blocking=True, context=context,
                    )
            except Exception as err:  # noqa: BLE001 - one heater failing shouldn't stop the others
                self._pending.pop(heater, None)
                msg = "the thermostat didn't answer within 30 seconds" if isinstance(err, TimeoutError) else str(err)[:120]
                zone.update(ok=False, why=msg)
                _LOGGER.warning("Sentient heating schedule %s couldn't change %s: %s", name, heater, msg)
                rt.timeline.add("health", f"Heating schedule couldn't change {heater}", msg)
                if what == "recover_off" or (what == "off" and isinstance(err, TimeoutError)):
                    self._drop(heater)  # do not repeatedly dispatch an uncertain request
                continue
            if what != "on":
                self._pending.pop(heater, None)
                self._drop(heater)
            heating = getattr(rt, "heating", None)
            if heating is not None and (boost := getattr(heating, "_boosted", {}).get(heater)) is not None:
                heating._drop(heater, boost)
            requested = f"on to {temp:g}°" if what == "on" else "off"
            result = f"{label}: requested {requested}"
            if what == "recover_off":
                result += " after a missed switch-off"
            done.append(result)
            zone.update(ok=True, why=result)
        for key, row in rows.items():
            results = list(row["zones"].values())
            if any(zone["ok"] is False for zone in results):
                row["ok"] = False
            elif all(zone["ok"] is True for zone in results):
                row["ok"] = True
            else:
                row["ok"] = None
            row["why"] = " · ".join(zone["why"] for zone in results)
            accepted = [zone["why"] for zone in results if zone["ok"] is True]
            if accepted:
                rt.timeline.add("mode", "Heating schedule: " + ", ".join(accepted), (self.items.items.get(key) or {}).get("name", key))
        return done


def suggest(zones: list[str], wake: dict[str, int | None], learned: dict[str, dict[str, Any]]) -> list[dict[str, Any]]:
    """A starting schedule from what's been learned: warm by when people usually get up (started early enough for
    how fast the house warms), off a couple of hours later on weekdays, back on for the evening. A suggestion only:
    nothing is saved until someone says so."""
    if not zones:
        return []
    rates = [r for z in zones if (r := (learned.get(z) or {}).get("rate"))]
    comforts = [c for z in zones if (c := (learned.get(z) or {}).get("comfort"))]
    temp = max(19.0, round(sum(comforts) / len(comforts) * 2) / 2) if comforts else 20.0
    rate = sum(rates) / len(rates) if rates else 1.0
    lead = max(30, min(90, round(2 / max(rate, 0.3) * 60 / 15) * 15))  # about two degrees' warming, to the quarter-hour

    def hhmm(minute: int) -> str:
        minute %= 1440
        return f"{minute // 60:02d}:{minute % 60:02d}"
    wd = wake.get("weekdays") or 6 * 60 + 30
    we = wake.get("weekends") or 8 * 60
    return [
        {"name": "Weekdays (suggested)", "heaters": list(zones), "days": list(DAYS[:5]), "enabled": True,
         "periods": [{"on": hhmm(wd - lead), "off": hhmm(wd + 120), "temp": temp}, {"on": "17:00", "off": "22:00", "temp": temp}]},
        {"name": "Weekends (suggested)", "heaters": list(zones), "days": list(DAYS[5:]), "enabled": True,
         "periods": [{"on": hhmm(we - lead), "off": "22:00", "temp": temp}]},
    ]
