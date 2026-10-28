"""Bins check: on the evening before collection, one look to see whether the bins are out.

At the chosen time, the bin calendar (a waste-collection calendar, or any calendar with "bin",
"recycling", "refuse" or "waste" in the event) is read for tomorrow. If there's a collection, one AI
look at the "bins" spot: are they out? Only if they aren't is anything said — "Recycling tomorrow, and
the bins aren't out yet." Looked at once more an hour later if they're still in. Off by default.
"""

from __future__ import annotations

from datetime import datetime, timedelta
import logging
import re
from typing import Any, Callable

from homeassistant.core import HomeAssistant, callback
from homeassistant.helpers.event import async_track_time_change
from homeassistant.util import dt as dt_util

from .ai import async_generate
from .const import (
    CONF_AI_TASK_ENTITY,
    CONF_BINS_CALENDAR,
    CONF_BINS_CHECK,
    CONF_BINS_SPOT,
    CONF_BINS_TIME,
    CONF_BRIEFING_CALENDARS,
    DEFAULT_BINS_SPOT,
    DEFAULT_BINS_TIME,
)

_LOGGER = logging.getLogger(__name__)

BIN_WORDS = re.compile(r"bin|recycl|refuse|waste|rubbish|garden|food|collection|general", re.I)
AGAIN_AFTER = timedelta(hours=1)


class Bins:
    def __init__(self, hass: HomeAssistant, options: dict[str, Any], rt_getter: Callable[[], Any]) -> None:
        self.hass = hass
        self.options = options
        self._rt = rt_getter
        self.last: dict[str, Any] = {}
        self._again: datetime | None = None
        self._again_day: str | None = None
        self._unsub = None

    async def async_start(self) -> None:
        self._unsub = async_track_time_change(self.hass, self._tick, second=20)

    @callback
    def async_stop(self) -> None:
        if self._unsub:
            self._unsub()
            self._unsub = None

    def calendars(self) -> list[str]:
        if chosen := self.options.get(CONF_BINS_CALENDAR):
            return [chosen] if isinstance(chosen, str) else list(chosen)
        cals = self.options.get(CONF_BRIEFING_CALENDARS) or [s.entity_id for s in self.hass.states.async_all("calendar")]
        return [c for c in cals if BIN_WORDS.search(c)]

    def spot(self) -> dict[str, Any] | None:
        rt = self._rt()
        if rt is None:
            return None
        key = rt.spots.find_any(self.options.get(CONF_BINS_SPOT) or DEFAULT_BINS_SPOT)
        return rt.spots.spots.get(key or "")

    @callback
    def _tick(self, now: datetime) -> None:
        if not self.options.get(CONF_BINS_CHECK):
            return
        local = dt_util.as_local(now)
        at = str(self.options.get(CONF_BINS_TIME) or DEFAULT_BINS_TIME)[:5]
        if f"{local:%H:%M}" == at or (self._again and now >= self._again):
            self._again = None
            self.hass.async_create_task(self.async_check(), eager_start=False)

    async def async_collection_tomorrow(self) -> str | None:
        """What's collected tomorrow ("Recycling"), from the bin calendar; None if nothing."""
        start = (dt_util.now() + timedelta(days=1)).replace(hour=0, minute=0, second=0, microsecond=0)
        for cal in self.calendars():
            try:
                resp = await self.hass.services.async_call(
                    "calendar", "get_events",
                    {"entity_id": cal, "start_date_time": start.isoformat(), "end_date_time": (start + timedelta(days=1)).isoformat()},
                    blocking=True, return_response=True,
                )
            except Exception as err:  # noqa: BLE001
                _LOGGER.debug("Bins: couldn't read %s: %s", cal, err)
                continue
            names = [str(e.get("summary") or "") for e in (resp or {}).get(cal, {}).get("events", [])]
            dedicated = BIN_WORDS.search(cal) is not None
            names = [n for n in names if n and (dedicated or BIN_WORDS.search(n))]
            if names:
                return " and ".join(dict.fromkeys(names))
        return None

    async def async_check(self, force: bool = False) -> dict[str, Any]:
        if not force and not self.options.get(CONF_BINS_CHECK):
            return {"skipped": "the bins check is off"}
        rt, spot = self._rt(), self.spot()
        what = await self.async_collection_tomorrow()
        if not what:
            return {"collection": None}
        if rt is None or spot is None:
            return {"collection": what, "error": "No 'bins' spot: draw one on the Spots tab where the bins go out"}
        if not rt.watches.spend("bins", force=force):
            return {"collection": what, "skipped": "daily AI limit reached"}
        from .spots import _key

        data: dict[str, Any] = {
            "task_name": "Sentient: are the bins out?",
            "instructions": ("This picture shows where a house puts its wheelie bins out for collection. Are the "
                             "bins out, ready for collection? Set out true if at least one bin is clearly put out. "
                             "If it's too dark to tell, set unsure true."),
            "structure": {
                "out": {"description": "Bins are out", "required": True, "selector": {"boolean": {}}},
                "unsure": {"description": "Too dark or unclear", "required": False, "selector": {"boolean": {}}},
            },
            "attachments": [await rt.spots.async_attachment(spot["camera"], _key(spot["name"]))],
        }
        if ai := self.options.get(CONF_AI_TASK_ENTITY):
            data["entity_id"] = ai
        try:
            result = ((await async_generate(self.hass, data)) or {}).get("data") or {}
        except Exception as err:  # noqa: BLE001
            return {"collection": what, "error": str(err)}
        out = isinstance(result, dict) and result.get("out") in (True, "true", "yes")
        unsure = isinstance(result, dict) and result.get("unsure") in (True, "true", "yes")
        self.last = {"time": dt_util.utcnow().isoformat(), "collection": what, "out": out}
        if out or unsure:
            return {"collection": what, "out": out, "unsure": unsure}
        text = f"{what} tomorrow, and the bins aren't out yet."
        rt.timeline.add("watch", text, "bins check")
        said = await rt.speech.async_say(text, "normal", None, source="bins")
        today = dt_util.now().date().isoformat()
        if not force and self._again_day != today:
            self._again_day = today
            self._again = dt_util.utcnow() + AGAIN_AFTER  # one more look later, once
        return {"collection": what, "out": False, "said": said.spoken}
