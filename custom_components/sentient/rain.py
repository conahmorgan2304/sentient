"""Rain on the way: a word when rain's due within the hour and a window or door is open.

Every ten minutes, while something chosen is open (or any window or door, if none are chosen), the
hourly forecast is read — a plain Home Assistant call, no AI. If rain is due in the next hour: "Rain's
on the way in the next hour, and the bedroom window's open." Once per spell of rain, never in quiet
hours (it's held like any other remark). Off by default.
"""

from __future__ import annotations

import asyncio
from datetime import datetime, timedelta
import logging
from typing import Any, Callable

from homeassistant.core import HomeAssistant, callback
from homeassistant.helpers.event import async_track_time_interval
from homeassistant.util import dt as dt_util

from .const import CONF_BRIEFING_WEATHER, CONF_RAIN, CONF_RAIN_ENTITIES
from .security import OPEN_CLASSES
from .numeric import finite_float

_LOGGER = logging.getLogger(__name__)

WET = {"rainy", "pouring", "lightning-rainy", "hail", "snowy-rainy", "snowy"}
FORECAST_TIMEOUT = 10
LIKELY = 60  # % chance of rain that counts, when the forecast gives one
AGAIN_AFTER = timedelta(hours=3)


class Rain:
    def __init__(self, hass: HomeAssistant, options: dict[str, Any], rt_getter: Callable[[], Any]) -> None:
        self.hass = hass
        self.options = options
        self._rt = rt_getter
        self._last_said: datetime | None = None
        self._unsub = None
        self._generation = 0
        self._stopped = False
        self._lock = asyncio.Lock()

    async def async_start(self) -> None:
        self._stopped = False
        self._unsub = async_track_time_interval(self.hass, self._tick, timedelta(minutes=10))

    @callback
    def async_stop(self) -> None:
        self._stopped = True
        self._generation += 1
        if self._unsub:
            self._unsub()
            self._unsub = None

    @property
    def enabled(self) -> bool:
        return bool(self.options.get(CONF_RAIN, False))

    def open_now(self) -> list[str]:
        chosen = self.options.get(CONF_RAIN_ENTITIES) or []
        states = [self.hass.states.get(e) for e in chosen] if chosen else [
            s for s in self.hass.states.async_all("binary_sensor") if s.attributes.get("device_class") in OPEN_CLASSES]
        return [s.name for s in states if s is not None and s.state == "on"]

    @callback
    def _tick(self, _now: datetime) -> None:
        if self.enabled and self.open_now():
            self.hass.async_create_task(self.async_check(), eager_start=False)

    async def async_rain_due(self) -> bool:
        weather_id = self.options.get(CONF_BRIEFING_WEATHER) or next(
            (s.entity_id for s in self.hass.states.async_all("weather")), None)
        if not weather_id:
            return False
        try:
            async with asyncio.timeout(FORECAST_TIMEOUT):
                resp = await self.hass.services.async_call(
                    "weather", "get_forecasts", {"entity_id": weather_id, "type": "hourly"},
                    blocking=True, return_response=True,
                )
        except Exception as err:  # noqa: BLE001 - no hourly forecast: nothing to say
            _LOGGER.debug("No hourly forecast: %s", err)
            return False
        now = dt_util.utcnow()
        data = resp.get(weather_id) if isinstance(resp, dict) else None
        forecast = data.get('forecast') if isinstance(data, dict) else None
        if not isinstance(forecast, list):
            return False
        for f in forecast:
            if not isinstance(f, dict):
                continue
            when = dt_util.parse_datetime(str(f.get('datetime') or ''))
            if when is None or when.tzinfo is None or not now <= when <= now + timedelta(hours=1):
                continue
            raw = f.get('precipitation_probability')
            chance = finite_float(raw)
            if raw is not None and (chance is None or not 0 <= chance <= 100):
                continue
            if f.get('condition') in WET and (chance is None or chance >= LIKELY):
                return True
            if chance is not None and chance >= 80:
                return True
        return False

    async def async_check(self, force: bool = False) -> dict[str, Any]:
        if self._lock.locked():
            return {'skipped': 'rain check already running'}
        async with self._lock:
            return await self._async_check(force)

    async def _async_check(self, force: bool) -> dict[str, Any]:
        generation, options = self._generation, self.options
        if self._stopped:
            return {'skipped': 'rain checks stopped'}
        if not self.enabled and not force:
            return {"skipped": "rain warnings are off"}
        opened = self.open_now()
        if not opened:
            return {"rain": None, "open": []}
        now = dt_util.utcnow()
        if not force and self._last_said and now - self._last_said < AGAIN_AFTER:
            return {"skipped": "already said"}
        if not await self.async_rain_due():
            return {"rain": False, "open": opened}
        if (self._stopped or generation != self._generation or self.options is not options
                or (not force and not self.enabled)):
            return {'skipped': 'rain settings changed while checking'}
        opened = self.open_now()
        if not opened:
            return {'rain': True, 'open': [], 'said': False}
        rt = self._rt()
        if rt is None:
            return {"rain": True, "open": opened}
        things = opened[0] if len(opened) == 1 else ", ".join(opened[:-1][:3]) + f" and {opened[-1]}"
        verb = "is" if len(opened) == 1 else "are"
        result = await rt.speech.async_say(
            f"Rain's on the way in the next hour, and the {things.lower()} {verb} open.", "normal", None, source="rain")
        if result.spoken:
            self._last_said = dt_util.utcnow()
        return {"rain": True, "open": opened, "said": result.spoken}
