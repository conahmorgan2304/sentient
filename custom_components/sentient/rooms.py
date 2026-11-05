"""Room profiles: a room can be private, gentler, have its own volume and quiet hours, and say no to offers.

Set on the Rooms page (one profile can cover several rooms). Rooms without a profile behave as before.

* **private** — the voice in that room gets no personal memories, conversations from other rooms or names
  seen at the door, and recalling memories there only finds corrections (how devices are named);
* **gentle** — the voice there is told to speak softly and simply;
* **volume** — announcements on its speakers at this level (outside safety alerts);
* **quiet** — times its speakers say nothing but safety and urgent alerts (a child's bedtime);
* **offers** — off: offers, "want me to do that for you?" and curious questions aren't asked there.
"""

from __future__ import annotations

from typing import Any

from homeassistant.core import HomeAssistant
from homeassistant.helpers import device_registry as dr
from homeassistant.util import dt as dt_util

from .kinds import in_windows, parse_windows
from .presence import area_of

ASKING = ("offer", "learning", "curious")  # sources that ask something that isn't urgent


class RoomProfiles:
    def __init__(self, hass: HomeAssistant, items: Any) -> None:
        self.hass = hass
        self.items = items

    def for_area(self, area_id: str | None) -> dict[str, Any] | None:
        if not area_id:
            return None
        return next((p for p in self.items.enabled() if area_id in (p.get("areas") or [])), None)

    def for_speaker(self, entity_id: str) -> dict[str, Any] | None:
        return self.for_area(area_of(self.hass, entity_id))

    def for_device(self, device_id: str | None) -> dict[str, Any] | None:
        if not device_id or (device := dr.async_get(self.hass).async_get(device_id)) is None:
            return None
        return self.for_area(device.area_id)

    def quiet(self, entity_id: str) -> bool:
        p = self.for_speaker(entity_id)
        return bool(p and parse_windows(p.get("quiet")) and in_windows(p["quiet"], dt_util.now()))

    def allowed(self, speakers: list[str], priority: str, source: str | None) -> list[str]:
        """Drop speakers in rooms that are in their own quiet time (urgent still goes), or that don't take questions."""
        src = str(source or "")
        out = []
        for s in speakers:
            p = self.for_speaker(s)
            if p is None:
                out.append(s)
                continue
            if priority != "urgent" and src != "critical" and self.quiet(s):
                continue
            if src.startswith(ASKING) and not p.get("offers", True):
                continue
            out.append(s)
        return out

    def volume(self, entity_id: str) -> float | None:
        p = self.for_speaker(entity_id)
        level = float((p or {}).get("volume") or 0)
        return level / 100 if level > 0 else None
