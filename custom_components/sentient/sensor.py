"""Sensors so dashboards and automations can see what Sentient knows and says."""

from __future__ import annotations

from typing import Any

from homeassistant.components.sensor import SensorEntity
from homeassistant.core import Event, HomeAssistant, callback
from homeassistant.helpers.device_registry import DeviceEntryType, DeviceInfo
from homeassistant.helpers.entity_platform import AddConfigEntryEntitiesCallback
from homeassistant.util import dt as dt_util

from . import SentientConfigEntry
from .const import DOMAIN, EVENT_HELD, EVENT_MEMORY_CHANGED, EVENT_SPOKE


async def async_setup_entry(
    hass: HomeAssistant,
    entry: SentientConfigEntry,
    async_add_entities: AddConfigEntryEntitiesCallback,
) -> None:
    async_add_entities([MemorySensor(entry), LastSpokenSensor(entry)])


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
        return {"items": [{"id": m.id, "text": m.text, "category": m.category} for m in items[:50]]}


class LastSpokenSensor(_Base):
    """The last thing said, plus a short log of what was said or held back."""

    _attr_icon = "mdi:account-voice"

    def __init__(self, entry: SentientConfigEntry) -> None:
        super().__init__(entry, "last_spoken")
        self._value: str | None = None
        self._log: list[dict[str, Any]] = []

    async def async_added_to_hass(self) -> None:
        self.async_on_remove(self.hass.bus.async_listen(EVENT_SPOKE, self._spoke))
        self.async_on_remove(self.hass.bus.async_listen(EVENT_HELD, self._held))

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
        return {"log": self._log}
