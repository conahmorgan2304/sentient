"""Passive Frigate telemetry for configured Sentient security cameras."""
from __future__ import annotations

from datetime import timedelta
import json
import logging

from homeassistant.auth.permissions.const import POLICY_READ
from homeassistant.core import callback
from homeassistant.util import dt as dt_util

from .clipwatch import frigate_prefix
from .incidents import configured_cameras
from .numeric import finite_float
from .security_events import normal_event

CONF_FRIGATE_HEALTH = "frigate_pipeline_health"
STALE_AFTER = timedelta(minutes=3)
_LOGGER = logging.getLogger(__name__)


class FrigateHealth:
    def __init__(self, hass, options):
        self.hass = hass
        self.options = options
        self._subscriptions = []
        self._subscribing = None
        self._generation = 0
        self.availability = None
        self.stats_at = None
        self.stats = {}
        self.events = {}

    @callback
    def async_stop(self):
        self._generation += 1
        if self._subscribing is not None:
            self._subscribing.cancel()
            self._subscribing = None
        for unsubscribe in self._subscriptions:
            unsubscribe()
        self._subscriptions.clear()
        self.availability = self.stats_at = None
        self.stats.clear()
        self.events.clear()

    @callback
    def configure(self, options):
        enabled = options.get(CONF_FRIGATE_HEALTH)
        if enabled and (self._subscriptions or self._subscribing):
            self.options = options
            return
        self.async_stop()
        self.options = options
        if enabled and self.hass.config_entries.async_entries("mqtt"):
            self._subscribing = self.hass.async_create_background_task(
                self._subscribe(self._generation), "sentient frigate pipeline health", eager_start=False
            )

    async def _subscribe(self, stamp):
        import asyncio

        from homeassistant.components import mqtt

        prefix = frigate_prefix(self.hass)

        @callback
        def received(message):
            if stamp == self._generation:
                self._received(message, message.topic.removeprefix(f"{prefix}/"))

        try:
            if not await mqtt.async_wait_for_mqtt_client(self.hass) or stamp != self._generation:
                return
            for suffix in ("available", "stats", "events"):
                unsubscribe = await mqtt.async_subscribe(self.hass, f"{prefix}/{suffix}", received)
                if stamp != self._generation:
                    unsubscribe()
                    return
                self._subscriptions.append(unsubscribe)
        except Exception as err:  # noqa: BLE001 - optional telemetry must never stop setup
            if stamp == self._generation:
                for unsubscribe in self._subscriptions:
                    unsubscribe()
                self._subscriptions.clear()
            _LOGGER.debug("Frigate health subscriptions unavailable: %s", err)
        finally:
            if self._subscribing is asyncio.current_task():
                self._subscribing = None

    @callback
    def _received(self, message, topic):
        if topic == "available":
            if message.payload in ("online", "offline", "stopped"):
                self.availability = message.payload
                if self.availability != "online":
                    self.stats_at = None
                    self.stats.clear()
            return
        if message.retain:
            return
        try:
            data = json.loads(message.payload)
        except (TypeError, ValueError):
            return
        if not isinstance(data, dict):
            return
        cameras = configured_cameras(self.hass, self.options)
        now = dt_util.utcnow()
        if topic == "stats" and isinstance(data.get("cameras"), dict):
            self.stats = {}
            for eid in cameras:
                values = data["cameras"].get(eid.removeprefix("camera."))
                if isinstance(values, dict):
                    self.stats[eid] = {key: rate for key in ("camera_fps", "process_fps", "detection_fps", "skipped_fps")
                                       if (rate := finite_float(values.get(key))) is not None and rate >= 0}
            self.stats_at = now
        elif topic == "events" and data.get("type") in ("new", "update", "end"):
            if (event := normal_event(data.get("after"))) and (eid := f"camera.{event['camera']}") in cameras:
                self.events[eid] = now

    def report(self, user):
        if not self.options.get(CONF_FRIGATE_HEALTH):
            return {"enabled": False}
        now = dt_util.utcnow()
        fresh = bool(self.availability not in ("offline", "stopped") and self.stats_at and now - self.stats_at <= STALE_AFTER)
        cameras = []
        for eid in sorted(configured_cameras(self.hass, self.options)):
            if not user.permissions.check_entity(eid, POLICY_READ):
                continue
            state = self.hass.states.get(eid)
            values = self.stats.get(eid, {}) if fresh else {}
            zero = values.get("camera_fps") == 0 or values.get("process_fps") == 0
            event = self.events.get(eid)
            cameras.append({"camera": eid, "name": state.name if state else eid,
                            "ha_report": state.state if state and not state.attributes.get("restored") else "unknown",
                            "processing": "zero_rate" if zero else "reporting" if values.get("process_fps") else "unknown",
                            "last_event_received": event.isoformat() if event else None, **values})
        availability = self.availability or "unknown"
        if availability == "online" and not fresh:
            availability = "unknown"
        return {"enabled": True, "availability": availability, "last_reported_availability": self.availability,
                "stats_fresh": fresh, "stats_at": self.stats_at.isoformat() if self.stats_at else None, "cameras": cameras}
