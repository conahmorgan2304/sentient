"""Holiday mode: the house minds itself while everyone's away.

While it's on:
* nothing is said out loud except safety alerts — what would have been said goes to the phones instead,
  with visitors and parcels at the door;
* the away lights run (copying last week's evenings) even if they're normally off;
* the chosen thermostats (none unless picked) go down to frost protection, and are put back exactly as
  they were when it ends.

It ends on the date given, by the switch or by voice, or — if that setting is on — when one of the people
comes home after everyone has been out. Plain rules, no AI.
"""

from __future__ import annotations

from datetime import datetime, timedelta
import logging
from typing import Any, Callable

from homeassistant.const import EVENT_STATE_CHANGED, STATE_HOME
from homeassistant.core import Event, HomeAssistant, callback
from homeassistant.helpers.event import async_track_time_interval
from homeassistant.helpers.storage import Store
from homeassistant.util import dt as dt_util

from .const import (
    CONF_HOLIDAY,
    CONF_HOLIDAY_END_ON_RETURN,
    CONF_HOLIDAY_FROST,
    CONF_HOLIDAY_THERMOSTATS,
    DEFAULT_HOLIDAY_FROST,
    DOMAIN,
    EVENT_MODE_CHANGED,
)
from .own import own_context

_LOGGER = logging.getLogger(__name__)
SPEAKS_ANYWAY = ("critical",)  # safety alerts still sound in an empty house (a neighbour may hear)


class Holiday:
    def __init__(self, hass: HomeAssistant, entry_id: str, options: dict[str, Any], rt_getter: Callable[[], Any]) -> None:
        self.hass = hass
        self.options = options
        self._rt = rt_getter
        self._store: Store[dict[str, Any]] = Store(hass, 1, f"{DOMAIN}.{entry_id}.holiday")
        self.on = False
        self.since: str | None = None
        self.until: str | None = None
        self.left = False  # everyone has been out since it started: coming home now means they're back
        self.heating: dict[str, dict[str, Any]] = {}  # thermostat -> how it was, to put back
        self._unsubs: list = []

    @property
    def enabled(self) -> bool:
        return bool(self.options.get(CONF_HOLIDAY, True))

    @property
    def active(self) -> bool:
        return self.enabled and self.on

    async def async_start(self) -> None:
        data = await self._store.async_load() or {}
        self.on, self.since, self.until = bool(data.get("on")), data.get("since"), data.get("until")
        self.left, self.heating = bool(data.get("left")), dict(data.get("heating") or {})
        self._unsubs = [
            self.hass.bus.async_listen(EVENT_STATE_CHANGED, self._changed),
            async_track_time_interval(self.hass, self._tick, timedelta(minutes=5)),
        ]

    @callback
    def async_stop(self) -> None:
        while self._unsubs:
            self._unsubs.pop()()

    def _save(self) -> None:
        self._store.async_delay_save(lambda: {"on": self.on, "since": self.since, "until": self.until,
                                              "left": self.left, "heating": self.heating}, 1)

    # ----- on and off ---------------------------------------------------------------------
    async def async_begin(self, until: datetime | None = None) -> dict[str, Any]:
        if not self.enabled:
            return {"error": "Holiday mode is turned off in Sentient's settings"}
        if until is not None and until <= dt_util.now():
            return {"error": "That date has already passed"}
        first = not self.on
        self.on = True
        self.since = self.since if not first else dt_util.utcnow().isoformat()
        self.until = dt_util.as_utc(until).isoformat() if until is not None else None
        if first:
            self.left = False
            await self._frost()
        self._save()
        rt = self._rt()
        if rt is not None:
            back = f" until {dt_util.as_local(until):%A %-d %B}" if until is not None else ""
            rt.timeline.add("mode", f"Holiday mode on{back}", "quiet, away lights, phones get what would have been said")
        self.hass.bus.async_fire(EVENT_MODE_CHANGED, {"holiday": True})
        return {"holiday": True, "until": self.until, "thermostats": list(self.heating)}

    async def async_end(self, why: str = "turned off") -> dict[str, Any]:
        if not self.on:
            return {"holiday": False}
        self.on, self.until, self.left = False, None, False
        restored = await self._restore()
        self._save()
        rt = self._rt()
        if rt is not None:
            rt.timeline.add("mode", "Holiday mode off", why + (f"; heating put back: {', '.join(restored)}" if restored else ""))
            if rt.away is not None and not rt.away.enabled:
                await rt.away.async_finish("holiday over")
        self.hass.bus.async_fire(EVENT_MODE_CHANGED, {"holiday": False})
        return {"holiday": False, "heating_put_back": restored}

    # ----- heating -------------------------------------------------------------------------
    async def _frost(self) -> None:
        frost = float(self.options.get(CONF_HOLIDAY_FROST, DEFAULT_HOLIDAY_FROST) or DEFAULT_HOLIDAY_FROST)
        for entity_id in self.options.get(CONF_HOLIDAY_THERMOSTATS) or []:
            st = self.hass.states.get(entity_id)
            if st is None or st.state in ("unavailable", "unknown"):
                continue
            self.heating[entity_id] = {"hvac_mode": st.state, "temperature": st.attributes.get("temperature")}
            if st.state == "off":
                continue  # already off: nothing to turn down
            try:
                await self.hass.services.async_call(
                    "climate", "set_temperature", {"entity_id": entity_id, "temperature": frost}, blocking=True,
                    context=own_context(self.hass, "holiday mode: frost protection"),
                )
            except Exception as err:  # noqa: BLE001
                _LOGGER.warning("Holiday mode couldn't turn %s down: %s", entity_id, err)

    async def _restore(self) -> list[str]:
        done = []
        for entity_id, was in list(self.heating.items()):
            st = self.hass.states.get(entity_id)
            if st is None:
                continue
            try:
                if was.get("temperature") is not None:
                    await self.hass.services.async_call(
                        "climate", "set_temperature", {"entity_id": entity_id, "temperature": was["temperature"]},
                        blocking=True, context=own_context(self.hass, "holiday over: heating put back"),
                    )
                if was.get("hvac_mode") and was["hvac_mode"] != st.state and was["hvac_mode"] not in ("unavailable", "unknown"):
                    await self.hass.services.async_call(
                        "climate", "set_hvac_mode", {"entity_id": entity_id, "hvac_mode": was["hvac_mode"]},
                        blocking=True, context=own_context(self.hass, "holiday over: heating put back"),
                    )
                done.append(st.name)
            except Exception as err:  # noqa: BLE001
                _LOGGER.warning("Holiday mode couldn't put %s back: %s", entity_id, err)
        self.heating = {}
        return done

    # ----- coming home and the end date ----------------------------------------------------
    def _people(self) -> list[str]:
        rt = self._rt()
        return rt.away.people() if rt is not None and rt.away is not None else []

    @callback
    def _changed(self, event: Event) -> None:
        if not self.active:
            return
        entity_id = event.data.get("entity_id")
        if entity_id not in self._people():
            return
        new = event.data.get("new_state")
        if new is None:
            return
        if not self.left and all((s := self.hass.states.get(p)) is None or s.state != STATE_HOME for p in self._people()):
            self.left = True
            self._save()
        elif self.left and new.state == STATE_HOME and self.options.get(CONF_HOLIDAY_END_ON_RETURN, True):
            self.hass.async_create_task(self.async_end(f"{new.name} came home"))

    @callback
    def _tick(self, now: datetime) -> None:
        if self.active and self.until and (end := dt_util.parse_datetime(self.until)) and now >= end:
            self.hass.async_create_task(self.async_end("the date it was set until"))

    def speaks(self, source: str | None) -> bool:
        """Whether something may still be said out loud on holiday."""
        return not self.active or str(source or "").startswith(SPEAKS_ANYWAY)

    def describe(self) -> dict[str, Any]:
        return {"on": self.active, "since": self.since, "until": self.until,
                "thermostats_turned_down": list(self.heating), "everyone_left": self.left}
