"""Shush: "stop telling me about the washing machine" — muted until a time, or until unmuted.

A mute is an entity (its changes aren't noticed or remarked on) or a few words (anything
non-urgent containing them is held back). Kept across restarts.
"""

from __future__ import annotations

from datetime import timedelta
from typing import Any
import uuid

from homeassistant.core import HomeAssistant
from homeassistant.helpers.storage import Store
from homeassistant.util import dt as dt_util

from .const import DOMAIN

STORAGE_VERSION = 1


class Mutes:
    def __init__(self, hass: HomeAssistant, entry_id: str) -> None:
        self.hass = hass
        self._store: Store[dict[str, Any]] = Store(hass, STORAGE_VERSION, f"{DOMAIN}.{entry_id}.mutes")
        self.items: list[dict[str, Any]] = []
        self.feedback = None  # set by the runtime: shushing teaches it what not to mention

    async def async_load(self) -> None:
        self.items = (await self._store.async_load() or {}).get("items", [])

    def _save(self) -> None:
        self._store.async_delay_save(lambda: {"items": self.items}, 2)

    def _live(self) -> list[dict[str, Any]]:
        now = dt_util.utcnow().isoformat()
        live = [m for m in self.items if not m.get("until") or m["until"] > now]
        if len(live) != len(self.items):
            self.items = live
            self._save()
        return live

    def add(self, what: str, hours: float | None = None) -> dict[str, Any]:
        what = what.strip()
        kind = "entity" if self.hass.states.get(what) is not None else "words"
        until = (dt_util.utcnow() + timedelta(hours=hours)).isoformat() if hours else None
        mute = {"id": uuid.uuid4().hex[:6], "what": what.lower() if kind == "words" else what, "kind": kind, "until": until}
        self.items = [m for m in self._live() if m["what"] != mute["what"]] + [mute]
        self._save()
        if self.feedback is not None and kind == "entity":
            from .feedback import SHUSHED

            self.feedback.record(what, SHUSHED, "shushed for a while" if hours else "shushed")
        return mute

    def remove(self, what: str | None = None) -> int:
        """Unmute by id, entity or words; None clears everything."""
        before = len(self.items)
        if what is None:
            gone, self.items = self.items, []
        else:
            key = what.strip().lower()
            gone = [m for m in self.items if key in (m["id"], m["what"].lower())]
            self.items = [m for m in self.items if m not in gone]
        self._save()
        if self.feedback is not None:
            from .feedback import SHUSHED

            for m in gone:  # they want to hear about it again
                if m["kind"] == "entity":
                    self.feedback.record(m["what"], -SHUSHED, "unshushed")
        return before - len(self.items)

    def entity_muted(self, entity_id: str) -> bool:
        return any(m["kind"] == "entity" and m["what"] == entity_id for m in self._live())

    def text_muted(self, text: str) -> str | None:
        low = text.lower()
        for m in self._live():
            if m["kind"] == "words" and m["what"] in low:
                return m["what"]
            if m["kind"] == "entity":
                st = self.hass.states.get(m["what"])
                if st is not None and st.name and st.name.lower() in low:
                    return st.name
        return None

    def listing(self) -> list[dict[str, Any]]:
        out = []
        for m in self._live():
            st = self.hass.states.get(m["what"]) if m["kind"] == "entity" else None
            out.append({**m, "name": st.name if st else m["what"]})
        return out
