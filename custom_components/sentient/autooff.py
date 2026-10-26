"""Auto-off: things switched off by themselves.

Each item on the Settings page names what to switch off (entities, and/or every real light in some
rooms) and when:
* once it has been on for a while (the coffee machine after 10 minutes), and/or
* once the room's motion sensors have seen nothing for a while (lights after 20 minutes).
Checked every 30 seconds from the states themselves, so a restart never leaves something on.
Lights in a light show are left alone. Optionally says a line when it does it.
"""

from __future__ import annotations

from datetime import datetime, timedelta
import logging
from typing import Any, Callable

from homeassistant.core import Context, HomeAssistant, callback
from homeassistant.helpers import entity_registry as er
from homeassistant.helpers.event import async_track_time_interval
from homeassistant.util import dt as dt_util

from .items import Items
from .kinds import in_windows

_LOGGER = logging.getLogger(__name__)

OFF_STATES = {"off", "unavailable", "unknown", "idle", "standby", "paused"}


def is_on(state) -> bool:
    return state is not None and state.state not in OFF_STATES


class AutoOff:
    def __init__(self, hass: HomeAssistant, items: Items, runtime: Callable[[], Any]) -> None:
        self.hass = hass
        self.items = items
        self._rt = runtime
        self._unsub = None
        self._parent = Context()

    @callback
    def async_start(self) -> None:
        self._unsub = async_track_time_interval(self.hass, self._tick, timedelta(seconds=30))

    @callback
    def async_stop(self) -> None:
        if self._unsub:
            self._unsub()
            self._unsub = None

    def targets(self, item: dict[str, Any]) -> list[str]:
        out = [e for e in item.get("entities") or [] if self.hass.states.get(e)]
        areas = set(item.get("areas") or [])
        if areas:
            from .presence import area_of

            reg = er.async_get(self.hass)
            for st in self.hass.states.async_all("light"):
                entry = reg.async_get(st.entity_id)
                if entry is not None and (entry.entity_category or entry.hidden_by):
                    continue
                if isinstance(st.attributes.get("entity_id"), list):
                    continue  # a light group
                if "screen" in st.entity_id or "backlight" in st.entity_id:
                    continue
                if area_of(self.hass, st.entity_id) in areas and st.entity_id not in out:
                    out.append(st.entity_id)
        return out

    def _empty_for(self, item: dict[str, Any], now: datetime) -> timedelta | None:
        """How long every motion sensor has been clear; None if one sees movement."""
        sensors = [self.hass.states.get(e) for e in item.get("motion") or []]
        sensors = [s for s in sensors if s is not None and s.state not in ("unavailable", "unknown")]
        if not sensors or any(s.state == "on" for s in sensors):
            return None
        return now - max(s.last_changed for s in sensors)

    def due(self, item: dict[str, Any], now: datetime) -> list[str]:
        """What should go off now."""
        if not item.get("enabled", True) or not in_windows(item.get("times"), dt_util.as_local(now)):
            return []
        rt = self._rt()
        showing = set(rt.party.lights) if rt is not None and rt.party.active else set()
        on = [e for e in self.targets(item) if is_on(self.hass.states.get(e)) and e not in showing]
        if not on:
            return []
        out: set[str] = set()
        minutes = float(item.get("on_minutes") or 0)
        if minutes > 0:
            out |= {e for e in on if now - self.hass.states.get(e).last_changed >= timedelta(minutes=minutes)}
        if item.get("motion"):
            empty = self._empty_for(item, now)
            limit = timedelta(minutes=float(item.get("empty_minutes") or 20))
            if empty is not None and empty >= limit:
                # only things that were already on when the room went quiet, or have been on that long
                out |= {e for e in on if now - self.hass.states.get(e).last_changed >= limit}
        return sorted(out)

    @callback
    def _tick(self, now: datetime) -> None:
        for item in list(self.items.items.values()):
            if due := self.due(item, now):
                self.hass.async_create_task(self.async_switch_off(item, due), eager_start=False)

    async def async_switch_off(self, item: dict[str, Any], entities: list[str]) -> None:
        ctx = Context(parent_id=self._parent.id)  # the house did this, not a person
        for entity_id in entities:
            domain = entity_id.split(".", 1)[0]
            service = "turn_off"
            if domain == "media_player":
                service = "media_stop" if self.hass.states.get(entity_id).state == "playing" else "turn_off"
            try:
                await self.hass.services.async_call(domain, service, {"entity_id": entity_id}, blocking=True, context=ctx)
            except Exception as err:  # noqa: BLE001
                _LOGGER.debug("Auto-off couldn't switch off %s: %s", entity_id, err)
        self.items.update(item["id"], last=dt_util.utcnow().isoformat())
        rt = self._rt()
        if rt is None:
            return
        names = [st.name for e in entities if (st := self.hass.states.get(e))]
        why = []
        if item.get("motion"):
            why.append(f"no movement for {float(item.get('empty_minutes') or 20):g} min")
        if float(item.get("on_minutes") or 0) > 0:
            why.append(f"on for {float(item['on_minutes']):g} min")
        rt.timeline.add("light", f"{', '.join(names)} off", f"auto-off ({item['name']}): {' or '.join(why)}")
        if (say := (item.get("say") or "").strip()):
            await rt.speech.async_say(say, "low", None, source=f"autooff:{item['name']}")
