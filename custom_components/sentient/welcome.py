"""Welcome home: when someone gets back, a short word on what happened while they were out.

Triggered by a person entity coming home after being away a while. It waits a moment
for them to get inside, then speaks in the busiest room: "Welcome back" — plus, if
anything notable happened (visitors, watches, problems), one or two sentences from the
timeline written by the AI. No names, nothing in quiet hours, hush or guest mode.
"""

from __future__ import annotations

from datetime import datetime, timedelta
import logging
from typing import Any, Callable

from homeassistant.core import Event, EventStateChangedData, HomeAssistant, callback
from homeassistant.const import EVENT_STATE_CHANGED
from homeassistant.helpers.event import async_call_later
from homeassistant.util import dt as dt_util

from .const import personality_line, CONF_AI_TASK_ENTITY, CONF_WELCOME, CONF_WELCOME_PEOPLE

_LOGGER = logging.getLogger(__name__)

MIN_AWAY = timedelta(minutes=20)
SETTLE_SECONDS = 75
ONCE_PER = timedelta(minutes=10)
NOTABLE = ["visitor", "watch", "health"]


class Welcome:
    def __init__(self, hass: HomeAssistant, options: dict[str, Any], rt_getter: Callable[[], Any]) -> None:
        self.hass = hass
        self.options = options
        self._rt = rt_getter
        self._unsub = None
        self._last: datetime | None = None
        self._pending = None

    @callback
    def async_start(self) -> None:
        self.async_stop()
        if not self.options.get(CONF_WELCOME):
            return
        people = set(self.options.get(CONF_WELCOME_PEOPLE) or [])

        @callback
        def _wanted(event_data) -> bool:
            entity_id = event_data["entity_id"]
            return entity_id in people if people else entity_id.startswith("person.")

        self._unsub = self.hass.bus.async_listen(EVENT_STATE_CHANGED, self._changed, event_filter=_wanted)

    @callback
    def async_stop(self) -> None:
        if self._unsub:
            self._unsub()
            self._unsub = None

    @callback
    def _changed(self, event: Event[EventStateChangedData]) -> None:
        old, new = event.data["old_state"], event.data["new_state"]
        if old is None or new is None or new.state != "home" or old.state in ("home", "unknown", "unavailable"):
            return
        away_since = old.last_changed
        if dt_util.utcnow() - away_since < MIN_AWAY:
            return
        if self._last and dt_util.utcnow() - self._last < ONCE_PER:
            return  # someone else just got the welcome — arriving together
        self._last = dt_util.utcnow()

        @callback
        def _go(_now: datetime) -> None:
            self.hass.async_create_background_task(self.async_welcome(away_since), "sentient_welcome")

        async_call_later(self.hass, SETTLE_SECONDS, _go)

    async def async_welcome(self, since: datetime) -> dict[str, Any]:
        rt = self._rt()
        speech = rt.speech
        if speech.guest_mode or speech.quiet_now():
            return {"spoken": False, "reason": "quiet"}
        hours = max(0.25, (dt_util.utcnow() - since).total_seconds() / 3600)
        items = list(reversed(rt.timeline.since(hours, NOTABLE, 30)))
        message = "Welcome back."
        if items:
            lines = [
                f"{dt_util.as_local(dt_util.parse_datetime(i['time'])):%H:%M} {i['text']}" for i in items
            ]
            data: dict[str, Any] = {
                "task_name": "Sentient: welcome home",
                "instructions": (
                    "Someone has just come home. In at most two short spoken sentences, starting with "
                    "'Welcome back.', tell them what's worth knowing from this log of while they were out "
                    "(visitors, parcels, things that finished, problems). No names, no times unless useful, "
                    "British English. Never say or imply the house was empty. " + personality_line(self.options)
                    + "\n\n" + "\n".join(lines)
                ),
            }
            if ai := self.options.get(CONF_AI_TASK_ENTITY):
                data["entity_id"] = ai
            try:
                rt.watches.spend()
                response = await self.hass.services.async_call(
                    "ai_task", "generate_data", data, blocking=True, return_response=True
                )
                text = str((response or {}).get("data") or "").strip()
                if text:
                    message = text
            except Exception as err:  # noqa: BLE001 - a plain welcome is fine
                _LOGGER.debug("Welcome summary failed: %s", err)
        result = await speech.async_say(message, "normal", source="welcome")
        return result.as_dict()
