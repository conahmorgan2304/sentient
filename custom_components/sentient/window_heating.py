"""Open windows and the heating: turn a room's own heater down while its window is open; say so for whole-house heating.

23 Nov: the bedroom window was open in 0.7° with the heating on. When a window has been open for a few minutes:

* **a heater in the same room** (one of the thermostats chosen for Sentient's heating) is turned down to the night
  temperature, and put back when every window in that room is shut — but only if it's still at what Sentient set
  (nobody has changed it since) and only to what it was, or to what the heating schedule wants by then;
* **heating that isn't in that room** (a whole-house thermostat in the hallway, say) is never touched because of
  one window: if it's on, the house is told once — "The bedroom window's been open five minutes and the heating's
  on." — as a normal remark, so quiet hours and hush apply.

A thermostat in a pass-through area (hallway, landing) counts as whole-house even if a window there opens.
Turned-down heaters are remembered over a restart; a window whose sensor stops reporting doesn't count as open, and
nothing stays turned down for more than three hours. The heating schedule waits for a heater that's turned down.
No AI. Off until switched on (Energy and appliances › Heating).
"""

from __future__ import annotations

import asyncio
from datetime import datetime, timedelta
import logging
from typing import Any, Callable

from homeassistant.const import EVENT_STATE_CHANGED
from homeassistant.core import Event, HomeAssistant, callback
from homeassistant.helpers import area_registry as ar
from homeassistant.helpers.event import async_track_time_interval
from homeassistant.helpers.storage import Store
from homeassistant.util import dt as dt_util

from .const import DOMAIN

_LOGGER = logging.getLogger(__name__)

CONF_WINDOW_HEATING = "window_heating"
CONF_WINDOW_MINUTES = "window_heating_minutes"
DEFAULT_MINUTES = 5.0
LONGEST = timedelta(hours=3)
WINDOW_CLASSES = ("window",)


def _num(value: Any) -> float | None:
    from .numeric import finite_float

    return finite_float(value)


class WindowHeating:
    def __init__(self, hass: HomeAssistant, entry_id: str, options: dict[str, Any], rt_getter: Callable[[], Any]) -> None:
        self.hass = hass
        self.options = options
        self._rt = rt_getter
        self._store: Store[dict[str, Any]] = Store(hass, 1, f"{DOMAIN}.{entry_id}.window_heating")
        self.down: dict[str, dict[str, Any]] = {}  # heater -> {"was", "set", "since", "area", "windows"}
        self._told: set[str] = set()  # windows already mentioned this time they're open
        self._gave_up: set[str] = set()  # windows open past the longest time: left alone until they're shut
        self._unsubs: list[Callable[[], None]] = []
        self._stopped = False
        self._check_lock = asyncio.Lock()

    @property
    def enabled(self) -> bool:
        return bool(self.options.get(CONF_WINDOW_HEATING))

    async def async_start(self) -> None:
        self._stopped = False
        try:
            self.down = dict((await self._store.async_load() or {}).get("down") or {})
        except Exception:  # noqa: BLE001 - a damaged file: nothing is turned down
            self.down = {}
        self._unsubs.append(async_track_time_interval(self.hass, self._tick, timedelta(minutes=1)))
        self._unsubs.append(self.hass.bus.async_listen(EVENT_STATE_CHANGED, self._changed))

    @callback
    def async_stop(self) -> None:
        self._stopped = True
        for unsub in self._unsubs:
            unsub()
        self._unsubs.clear()

    def holding(self, heater: str) -> bool:
        """Is this heater turned down for an open window? (The heating schedule waits for it.)"""
        return self.enabled and heater in self.down

    # what's where
    def _area(self, entity_id: str) -> str | None:
        from .presence import area_of

        return area_of(self.hass, entity_id)

    def _windows(self) -> list[Any]:
        return [s for s in self.hass.states.async_all("binary_sensor")
                if s.attributes.get("device_class") in WINDOW_CLASSES]

    def _heaters(self) -> list[str]:
        from .const import CONF_HEATING_ENTITIES

        return [h for h in self.options.get(CONF_HEATING_ENTITIES) or [] if h.startswith("climate.")]

    def _pass_through(self, area_id: str | None) -> bool:
        if not area_id:
            return True
        names = {str(a).casefold() for a in self.options.get("pass_through_areas") or []}
        area = ar.async_get(self.hass).async_get_area(area_id)
        return area_id.casefold() in names or bool(area and area.name.casefold() in names)

    def _room_heaters(self, area_id: str | None) -> list[str]:
        if not area_id or self._pass_through(area_id):
            return []
        from .never import blocked

        # a never-touch thermostat is left alone (the house is still told, as for whole-house heating)
        return [h for h in self._heaters() if self._area(h) == area_id and not blocked(self.hass, h)]

    def _open_long_enough(self, now: datetime) -> list[Any]:
        wait = timedelta(minutes=_num(self.options.get(CONF_WINDOW_MINUTES)) or DEFAULT_MINUTES)
        return [w for w in self._windows() if w.state == "on" and now - w.last_changed >= wait]

    @staticmethod
    def _warming(st: Any) -> bool:
        if st is None or st.state in ("off", "unavailable", "unknown"):
            return False
        target, current = _num(st.attributes.get("temperature")), _num(st.attributes.get("current_temperature"))
        return st.attributes.get("hvac_action") == "heating" or (
            target is not None and (current is None or target > current - 0.5))

    def _night(self) -> float:
        from .heating_schedule import night_temp

        return night_temp(self.options)

    # events
    @callback
    def _changed(self, event: Event) -> None:
        if self._stopped or not self.enabled:
            return
        entity_id = event.data.get("entity_id", "")
        old, new = event.data.get("old_state"), event.data.get("new_state")
        if (not entity_id.startswith("binary_sensor.") or new is None or new.state == "on"
                or (old is not None and old.state == new.state)):
            return
        if not any(st is not None and st.attributes.get("device_class") in WINDOW_CLASSES for st in (old, new)):
            return
        self._told.discard(entity_id)
        self._gave_up.discard(entity_id)
        self.hass.async_create_task(self.async_check(), eager_start=False)

    @callback
    def _tick(self, now: datetime) -> None:
        if not self._stopped and (self.enabled or self.down):
            self.hass.async_create_task(self.async_check(), eager_start=False)

    async def async_check(self, now: datetime | None = None) -> None:
        # The next scheduled check reconciles fresh states if one is in progress.
        if self._check_lock.locked():
            return
        async with self._check_lock:
            if self._stopped:
                return
            now = now or dt_util.utcnow()
            await self._put_back(now, force=not self.enabled)
            if self.enabled:
                await self._inspect_windows(now)

    async def _inspect_windows(self, now: datetime) -> None:
        for window in self._open_long_enough(now):
            if self._stopped or not self.enabled:
                return
            if window.entity_id in self._gave_up:
                continue
            area_id = self._area(window.entity_id)
            room = self._room_heaters(area_id)
            for heater in room:
                if self._stopped or not self.enabled:
                    return
                if heater not in self.down:
                    await self._turn_down(heater, window, area_id, now)
                elif window.entity_id not in self.down[heater]["windows"]:
                    self.down[heater]["windows"].append(window.entity_id)
                    self._save()
            others = [h for h in self._heaters() if h not in room and self._warming(self.hass.states.get(h))]
            if others and window.entity_id not in self._told:
                self._told.add(window.entity_id)
                await self._tell(window, now)

    async def _turn_down(self, heater: str, window: Any, area_id: str | None, now: datetime) -> None:
        st = self.hass.states.get(heater)
        if st is None or st.state in ("off", "unavailable", "unknown"):
            return
        was = _num(st.attributes.get("temperature"))
        night = self._night()
        if was is None or was <= night:
            return  # already as low as it would go
        from .own import own_context

        try:
            await self.hass.services.async_call("climate", "set_temperature", {"entity_id": heater, "temperature": night},
                                                blocking=True, context=own_context(self.hass, "an open window"))
        except Exception as err:  # noqa: BLE001
            _LOGGER.debug("Couldn't turn %s down for an open window: %s", heater, err)
            return
        self.down[heater] = {"was": was, "set": night, "since": now.isoformat(), "area": area_id,
                             "windows": [window.entity_id]}
        self._save()
        rt = self._rt()
        if rt is not None:
            rt.timeline.add("heating", f"{st.name}: turned down to {night:g}° — {window.name} open",
                            f"was {was:g}°; back up when the window's shut")

    async def _tell(self, window: Any, now: datetime) -> None:
        rt = self._rt()
        if rt is None:
            return
        minutes = max(1, round((now - window.last_changed).total_seconds() / 60))
        text = f"The {window.name} has been open {minutes} minutes and the heating's on."
        try:
            await rt.speech.async_say(text, "normal", None, source="window heating")
        except Exception as err:  # noqa: BLE001
            _LOGGER.debug("Couldn't mention the open window: %s", err)

    async def _put_back(self, now: datetime, *, force: bool = False) -> None:
        from .never import blocked
        from .own import own_context

        if not self.down:
            return
        # A second window may have opened just before the first one closed. Its
        # setback delay only applies when starting a hold, not when ending one.
        open_by_room: dict[str, set[str]] = {}
        for window in self._windows():
            if window.state == "on" and (area := self._area(window.entity_id)):
                open_by_room.setdefault(area, set()).add(window.entity_id)
        for heater, record in list(self.down.items()):
            if self._stopped:
                return
            tracked_open = {eid for eid in record.get("windows") or []
                            if (window := self.hass.states.get(eid)) is not None and window.state == "on"}
            still_open = tracked_open | open_by_room.get(record.get("area"), set())
            since = dt_util.parse_datetime(record.get("since") or "")
            too_long = since is not None and now - since >= LONGEST
            if still_open and not (too_long or force):
                continue
            st = self.hass.states.get(heater)
            if st is None or st.state in ("unavailable", "unknown"):
                continue
            have = _num(st.attributes.get("temperature"))
            if blocked(self.hass, heater) or st.state == "off" or (have is not None and abs(have - float(record["set"])) > 0.3):
                self.down.pop(heater)
                self._save()
                continue  # ownership has changed
            if have is None:
                continue
            if too_long:
                self._gave_up.update(still_open)
            rt = self._rt()
            target = None
            if rt is not None and getattr(rt, "heat_prog", None) is not None:
                target = rt.heat_prog.target_now(heater)  # on the schedule: what it wants by now
            target = target if target is not None else float(record["was"])
            why = ("window heating disabled" if force else
                   "three-hour window limit" if too_long and still_open else "window shut")
            try:
                await self.hass.services.async_call(
                    "climate", "set_temperature", {"entity_id": heater, "temperature": target}, blocking=True,
                    context=own_context(self.hass, why))
            except Exception as err:  # noqa: BLE001
                _LOGGER.debug("Couldn't put %s back after the window: %s", heater, err)
                continue
            self.down.pop(heater)
            self._save()
            if rt is not None:
                rt.timeline.add("heating", f"{st.name}: requested return to {target:g}°", why)

    async def async_disable(self) -> None:
        """Restore outstanding setbacks. Keep failed restores for the next check."""
        async with self._check_lock:
            await self._put_back(dt_util.utcnow(), force=True)
            self._told.clear()
            self._gave_up.clear()

    def _save(self) -> None:
        self._store.async_delay_save(lambda: {"down": self.down}, 2)
