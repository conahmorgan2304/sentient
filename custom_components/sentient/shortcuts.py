"""Named routines: "goodnight", "good morning", "leaving" — a set of actions run on request.

Each routine is a list of ordinary Home Assistant actions (edited with HA's own action
editor on the Settings page), optional checks run first — anything left open, lights
still on — and an optional line to say. Run by voice, from the sidebar page, or with
`sentient.run_routine`. Asked for, so it isn't held back by quiet hours.
"""

from __future__ import annotations

from dataclasses import asdict, dataclass, field
import logging
import re
from typing import Any
import uuid

from homeassistant.core import Context, HomeAssistant
from homeassistant.helpers import config_validation as cv
from homeassistant.helpers.script import Script, async_validate_actions_config
from homeassistant.helpers.storage import Store

from .const import DOMAIN

_LOGGER = logging.getLogger(__name__)
STORAGE_VERSION = 1
OPEN_CLASSES = {"door", "window", "garage_door", "opening"}


def _norm(text: str) -> str:
    return re.sub(r"[^a-z0-9]+", " ", text.lower()).strip()


@dataclass
class Shortcut:
    name: str
    actions: list[dict[str, Any]] = field(default_factory=list)
    aliases: str = ""  # other ways of asking, comma separated
    check_open: bool = False
    check_lights: bool = False
    say: str = ""
    id: str = field(default_factory=lambda: uuid.uuid4().hex[:6])
    last_run: str | None = None

    @classmethod
    def from_dict(cls, data: dict[str, Any]) -> Shortcut:
        return cls(**{k: v for k, v in data.items() if k in cls.__dataclass_fields__})

    def names(self) -> list[str]:
        return [_norm(self.name)] + [_norm(a) for a in self.aliases.split(",") if a.strip()]


class Shortcuts:
    def __init__(self, hass: HomeAssistant, entry_id: str) -> None:
        self.hass = hass
        self._store: Store[dict[str, Any]] = Store(hass, STORAGE_VERSION, f"{DOMAIN}.{entry_id}.shortcuts")
        self.items: dict[str, Shortcut] = {}

    async def async_load(self) -> None:
        data = await self._store.async_load() or {}
        self.items = {}
        for raw in data.get("items", []):
            try:
                item = Shortcut.from_dict(raw)
            except TypeError:
                continue
            self.items[item.id] = item

    def _save(self) -> None:
        self._store.async_delay_save(lambda: {"items": [asdict(s) for s in self.items.values()]}, 2)

    def upsert(self, data: dict[str, Any]) -> Shortcut:
        old = self.items.get(data.get("id") or "")
        merged = {**(asdict(old) if old else {}), **data}
        if not str(merged.get("name") or "").strip():
            raise ValueError("A routine needs a name")
        if old is None:
            merged.pop("id", None)
        item = Shortcut.from_dict(merged)
        item.name = item.name.strip()
        clash = self.find(item.name)
        if clash and clash.id != item.id:
            raise ValueError(f"There's already a routine called {clash.name}")
        self.items[item.id] = item
        self._save()
        return item

    def remove(self, item_id: str) -> bool:
        if self.items.pop(item_id, None) is None:
            return False
        self._save()
        return True

    def find(self, name: str) -> Shortcut | None:
        want = _norm(name)
        if item := self.items.get(name):
            return item
        for item in self.items.values():
            if want in item.names():
                return item
        for item in self.items.values():  # "goodnight routine" -> "goodnight"
            if any(n and (n in want or want in n) for n in item.names()):
                return item
        return None

    def listing(self) -> list[dict[str, Any]]:
        return [asdict(s) for s in self.items.values()]

    # ----- running ----------------------------------------------------------------
    def checks(self, item: Shortcut) -> list[str]:
        notes: list[str] = []
        if item.check_open:
            open_now = [
                st.name for st in self.hass.states.async_all("binary_sensor")
                if st.state == "on" and st.attributes.get("device_class") in OPEN_CLASSES
            ]
            if open_now:
                notes.append(_join(open_now) + (" is" if len(open_now) == 1 else " are") + " still open")
        if item.check_lights:
            lit = [st.name for st in self.hass.states.async_all("light") if st.state == "on"]
            if lit:
                shown = lit[:4] + ([f"{len(lit) - 4} more"] if len(lit) > 4 else [])
                notes.append(_join(shown) + (" is" if len(lit) == 1 else " are") + " still on")
        return notes

    async def async_run(self, name: str, context: Context | None = None) -> dict[str, Any]:
        from homeassistant.util import dt as dt_util

        item = self.find(name)
        if item is None:
            return {"error": f"No routine called {name}", "routines": [s.name for s in self.items.values()]}
        notes = self.checks(item)  # before the actions, so "still on" means before it ran
        if item.actions:
            try:
                sequence = await async_validate_actions_config(self.hass, cv.SCRIPT_SCHEMA(item.actions))
                script = Script(self.hass, sequence, f"Sentient: {item.name}", DOMAIN, running_description="routine")
                await script.async_run(context=context or Context())
            except Exception as err:  # noqa: BLE001 - report it, don't crash the caller
                _LOGGER.warning("Routine %s failed: %s", item.name, err)
                return {"routine": item.name, "error": str(err)}
        item.last_run = dt_util.utcnow().isoformat()
        self._save()
        parts = [item.say.strip()] if item.say.strip() else []
        parts += [n[0].upper() + n[1:] + "." for n in notes]
        return {"routine": item.name, "steps": len(item.actions), "said": " ".join(parts), "notes": notes}


def _join(names: list[str]) -> str:
    return names[0] if len(names) == 1 else ", ".join(names[:-1]) + " and " + names[-1]
