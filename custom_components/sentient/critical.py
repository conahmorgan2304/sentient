"""Critical alerts: smoke, carbon monoxide, gas, leaks — always said, wherever people are.

Found automatically by device class, no list to keep up to date. They break through
quiet hours, hush and guest mode, go to phones, and repeat a couple of times while the
sensor stays on.
"""

from __future__ import annotations

from datetime import datetime
import logging
from typing import Any

from homeassistant.const import EVENT_STATE_CHANGED
from homeassistant.core import Event, HomeAssistant, callback
from homeassistant.helpers import area_registry as ar
from homeassistant.helpers.event import async_call_later

from .const import CONF_CRITICAL, CONF_CRITICAL_EXTRA, CONF_CRITICAL_IGNORE
from .presence import area_of

_LOGGER = logging.getLogger(__name__)

WORDS = {
    "smoke": "Smoke detected",
    "carbon_monoxide": "Carbon monoxide detected",
    "gas": "Gas detected",
    "moisture": "Water leak detected",
    "safety": "Safety alarm",
    "tamper": "Tamper alarm",
}


def is_active(st) -> bool:
    if st is None:
        return False
    if st.domain == "alarm_control_panel":
        return st.state == "triggered"
    return st.state == "on"
REPEAT_SECONDS = 120
REPEATS = 2


class CriticalAlerts:
    def __init__(self, hass: HomeAssistant, options: dict[str, Any], rt_getter) -> None:
        self.hass = hass
        self.options = options
        self._rt = rt_getter
        self._unsub = None
        self._timers: dict[str, Any] = {}

    @callback
    def async_start(self) -> None:
        @callback
        def _wanted(data) -> bool:
            new = data.get("new_state")
            if new is None:
                return False
            entity_id = data["entity_id"]
            if entity_id in (self.options.get(CONF_CRITICAL_EXTRA) or []):
                return True
            if entity_id.startswith("alarm_control_panel."):
                return True
            return entity_id.startswith("binary_sensor.") and new.attributes.get("device_class") in WORDS

        self._unsub = self.hass.bus.async_listen(EVENT_STATE_CHANGED, self._changed, event_filter=_wanted)

    @callback
    def async_stop(self) -> None:
        if self._unsub:
            self._unsub()
            self._unsub = None
        for unsub in self._timers.values():
            unsub()
        self._timers.clear()

    def message(self, entity_id: str) -> str:
        st = self.hass.states.get(entity_id)
        if st is not None and st.domain == "alarm_control_panel":
            return f"The {st.name.lower() if st.name else 'alarm'} has gone off!"
        if st is not None and st.attributes.get("device_class") not in WORDS:
            return f"Alert: {st.name}!"
        base = WORDS.get(st.attributes.get("device_class") if st else "", "Alarm")
        area = area_of(self.hass, entity_id)
        area_entry = ar.async_get(self.hass).async_get_area(area) if area else None
        where = area_entry.name if area_entry else (st.name if st else entity_id)
        return f"{base} in the {where.lower()}!" if area_entry else f"{base}: {where}!"

    @callback
    def _changed(self, event: Event) -> None:
        entity_id = event.data["entity_id"]
        old, new = event.data["old_state"], event.data["new_state"]
        if not self.options.get(CONF_CRITICAL, True) or entity_id in (self.options.get(CONF_CRITICAL_IGNORE) or []):
            return
        if not is_active(new):
            if (unsub := self._timers.pop(entity_id, None)) is not None:
                unsub()
            if is_active(old):
                st = self.hass.states.get(entity_id)
                self._rt().timeline.add("critical", f"{st.name if st else entity_id} has cleared")
            return
        if old is None or is_active(old) or old.state in ("unavailable", "unknown"):
            return  # not a fresh alarm (or just a sensor coming back)
        self._raise(entity_id, REPEATS)

    @callback
    def _raise(self, entity_id: str, repeats_left: int) -> None:
        st = self.hass.states.get(entity_id)
        if not is_active(st):
            return
        text = self.message(entity_id)
        rt = self._rt()
        if repeats_left == REPEATS:
            rt.timeline.add("critical", text, st.name)
        self.hass.async_create_task(self._say(rt, text))

        if repeats_left > 0:
            @callback
            def _again(_now: datetime) -> None:
                self._timers.pop(entity_id, None)
                self._raise(entity_id, repeats_left - 1)

            self._timers[entity_id] = async_call_later(self.hass, REPEAT_SECONDS, _again)

    async def async_test(self) -> dict[str, Any]:
        """Say a test alert on every speaker (from the Settings page)."""
        rt = self._rt()
        await self._say(rt, "This is a test of the safety alert. Nothing is wrong.")
        rt.timeline.add("critical", "Safety alert tested", "from the settings page")
        return {"tested": True}

    async def _say(self, rt, text: str) -> None:
        speech = rt.speech
        # everywhere, not just where people seem to be
        # safety first: Echos too, even when they're switched off for everyday talk
        speakers = list(dict.fromkeys(list(speech.options.get("speakers") or []) + list(speech.options.get("fallback_speakers") or [])))
        try:
            await speech.async_say(text, "urgent", speakers or None, source="critical")
        except Exception as err:  # noqa: BLE001
            _LOGGER.warning("Critical alert could not be spoken: %s", err)
