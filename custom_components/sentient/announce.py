"""Announcements: said straight away when something happens — the doorbell, the gate opening.

Set up on the Settings page: what to watch, what to say (a few ways, picked at random), on which
speakers, whether it gets through quiet hours, and a door that means "someone's just going out"
(the gate opening within half a minute of the back door isn't news). Afterwards the house keeps
other chatter back for a minute so nothing talks over it.
"""

from __future__ import annotations

import asyncio
from datetime import timedelta
import logging
from typing import Any, Callable

from homeassistant.const import EVENT_STATE_CHANGED
from homeassistant.core import Event, HomeAssistant, callback
from homeassistant.util import dt as dt_util

from .items import Items
from .kinds import variant
from .shortcuts import async_run_actions

_LOGGER = logging.getLogger(__name__)
_EVENTISH = ("event", "input_button", "button", "scene")
HOLD_AFTER = 60  # seconds of quiet after an announcement


def fired(new, old, to_state: str | None) -> bool:
    if new is None or old is None or old.state in ("unavailable", "unknown") or new.state in ("unavailable", "unknown"):
        return False
    if new.domain in _EVENTISH:
        return new.state != old.state
    want = (to_state or "on").strip()
    return new.state == want and old.state != want


class Announcements:
    def __init__(self, hass: HomeAssistant, items: Items, runtime: Callable[[], Any]) -> None:
        self.hass = hass
        self.items = items
        self._rt = runtime
        self._unsub = None
        self._event_unsubs: dict[str, Any] = {}

    @callback
    def async_start(self) -> None:
        self._unsub = self.hass.bus.async_listen(EVENT_STATE_CHANGED, self._changed)
        self.items.listeners.append(self._resubscribe)
        self._resubscribe()

    @callback
    def async_stop(self) -> None:
        if self._unsub:
            self._unsub()
            self._unsub = None
        for unsub in self._event_unsubs.values():
            unsub()
        self._event_unsubs.clear()
        if self._resubscribe in self.items.listeners:
            self.items.listeners.remove(self._resubscribe)

    @callback
    def _resubscribe(self) -> None:
        """Listen for the events the announcements use (e.g. doorbell_pressed)."""
        wanted = {str(i["event_type"]).strip() for i in self.items.enabled() if str(i.get("event_type") or "").strip()}
        for et in list(self._event_unsubs):
            if et not in wanted:
                self._event_unsubs.pop(et)()
        for et in wanted - set(self._event_unsubs):
            @callback
            def _on_event(event: Event, et: str = et) -> None:
                for item in self.items.enabled():
                    if str(item.get("event_type") or "").strip() == et:
                        self._fire(item)

            self._event_unsubs[et] = self.hass.bus.async_listen(et, _on_event)

    @callback
    def _changed(self, event: Event) -> None:
        entity_id = event.data.get("entity_id")
        for item in self.items.enabled():
            if not item.get("event_type") and item.get("entity") == entity_id and fired(event.data.get("new_state"), event.data.get("old_state"), item.get("to_state")):
                self._fire(item)

    @callback
    def _fire(self, item: dict[str, Any]) -> None:
        rt = self._rt()
        now = dt_util.utcnow()
        last = dt_util.parse_datetime(item.get("last") or "")
        if last and now - last < timedelta(seconds=float(item.get("cooldown_seconds") or 0)):
            return
        if skip := item.get("skip_entity"):
            st = self.hass.states.get(skip)
            if st is not None and now - st.last_changed <= timedelta(seconds=float(item.get("skip_seconds") or 0)):
                rt.timeline.add("noticed", f"{item['name']}: not announced", f"{st.name} had just changed — someone going out")
                self.items.update(item["id"], last=now.isoformat())
                return
        self.items.update(item["id"], last=now.isoformat())
        self.hass.async_create_task(self.async_announce(item))

    async def async_announce(self, item: dict[str, Any]) -> dict[str, Any]:
        rt = self._rt()
        message = variant(item.get("messages")) or item["name"]
        speakers = [s for s in item.get("speakers") or [] if self.hass.states.get(s)] or None
        priority = "urgent" if item.get("through_quiet") else "normal"
        tasks = []
        if item.get("actions"):
            tasks.append(self.hass.async_create_task(self._actions(item)))
        result = await rt.speech.async_say(message, priority, speakers, source=f"announce:{item['name']}")
        rt.speech.hold(HOLD_AFTER)  # nothing else chatters over it for a minute
        for t in tasks:
            await t
        return result.as_dict()

    async def _actions(self, item: dict[str, Any]) -> None:
        await asyncio.sleep(1.5)  # let the speaker start talking first; switching screens can hold it up
        try:
            await async_run_actions(self.hass, item["actions"], item["name"])
        except Exception as err:  # noqa: BLE001
            _LOGGER.warning("Announcement %s: actions failed: %s", item["name"], err)
