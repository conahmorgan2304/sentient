"""Things to ask about later — the way a friend remembers to ask how the vet went.

When someone mentions something coming up (a trip, a match, an interview, the vet), the voice
saves one short question with sentient_ask_later. Once it's due, the next time anyone talks
to the house the voice is given that question to ask, once, at the end of its reply. Never in
quiet hours or guest mode, never in the middle of a plain command, and never anything private.
No AI of its own and nothing is said out of the blue. Switching the setting off clears them.
"""

from __future__ import annotations

from datetime import timedelta
from typing import Any
import uuid

from homeassistant.core import HomeAssistant, callback
from homeassistant.helpers.storage import Store
from homeassistant.util import dt as dt_util

from .const import DOMAIN

STORAGE_VERSION = 1
KEEP = 20
STALE_AFTER = timedelta(days=3)  # not asked within three days of being due: let it go
MAX_HOURS = 24 * 14


class Threads:
    def __init__(self, hass: HomeAssistant, entry_id: str) -> None:
        self.hass = hass
        self._store: Store[dict[str, Any]] = Store(hass, STORAGE_VERSION, f"{DOMAIN}.{entry_id}.threads")
        self.items: list[dict[str, Any]] = []

    async def async_load(self) -> None:
        self.items = list((await self._store.async_load() or {}).get("items", []))[-KEEP:]

    @callback
    def _save(self) -> None:
        self._store.async_delay_save(lambda: {"items": self.items}, 5)

    @callback
    def add(self, question: str, about: str, hours: float) -> dict[str, Any]:
        hours = max(0.5, min(float(hours), MAX_HOURS))
        about = about.strip()[:80]
        self.items = [i for i in self.items if i["about"].casefold() != about.casefold()]  # one per subject
        item = {"id": uuid.uuid4().hex[:8], "about": about, "question": question.strip()[:200],
                "due": (dt_util.utcnow() + timedelta(hours=hours)).isoformat(), "asked": None}
        self.items = (self.items + [item])[-KEEP:]
        self._save()
        return item

    @callback
    def take_due(self) -> dict[str, Any] | None:
        """The oldest question that's due, marked as asked (each is offered only once)."""
        now = dt_util.utcnow()
        keep, chosen = [], None
        for item in self.items:
            due = dt_util.parse_datetime(item["due"]) or now
            if item.get("asked") or now - due > STALE_AFTER:
                continue  # done with, or too late to ask now
            if chosen is None and due <= now:
                chosen = {**item, "asked": now.isoformat()}
                continue
            keep.append(item)
        if chosen is not None or len(keep) != len(self.items):
            self.items = keep
            self._save()
        return chosen

    def waiting(self) -> list[dict[str, Any]]:
        return [i for i in self.items if not i.get("asked")]

    @callback
    def clear(self) -> None:
        if self.items:
            self.items = []
            self._save()
