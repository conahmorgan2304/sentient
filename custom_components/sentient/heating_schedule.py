"""Heating schedules: set a thermostat to a temperature (or off) at a time, on chosen days.

Kept in the alarms store as silent entries tagged with a `heating` spec, so the same clock that runs
alarms runs these. The actions are rebuilt from the spec every time, never trusted from storage:
only the thermostats chosen under Heating zones can be scheduled, and the temperature is held to a
sensible range. Switched off in Settings, the entries stay saved and simply don't run.
"""

from __future__ import annotations

from datetime import datetime, timedelta
import logging
from typing import Any

from homeassistant.core import HomeAssistant, callback
from homeassistant.util import dt as dt_util

from .const import CONF_HEATING_ENTITIES, CONF_HEATING_SCHEDULE

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
import re  # noqa: E402

TIME = re.compile(r"^([01]\d|2[0-3]):[0-5]\d$")


def _minute(text: str) -> int:
    h, m = str(text)[:5].split(":")
    return int(h) * 60 + int(m)


def clean_programme(options: dict[str, Any], data: dict[str, Any]) -> dict[str, Any]:
    """A programme from the page, checked; raises ValueError with words a person can read."""
    zones = list(options.get(CONF_HEATING_ENTITIES) or [])
    heaters = [h for h in data.get("heaters") or [] if isinstance(h, str)]
    if not heaters:
        raise ValueError("Choose at least one heater")
    if bad := [h for h in heaters if h not in zones]:
        raise ValueError(f"{bad[0]} isn't one of the heating zones chosen in Sentient")
    days = [d for d in DAYS if d in (data.get("days") or [])]
    if not days:
        raise ValueError("Choose at least one day")
    periods = []
    for p in data.get("periods") or []:
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


def wanted_on(programmes: list[dict[str, Any]], heater: str, when: datetime) -> dict[str, Any] | None:
    """The period that wants this heater on at this moment (None if none)."""
    minute = when.hour * 60 + when.minute
    today, yesterday = DAYS[when.weekday()], DAYS[(when.weekday() - 1) % 7]
    for prog in programmes:
        if not prog.get("enabled", True) or heater not in prog.get("heaters", []):
            continue
        for p in prog.get("periods", []):
            on, off = _minute(p["on"]), _minute(p["off"])
            if on < off and today in prog["days"] and on <= minute < off:
                return p
            if on > off and ((today in prog["days"] and minute >= on) or (yesterday in prog["days"] and minute < off)):
                return p
    return None


def due(programmes: list[dict[str, Any]], when: datetime) -> list[tuple[str, str, float | None, str]]:
    """What happens this minute: (heater, 'on'|'off', temperature, programme name)."""
    minute = when.hour * 60 + when.minute
    today, yesterday = DAYS[when.weekday()], DAYS[(when.weekday() - 1) % 7]
    out: list[tuple[str, str, float | None, str]] = []
    for prog in programmes:
        if not prog.get("enabled", True):
            continue
        for p in prog.get("periods", []):
            on, off = _minute(p["on"]), _minute(p["off"])
            if minute == on and today in prog["days"]:
                out += [(h, "on", float(p["temp"]), prog["name"]) for h in prog["heaters"]]
            off_day = yesterday if off < on else today
            if minute == off and off_day in prog["days"]:
                out += [(h, "off", None, prog["name"]) for h in prog["heaters"]]
    # an 'on' wins over an 'off' for the same heater in the same minute (one period ends as another starts)
    ons = {h for h, what, _t, _n in out if what == "on"}
    return [x for x in out if x[1] == "on" or x[0] not in ons]


def next_change(programmes: list[dict[str, Any]], now: datetime) -> dict[str, Any] | None:
    """The next on or off within a week, for the page."""
    base = now.replace(second=0, microsecond=0)
    for step in range(1, 7 * 1440 + 1):
        when = base + timedelta(minutes=step)
        if events := due(programmes, when):
            heater, what, temp, name = events[0]
            return {"time": when.isoformat(), "what": what, "temp": temp, "name": name, "count": len(events)}
    return None


class HeatingProgrammes:
    """Runs the programmes each minute. Switched off in Settings (Heating → heating schedule), they stay
    saved and nothing runs."""

    def __init__(self, hass: HomeAssistant, items: Any, rt_getter: Any) -> None:
        self.hass = hass
        self.items = items
        self._rt = rt_getter
        self._unsub = None
        self.last_tick: str | None = None  # proof the minute check is running
        self.ran: dict[str, dict[str, Any]] = {}  # programme name -> what happened last time it acted

    async def async_start(self) -> None:
        from homeassistant.helpers.event import async_track_time_change

        self._unsub = async_track_time_change(self.hass, self._tick, second=0)

    @callback
    def async_stop(self) -> None:
        if self._unsub:
            self._unsub()
            self._unsub = None

    def programmes(self) -> list[dict[str, Any]]:
        return self.items.listing()

    @callback
    def _tick(self, now: datetime) -> None:
        rt = self._rt()
        if rt is None or not enabled(rt.options):
            return
        self.last_tick = now.isoformat()
        local = dt_util.as_local(now)
        events = due(self.programmes(), local)
        if events:
            self.hass.async_create_background_task(self.async_run(events, local), "sentient_heating_programme")

    async def async_run(self, events: list[tuple[str, str, float | None, str]], local: datetime) -> list[str]:
        import asyncio

        from .own import own_context

        rt = self._rt()
        for _h, what, temp, name in events:
            self.ran[name] = {"time": dt_util.as_utc(local).isoformat(), "what": what, "temp": temp, "ok": None, "why": "running"}
        zones = set(rt.options.get(CONF_HEATING_ENTITIES) or []) if rt else set()
        done: list[str] = []
        for heater, what, temp, name in events:
            if heater not in zones or not heater.startswith("climate."):
                continue
            if what == "off" and wanted_on(self.programmes(), heater, local + timedelta(minutes=1)):
                self.ran[name].update(ok=True, why="left on: another period wants it")
                continue  # another period still wants it on
            if what == "on":
                actions, why = heating_actions(self.hass, rt.options, {"entity_id": heater, "temperature": temp})
            else:
                actions, why = heating_actions(self.hass, rt.options, {"entity_id": heater, "temperature": None})
            if why:
                self.ran[name].update(ok=False, why=why)
                if rt is not None:
                    rt.timeline.add("health", f"Heating schedule didn't change {heater}", why)
                continue
            failed = False
            for action in actions:
                domain, service = action["action"].split(".", 1)
                try:
                    async with asyncio.timeout(30):  # a thermostat that never answers mustn't leave this hanging silently
                        await self.hass.services.async_call(domain, service, {**action.get("data", {}), **action["target"]},
                                                            blocking=True, context=own_context(self.hass, f"heating schedule: {name}"))
                except Exception as err:  # noqa: BLE001 - one heater failing shouldn't stop the others
                    failed = True
                    msg = "the thermostat didn't answer within 30 seconds" if isinstance(err, TimeoutError) else str(err)[:120]
                    self.ran[name].update(ok=False, why=msg)
                    _LOGGER.warning("Sentient heating schedule %s couldn't change %s: %s", name, heater, msg)
                    if rt is not None:
                        rt.timeline.add("health", f"Heating schedule couldn't change {heater}", msg)
                    continue
            if failed:
                continue
            # the schedule has taken over this heater: a hand boost's "put it back later" no longer applies
            heating = getattr(rt, "heating", None)
            if heating is not None and (boost := getattr(heating, "_boosted", {}).get(heater)) is not None:
                heating._drop(heater, boost)
            st = self.hass.states.get(heater)
            label = st.name if st else heater
            done.append(f"{label} {'on to ' + format(temp, 'g') + '°' if what == 'on' else 'off'}")
            self.ran[name].update(ok=True, why=done[-1])
        if done and rt is not None:
            rt.timeline.add("mode", "Heating schedule: " + ", ".join(done), name)
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
