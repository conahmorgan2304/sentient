"""Left open: doors and windows open too long, and a check when everyone goes out.

- A door or window open longer than a set time gets one spoken nudge ("The back door's
  been open for 15 minutes"), then again every half hour while it stays open. Quiet
  hours, hush and shushes apply as normal. No AI.
- When the last person leaves, anything open or unlocked goes to phones as a security
  note — so nobody needs to come back to check.
"""

from __future__ import annotations

from datetime import datetime, timedelta
import logging
from typing import Any

from homeassistant.const import EVENT_STATE_CHANGED
from homeassistant.core import Event, HomeAssistant, callback
from homeassistant.helpers.event import async_track_time_interval
from homeassistant.util import dt as dt_util

from .const import CONF_LEAVING_CHECK, CONF_LEFT_OPEN_IGNORE, CONF_LEFT_OPEN_MINUTES
from .security import OPEN_CLASSES, check

_LOGGER = logging.getLogger(__name__)
REPEAT = timedelta(minutes=30)


class LeftOpen:
    def __init__(self, hass: HomeAssistant, options: dict[str, Any], rt_getter) -> None:
        self.hass = hass
        self.options = options
        self._rt = rt_getter
        self._told: dict[str, datetime] = {}
        self._unsubs: list = []

    @callback
    def async_start(self) -> None:
        self._unsubs.append(async_track_time_interval(self.hass, self._tick, timedelta(minutes=1)))

        @callback
        def _people(data) -> bool:
            return data["entity_id"].startswith("person.")

        self._unsubs.append(self.hass.bus.async_listen(EVENT_STATE_CHANGED, self._person, event_filter=_people))

    @callback
    def async_stop(self) -> None:
        while self._unsubs:
            self._unsubs.pop()()

    @callback
    def _tick(self, now: datetime) -> None:
        minutes = float(self.options.get(CONF_LEFT_OPEN_MINUTES) or 0)
        if minutes <= 0:
            return
        ignore = set(self.options.get(CONF_LEFT_OPEN_IGNORE) or [])
        utcnow = dt_util.utcnow()
        still_open = set()
        for st in self.hass.states.async_all("binary_sensor"):
            if st.state != "on" or st.attributes.get("device_class") not in OPEN_CLASSES or st.entity_id in ignore:
                continue
            still_open.add(st.entity_id)
            open_for = utcnow - st.last_changed
            if open_for < timedelta(minutes=minutes):
                continue
            last = self._told.get(st.entity_id)
            if last and utcnow - last < REPEAT:
                continue
            self._told[st.entity_id] = utcnow
            mins = int(open_for.total_seconds() // 60)
            span = f"{mins // 60} hour{'s' if mins >= 120 else ''}" if mins >= 60 else f"{mins} minutes"
            text = f"{st.name} has been open for {span}."
            self.hass.async_create_task(self._rt().speech.async_say(text, "normal", source="left_open"))
        self._told = {k: v for k, v in self._told.items() if k in still_open}

    @callback
    def _person(self, event: Event) -> None:
        if not self.options.get(CONF_LEAVING_CHECK, True):
            return
        old, new = event.data["old_state"], event.data["new_state"]
        if old is None or new is None or old.state != "home" or new.state == "home":
            return
        if any(st.state == "home" for st in self.hass.states.async_all("person")):
            return  # someone's still in
        report = check(self.hass)
        if report["secure"]:
            return
        self._rt().timeline.add("security", report["summary"], "when the last person left")
