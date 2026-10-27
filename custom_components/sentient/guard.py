"""Guard: put back what a voice switched by mistake.

A small AI model will sometimes answer "turn off the shed light" by turning off every light in the
house. When a voice's on/off action reaches more than a few things, and the request named one of
them, everything it switched outside that thing's room is put straight back the way it was —
unless the request asked for all of them ("all", "every", "the whole house", "upstairs"…) or named
those rooms. It's noted on the timeline. Plain rules, no AI.
"""

from __future__ import annotations

from datetime import datetime, timedelta
import logging
import re
from typing import Any

from homeassistant.const import EVENT_STATE_CHANGED
from homeassistant.core import Context, Event, HomeAssistant, callback
from homeassistant.helpers import area_registry as ar
from homeassistant.util import dt as dt_util

from .presence import area_of

_LOGGER = logging.getLogger(__name__)

BROAD = re.compile(r"\b(all|every|everything|everywhere|whole|house|upstairs|downstairs|both)\b", re.I)
WORD = re.compile(r"[a-z0-9]+")
DOMAINS = {"light", "switch", "fan", "media_player", "input_boolean"}
RECENT = timedelta(seconds=30)
MIN_TARGETS = 3


def _words(text: str) -> list[str]:
    return WORD.findall((text or "").lower())


def _named(name: str, said: list[str]) -> bool:
    """Every word of the device's name appears in what was said (allowing plurals: light ~ lights)."""
    words = _words(name)
    return bool(words) and all(any(s == w or s.rstrip("s") == w or w.rstrip("s") == s for s in said) for w in words)


class Guard:
    def __init__(self, hass: HomeAssistant) -> None:
        self.hass = hass
        self._before: dict[str, tuple[str, datetime]] = {}  # entity -> (state before its last change, when)
        self._unsub = None
        self.context = Context()  # Sentient's own put-backs

    @callback
    def async_start(self) -> None:
        self._unsub = self.hass.bus.async_listen(EVENT_STATE_CHANGED, self._changed)

    @callback
    def async_stop(self) -> None:
        if self._unsub:
            self._unsub()
            self._unsub = None

    @callback
    def _changed(self, event: Event) -> None:
        new, old = event.data.get("new_state"), event.data.get("old_state")
        if new is None or old is None or new.domain not in DOMAINS or old.state == new.state:
            return
        self._before[new.entity_id] = (old.state, dt_util.utcnow())
        if len(self._before) > 300:
            cutoff = dt_util.utcnow() - RECENT
            self._before = {k: v for k, v in self._before.items() if v[1] > cutoff}

    def mistakes(self, said: str, targets: list[dict[str, Any]]) -> list[str]:
        """Which of these targets the request didn't ask for."""
        entities = [t["id"] for t in targets if isinstance(t, dict) and t.get("type") == "entity"
                    and str(t.get("id", "")).split(".")[0] in DOMAINS]
        if len(entities) < MIN_TARGETS or BROAD.search(said or ""):
            return []
        words = _words(said)
        names = {e: (st.name if (st := self.hass.states.get(e)) else e) for e in entities}
        asked = [e for e in entities if _named(names[e], words)]
        if not asked:
            return []  # can't tell what was meant: leave it alone
        areas = {area_of(self.hass, e) for e in asked}
        reg = ar.async_get(self.hass)
        out = []
        for e in entities:
            if e in asked:
                continue
            area = area_of(self.hass, e)
            if area in areas:
                continue  # "the kitchen lights" — the rest of that room was meant too
            area_name = (reg.async_get_area(area).name if area and reg.async_get_area(area) else "") or ""
            if area_name and _named(area_name, words):
                continue  # that room was named
            out.append(e)
        return out

    async def async_put_back(self, said: str, targets: list[dict[str, Any]]) -> list[str]:
        now = dt_util.utcnow()
        restored = []
        for entity_id in self.mistakes(said, targets):
            before = self._before.get(entity_id)
            st = self.hass.states.get(entity_id)
            if before is None or st is None or now - before[1] > RECENT or before[0] not in ("on", "off"):
                continue
            if st.state == before[0]:
                continue
            try:
                await self.hass.services.async_call(
                    "homeassistant", f"turn_{before[0]}", {"entity_id": entity_id}, blocking=True,
                    context=Context(parent_id=self.context.id),
                )
                restored.append(st.name)
            except Exception as err:  # noqa: BLE001
                _LOGGER.debug("Couldn't put back %s: %s", entity_id, err)
        return restored
