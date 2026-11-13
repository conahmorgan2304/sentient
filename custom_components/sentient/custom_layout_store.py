"""Optional custom-page layouts, saved locally; no editor or card rendering.

Only a trusted Home Assistant user object may grant admin write privileges.
Calling clients cannot grant admin through JSON booleans.
"""
from __future__ import annotations
from typing import Any
from homeassistant.helpers.storage import Store
from .layout_policy import validate

class CustomLayoutStore:
    def __init__(self, hass: Any, entry_id: str) -> None:
        self._store = Store(hass, 1, f"sentient.{entry_id}.custom_layout")
        self._layout: dict[str, list[dict[str, Any]]] = {}

    async def async_load(self) -> None:
        data = await self._store.async_load() or {}
        proposed = data.get("layout") if isinstance(data, dict) else None
        if isinstance(proposed, dict) and validate(proposed, admin=True)["accepted"]:
            self._layout = proposed
        else:
            self._layout = {}

    def view(self, *, enabled: bool) -> dict[str, Any]:
        """Switching off restores default UI without deleting saved layout."""
        return {"enabled": bool(enabled), "layout": {page: list(cards) for page, cards in self._layout.items()}
                if enabled else {}, "saved_layout_present": bool(self._layout)}

    async def async_save(self, proposed: dict[str, Any], *, user: Any) -> dict[str, Any]:
        if not getattr(user, "is_admin", False):
            return {"accepted": False, "reason": "administrator_required"}
        result = validate(proposed, admin=True)
        if not result["accepted"]:
            return result
        await self._store.async_save({"layout": proposed})
        self._layout = {page: [dict(card) for card in cards] for page, cards in proposed.items()}
        return {"accepted": True, "saved": True}

    async def async_reset(self, *, user: Any) -> dict[str, Any]:
        if not getattr(user, "is_admin", False):
            return {"accepted": False, "reason": "administrator_required"}
        await self._store.async_save({"layout": {}})
        self._layout = {}
        return {"accepted": True, "saved": True}
