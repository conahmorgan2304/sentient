"""House health: what's offline, what's running flat, what wants updating.

A local check every few minutes — no AI. Devices are reported as a whole
("Hallway tablet is offline") rather than entity by entity, and only once
every one of their entities has been unavailable for a while, so a brief
blip doesn't count.
"""

from __future__ import annotations

from datetime import datetime, timedelta
from typing import Any

from homeassistant.const import STATE_UNAVAILABLE
from homeassistant.core import HomeAssistant, callback
from homeassistant.helpers import device_registry as dr
from homeassistant.helpers import entity_registry as er
from homeassistant.helpers.event import async_track_time_interval
from homeassistant.util import dt as dt_util

from .const import (
    CONF_BATTERY_THRESHOLD,
    CONF_HEALTH_IGNORE,
    DEFAULT_BATTERY_THRESHOLD,
    EVENT_HEALTH_CHANGED,
)

OFFLINE_AFTER = timedelta(minutes=10)
CHECK_EVERY = timedelta(minutes=5)


class HouseHealth:
    """Keeps a short list of problems worth knowing about."""

    def __init__(self, hass: HomeAssistant, options: dict[str, Any]) -> None:
        self.hass = hass
        self.options = options
        self.issues: list[dict[str, Any]] = []
        self.updates: list[str] = []
        self._unsub = None

    @callback
    def async_start(self) -> None:
        self._unsub = async_track_time_interval(self.hass, self._tick, CHECK_EVERY)
        self.hass.loop.call_soon(self.async_check)

    @callback
    def async_stop(self) -> None:
        if self._unsub:
            self._unsub()
            self._unsub = None

    @callback
    def _tick(self, _now: datetime) -> None:
        self.async_check()

    @callback
    def async_check(self) -> list[dict[str, Any]]:
        ignore = set(self.options.get(CONF_HEALTH_IGNORE) or [])
        threshold = float(self.options.get(CONF_BATTERY_THRESHOLD, DEFAULT_BATTERY_THRESHOLD))
        now = dt_util.utcnow()
        ent_reg = er.async_get(self.hass)
        dev_reg = dr.async_get(self.hass)

        # devices whose every (non-diagnostic) entity has been unavailable for a while
        by_device: dict[str, list] = {}
        for entry in ent_reg.entities.values():
            if entry.device_id and not entry.disabled_by and entry.entity_category is None:
                by_device.setdefault(entry.device_id, []).append(entry.entity_id)
        offline = []
        for device_id, entity_ids in by_device.items():
            if device_id in ignore or any(e in ignore for e in entity_ids):
                continue
            states = [self.hass.states.get(e) for e in entity_ids]
            states = [s for s in states if s is not None]
            if not states or any(s.state != STATE_UNAVAILABLE for s in states):
                continue
            since = min(s.last_changed for s in states)
            if now - since < OFFLINE_AFTER:
                continue
            device = dev_reg.async_get(device_id)
            if device is None or device.disabled_by:
                continue
            name = device.name_by_user or device.name or entity_ids[0]
            offline.append({"kind": "offline", "name": name, "since": since.isoformat(), "device_id": device_id})

        # batteries — one line per device (the lowest reading)
        low_by_device: dict[str, dict[str, Any]] = {}
        for st in self.hass.states.async_all(("sensor", "binary_sensor")):
            if st.entity_id in ignore or st.attributes.get("device_class") != "battery":
                continue
            if st.domain == "sensor":
                try:
                    level = float(st.state)
                except ValueError:
                    continue
                if level >= threshold:
                    continue
                item = {"kind": "battery", "name": st.name, "level": round(level), "entity_id": st.entity_id}
            elif st.state == "on":
                item = {"kind": "battery", "name": st.name, "level": None, "entity_id": st.entity_id}
            else:
                continue
            entry = ent_reg.async_get(st.entity_id)
            if entry and entry.device_id and entry.device_id in ignore:
                continue
            key = (entry.device_id if entry and entry.device_id else st.entity_id)
            have = low_by_device.get(key)
            if have is None or (item["level"] is not None and (have["level"] is None or item["level"] < have["level"])):
                low_by_device[key] = item
        low = list(low_by_device.values())

        self.updates = sorted(
            s.name for s in self.hass.states.async_all("update") if s.state == "on" and s.entity_id not in ignore
        )
        issues = sorted(offline, key=lambda i: i["since"]) + sorted(low, key=lambda i: (i["level"] is None, i["level"] or 0))
        if issues != self.issues:
            self.issues = issues
            self.hass.bus.async_fire(EVENT_HEALTH_CHANGED, {"count": len(issues)})
        return issues

    def describe(self) -> list[str]:
        out = []
        for i in self.issues:
            if i["kind"] == "offline":
                out.append(f"{i['name']} is offline")
            elif i["level"] is not None:
                out.append(f"{i['name']} is at {i['level']}%")
            else:
                out.append(f"{i['name']} is low")
        return out
