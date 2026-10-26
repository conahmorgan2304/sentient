"""Sensors so dashboards and automations can see what Sentient knows and says."""

from __future__ import annotations

from typing import Any

from datetime import timedelta

from homeassistant.components.sensor import SensorEntity
from homeassistant.core import Event, HomeAssistant, callback
from homeassistant.helpers import area_registry as ar
from homeassistant.helpers.device_registry import DeviceEntryType, DeviceInfo
from homeassistant.helpers.entity_platform import AddConfigEntryEntitiesCallback
from homeassistant.helpers.event import async_track_time_interval
from homeassistant.helpers.restore_state import RestoreEntity
from homeassistant.util import dt as dt_util

from . import SentientConfigEntry
from .const import DOMAIN, EVENT_HELD, EVENT_MEMORY_CHANGED, EVENT_HEALTH_CHANGED, EVENT_NOTICED, EVENT_ROUTINES_CHANGED, EVENT_SPOKE, EVENT_VISITOR, EVENT_WATCH_CHANGED


async def async_setup_entry(
    hass: HomeAssistant,
    entry: SentientConfigEntry,
    async_add_entities: AddConfigEntryEntitiesCallback,
) -> None:
    async_add_entities([MemorySensor(entry), LastSpokenSensor(entry), LikelyRoomSensor(entry), WatchesSensor(entry), RoutinesSensor(entry), VisitorsSensor(entry), HealthSensor(entry), DriveSensor(entry), MindSensor(entry), HouseSensor(entry)])


class _Base(SensorEntity):
    _attr_has_entity_name = True
    _attr_should_poll = False

    def __init__(self, entry: SentientConfigEntry, key: str) -> None:
        self._entry = entry
        self._attr_unique_id = f"{entry.entry_id}_{key}"
        self._attr_translation_key = key
        self._attr_device_info = DeviceInfo(
            identifiers={(DOMAIN, entry.entry_id)},
            name=entry.title,
            manufacturer="Sentient",
            model="Home brain",
            entry_type=DeviceEntryType.SERVICE,
        )


class MemorySensor(_Base):
    """How many things the house remembers, with the list as attributes."""

    _attr_icon = "mdi:brain"
    _attr_native_unit_of_measurement = "memories"

    def __init__(self, entry: SentientConfigEntry) -> None:
        super().__init__(entry, "memories")

    async def async_added_to_hass(self) -> None:
        self.async_on_remove(self.hass.bus.async_listen(EVENT_MEMORY_CHANGED, self._changed))

    @callback
    def _changed(self, _event: Event) -> None:
        self.async_write_ha_state()

    @property
    def native_value(self) -> int:
        return len(self._entry.runtime_data.memory.items)

    @property
    def extra_state_attributes(self) -> dict[str, Any]:
        items = sorted(self._entry.runtime_data.memory.items, key=lambda m: m.created, reverse=True)
        return {
            "items": [{"id": m.id, "text": m.text, "category": m.category} for m in items[:50]],
            "last_tidy": self._entry.runtime_data.upkeep.last_tidy,
        }


class LastSpokenSensor(_Base, RestoreEntity):
    """The last thing said, plus a short log of what was said or held back."""

    _attr_icon = "mdi:account-voice"

    def __init__(self, entry: SentientConfigEntry) -> None:
        super().__init__(entry, "last_spoken")
        self._value: str | None = None
        self._log: list[dict[str, Any]] = []

    async def async_added_to_hass(self) -> None:
        # what was said survives a restart
        if (last := await self.async_get_last_state()) is not None:
            if last.state not in ("unknown", "unavailable"):
                self._value = last.state
            log = last.attributes.get("log")
            if isinstance(log, list):
                self._log = [i for i in log if isinstance(i, dict)][:20]
        self.async_on_remove(self.hass.bus.async_listen(EVENT_SPOKE, self._spoke))
        self.async_on_remove(self.hass.bus.async_listen(EVENT_HELD, self._held))
        self.async_on_remove(self.hass.bus.async_listen(EVENT_NOTICED, self._noticed))

    @callback
    def _noticed(self, _event: Event) -> None:
        self.async_write_ha_state()

    def _push(self, item: dict[str, Any]) -> None:
        self._log = ([item] + self._log)[:20]

    @callback
    def _spoke(self, event: Event) -> None:
        self._value = event.data["message"][:255]
        self._push({"time": dt_util.now().isoformat(), "said": event.data["message"],
                    "speakers": event.data.get("speakers"), "source": event.data.get("source")})
        self.async_write_ha_state()

    @callback
    def _held(self, event: Event) -> None:
        self._push({"time": dt_util.now().isoformat(), "held": event.data["message"],
                    "reason": event.data.get("reason"), "source": event.data.get("source")})
        self.async_write_ha_state()

    @property
    def native_value(self) -> str | None:
        return self._value

    @property
    def extra_state_attributes(self) -> dict[str, Any]:
        return {"log": self._log, "noticed": list(self._entry.runtime_data.noticer.decisions)[:10]}


class LikelyRoomSensor(_Base):
    """Where Sentient thinks people are, with the score for each room."""

    _attr_icon = "mdi:home-account"

    def __init__(self, entry: SentientConfigEntry) -> None:
        super().__init__(entry, "likely_room")

    async def async_added_to_hass(self) -> None:
        # local arithmetic only, so a gentle refresh to show decay is fine
        self.async_on_remove(
            async_track_time_interval(self.hass, self._tick, timedelta(seconds=30))
        )

    @callback
    def _tick(self, _now) -> None:
        self.async_write_ha_state()

    def _name(self, area_id: str | None) -> str | None:
        if not area_id:
            return None
        area = ar.async_get(self.hass).async_get_area(area_id)
        return area.name if area else area_id

    @property
    def native_value(self) -> str | None:
        rt = self._entry.runtime_data
        speakers = rt.speech.pick_speaker()
        area = rt.tracker.best_area()
        return self._name(area) or (self._name(rt.speech._area_of(speakers[0])) if speakers else None)

    @property
    def extra_state_attributes(self) -> dict[str, Any]:
        rt = self._entry.runtime_data
        scores = sorted(rt.tracker.scores().items(), key=lambda kv: kv[1], reverse=True)
        return {
            "scores": {self._name(a): v for a, v in scores},
            "speaker": (rt.speech.pick_speaker() or [None])[0],
            "quiet_hours": rt.speech.quiet_now(),
            "persona": rt.options.get("persona") or self._entry.title,
            "ai_checks_today": rt.watches.ai_checks_today,
            "chat_agent": rt.options.get("chat_agent"),
        }


class WatchesSensor(_Base):
    """How many things are being watched, with each one as attributes."""

    _attr_icon = "mdi:eye-outline"
    _attr_native_unit_of_measurement = "watches"

    def __init__(self, entry: SentientConfigEntry) -> None:
        super().__init__(entry, "watches")

    async def async_added_to_hass(self) -> None:
        self.async_on_remove(self.hass.bus.async_listen(EVENT_WATCH_CHANGED, self._changed))

    @callback
    def _changed(self, _event: Event) -> None:
        self.async_write_ha_state()

    @property
    def native_value(self) -> int:
        return len(self._entry.runtime_data.watches.watches)

    @property
    def extra_state_attributes(self) -> dict[str, Any]:
        manager = self._entry.runtime_data.watches
        return {
            "watches": [
                {**w.summary(), "ends": w.until, "entity_id": w.entity_id, "wakes_on": w.triggers, "spot": w.spot}
                for w in manager.watches
            ],
            "ai_checks_today": manager.ai_checks_today,
        }


class RoutinesSensor(_Base):
    """Routines learned from history, and which ones the house now does itself."""

    _attr_icon = "mdi:calendar-sync"
    _attr_native_unit_of_measurement = "routines"

    def __init__(self, entry: SentientConfigEntry) -> None:
        super().__init__(entry, "routines")

    async def async_added_to_hass(self) -> None:
        self.async_on_remove(self.hass.bus.async_listen(EVENT_ROUTINES_CHANGED, self._changed))

    @callback
    def _changed(self, _event: Event) -> None:
        self.async_write_ha_state()

    @property
    def native_value(self) -> int:
        return len(self._entry.runtime_data.learner.all())

    @property
    def extra_state_attributes(self) -> dict[str, Any]:
        learner = self._entry.runtime_data.learner
        return {
            "routines": [
                {**r.summary(), "entity_id": r.entity_id, "offered": bool(r.suggested), "declined": r.declined}
                for r in sorted(learner.all(), key=lambda r: (r.adopted, r.support), reverse=True)
            ],
            "deleted": [{"id": k, "routine": v} for k, v in learner.dismissed.items()],
            "last_learned": learner.last_run,
        }


class VisitorsSensor(_Base):
    """Visitors to the door today, with the recent log."""

    _attr_icon = "mdi:doorbell-video"
    _attr_native_unit_of_measurement = "visitors"

    def __init__(self, entry: SentientConfigEntry) -> None:
        super().__init__(entry, "visitors")

    async def async_added_to_hass(self) -> None:
        self.async_on_remove(self.hass.bus.async_listen(EVENT_VISITOR, self._changed))

    @callback
    def _changed(self, _event: Event) -> None:
        self.async_write_ha_state()

    @property
    def native_value(self) -> int:
        return len(self._entry.runtime_data.visitors.today())

    @property
    def extra_state_attributes(self) -> dict[str, Any]:
        return {"recent": list(self._entry.runtime_data.visitors.log)[:10]}


class HealthSensor(_Base):
    """Number of house problems: devices offline and batteries running low."""

    _attr_icon = "mdi:home-heart"
    _attr_native_unit_of_measurement = "problems"

    def __init__(self, entry: SentientConfigEntry) -> None:
        super().__init__(entry, "health")

    async def async_added_to_hass(self) -> None:
        self.async_on_remove(self.hass.bus.async_listen(EVENT_HEALTH_CHANGED, self._changed))

    @callback
    def _changed(self, _event: Event) -> None:
        self.async_write_ha_state()

    @property
    def native_value(self) -> int:
        return len(self._entry.runtime_data.health.issues)

    @property
    def extra_state_attributes(self) -> dict[str, Any]:
        health = self._entry.runtime_data.health
        return {"issues": health.issues[:30], "updates": health.updates}


class DriveSensor(_Base):
    """Cars on the drive, from the AI's last look at the drive spot."""

    _attr_icon = "mdi:car"
    _attr_native_unit_of_measurement = "cars"

    def __init__(self, entry: SentientConfigEntry) -> None:
        super().__init__(entry, "drive")

    async def async_added_to_hass(self) -> None:
        from .drive import EVENT_DRIVE

        self.async_on_remove(self.hass.bus.async_listen(EVENT_DRIVE, self._changed))

    @callback
    def _changed(self, _event: Event) -> None:
        self.async_write_ha_state()

    @property
    def native_value(self) -> int | None:
        return self._entry.runtime_data.drive.describe().get("cars")

    @property
    def extra_state_attributes(self) -> dict[str, Any]:
        d = self._entry.runtime_data.drive.describe()
        return {"description": d.get("description"), "checked": d.get("checked_at"), "changed": d.get("changed_at"),
                "set_up": self._entry.runtime_data.drive.spot() is not None}


class MindSensor(_Base):
    """What it's thinking: overnight thoughts, questions, follow-ups and conversations today."""

    _attr_icon = "mdi:head-lightbulb-outline"
    _attr_native_unit_of_measurement = "conversations"

    def __init__(self, entry: SentientConfigEntry) -> None:
        super().__init__(entry, "mind")

    async def async_added_to_hass(self) -> None:
        self.async_on_remove(async_track_time_interval(self.hass, self._tick, timedelta(minutes=1)))

    @callback
    def _tick(self, _now) -> None:
        self.async_write_ha_state()

    @property
    def native_value(self) -> int:
        return self._entry.runtime_data.conversations.count_today()

    @property
    def extra_state_attributes(self) -> dict[str, Any]:
        rt = self._entry.runtime_data
        last = rt.reflection.last or {}
        curious = rt.curiosity.listing()
        return {
            "thoughts": last.get("thoughts") or [],
            "learned_overnight": last.get("memories") or [],
            "last_reflection": last.get("time"),
            "wondering": curious["wondering"],
            "asked": curious["asked"],
            "follow_ups": rt.loops.listing(),
            "to_pass_on": [q["text"] for q in rt.followyou.queue if q.get("source") != "thought"],
            "recent_conversations": [rt.conversations.describe(e) for e in rt.conversations.recent(hours=12, limit=5)],
        }


class HouseSensor(_Base):
    """Asleep or awake: when everyone went to bed, and when someone was up."""

    _attr_icon = "mdi:home-heart"
    _attr_device_class = "enum"
    _attr_options = ["awake", "asleep"]

    def __init__(self, entry: SentientConfigEntry) -> None:
        super().__init__(entry, "house")

    async def async_added_to_hass(self) -> None:
        from .sleep import EVENT_SLEEP

        self.async_on_remove(self.hass.bus.async_listen(EVENT_SLEEP, self._changed))

    @callback
    def _changed(self, _event: Event) -> None:
        self.async_write_ha_state()

    @property
    def icon(self) -> str:
        return "mdi:sleep" if self._entry.runtime_data.sleep.asleep else "mdi:home-heart"

    @property
    def native_value(self) -> str:
        return "asleep" if self._entry.runtime_data.sleep.asleep else "awake"

    @property
    def extra_state_attributes(self) -> dict[str, Any]:
        return {"since": self._entry.runtime_data.sleep.since}
