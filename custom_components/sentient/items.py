"""Lists the household sets up on the Settings page — offers, announcements, alarms and camera
checks. Each is a list of plain dictionaries with an id, kept across restarts; the engines
that act on them read them live, so a change on the page works straight away."""

from __future__ import annotations

import copy
from typing import Any, Callable
import uuid

from homeassistant.core import HomeAssistant, callback
from homeassistant.helpers.storage import Store

from .const import DOMAIN

STORAGE_VERSION = 1


class Items:
    def __init__(self, hass: HomeAssistant, entry_id: str, kind: str, defaults: dict[str, Any]) -> None:
        self.hass = hass
        self.kind = kind
        self.defaults = defaults
        self._store: Store[dict[str, Any]] = Store(hass, STORAGE_VERSION, f"{DOMAIN}.{entry_id}.{kind}")
        self.items: dict[str, dict[str, Any]] = {}
        self.listeners: list[Callable[[], None]] = []

    async def async_load(self) -> None:
        data = await self._store.async_load() or {}
        self.items = {i["id"]: {**copy.deepcopy(self.defaults), **i} for i in data.get("items", []) if i.get("id")}

    @callback
    def _save(self) -> None:
        self._store.async_delay_save(lambda: {"items": list(self.items.values())}, 2)
        for listener in list(self.listeners):
            listener()

    def upsert(self, data: dict[str, Any]) -> dict[str, Any]:
        if not str(data.get("name") or "").strip():
            raise ValueError("It needs a name")
        old = self.items.get(data.get("id") or "")
        item = {**copy.deepcopy(self.defaults), **(old or {}), **data}
        item["name"] = item["name"].strip()
        if old is None:
            item["id"] = uuid.uuid4().hex[:6]
        self.items[item["id"]] = item
        self._save()
        return item

    def update(self, item_id: str, **changes: Any) -> None:
        if item_id in self.items:
            self.items[item_id].update(changes)
            self._save()

    def remove(self, item_id: str) -> bool:
        if self.items.pop(item_id, None) is None:
            return False
        self._save()
        return True

    def find(self, text: str) -> dict[str, Any] | None:
        if text in self.items:
            return self.items[text]
        low = text.strip().lower()
        for item in self.items.values():
            if item["name"].lower() == low:
                return item
        for item in self.items.values():
            if low and (low in item["name"].lower() or item["name"].lower() in low):
                return item
        return None

    def listing(self) -> list[dict[str, Any]]:
        return [copy.deepcopy(i) for i in self.items.values()]

    def enabled(self) -> list[dict[str, Any]]:
        return [i for i in self.items.values() if i.get("enabled", True)]
