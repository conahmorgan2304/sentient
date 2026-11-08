"""Auto-off: things switched off by themselves.

Each item on the Settings page names what to switch off (entities, and/or every real light in some
rooms) and when:
* once it has been on for a while (the coffee machine after 10 minutes), and/or
* once the room's motion sensors have seen nothing for a while (lights after 20 minutes).
Checked every 30 seconds from the states themselves, so a restart never leaves something on.
Lights in a light show are left alone. Optionally says a line when it does it. If someone switches
a light straight back on, the room wasn't empty after all: that room waits five minutes longer from
then on (up to an hour), noted on the timeline.
"""

from __future__ import annotations

import asyncio

from datetime import datetime, timedelta
import logging
from typing import Any, Callable

from homeassistant.const import EVENT_STATE_CHANGED
from homeassistant.core import Context, HomeAssistant, callback
from homeassistant.helpers import entity_registry as er
from homeassistant.helpers.event import async_track_time_interval
from homeassistant.util import dt as dt_util
from .own import LastingContext

from .items import Items
from .kinds import in_windows

_LOGGER = logging.getLogger(__name__)

REQUEST_TIMEOUT = 10
BACK_ON_WITHIN = timedelta(minutes=3)
LEARN_STEP = 5  # minutes added each time a room turns out not to be empty
LEARN_MAX = 60

OFF_STATES = {"off", "unavailable", "unknown", "idle", "standby", "paused"}


def is_on(state) -> bool:
    return state is not None and state.state not in OFF_STATES


class AutoOff:
    def __init__(self, hass: HomeAssistant, items: Items, runtime: Callable[[], Any]) -> None:
        self.hass = hass
        self.items = items
        self._rt = runtime
        self._unsub = None
        self._unsub_back = None
        self._stopped = False
        self._busy = set()
        self._lasting = LastingContext(hass, "auto-off")  # the house did this, not a person
        self._switched: dict[str, tuple[datetime, str]] = {}  # entity -> (when the house switched it off, item id)

    @property
    def _parent(self) -> Context:
        return self._lasting.get()

    @callback
    def async_start(self) -> None:
        self._stopped = False
        self._unsub = async_track_time_interval(self.hass, self._tick, timedelta(seconds=30))
        self._unsub_back = self.hass.bus.async_listen(EVENT_STATE_CHANGED, self._back_on)

    @callback
    def async_stop(self) -> None:
        self._stopped = True
        if self._unsub:
            self._unsub()
            self._unsub = None
        if self._unsub_back:
            self._unsub_back()
            self._unsub_back = None

    @callback
    def _back_on(self, event) -> None:
        """Someone switched it straight back on: the room wasn't empty — wait longer there from now on."""
        if self._stopped:
            return
        new, old = event.data.get("new_state"), event.data.get("old_state")
        if (old is None or old.state in ('unknown', 'unavailable') or is_on(old)):
            return
        if new is None or new.entity_id not in self._switched or not is_on(new) or new.context.parent_id:
            return
        when, item_id = self._switched.pop(new.entity_id)
        if dt_util.utcnow() - when > BACK_ON_WITHIN:
            return
        item = self.items.items.get(item_id)
        if item is None or not item.get("motion"):
            return
        before = float(item.get("empty_minutes") or 20)
        after = min(LEARN_MAX, before + LEARN_STEP)
        if after == before:
            return
        self.items.update(item_id, empty_minutes=after)
        rt = self._rt()
        if rt is not None:
            rt.timeline.add(
                "learned", f"{new.name} was switched straight back on — {item['name']} now waits {after:g} min",
                f"was {before:g} min",
            )

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
        if not sensors or any(s is None or s.state != "off" for s in sensors):
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

    async def async_switch_off(self, item: dict[str, Any], entities: list[str], *, force: bool = False) -> list[str]:
        """Recheck each queued target and report accepted requests, never assumed success."""
        from .never import allowed

        accepted = []
        for entity_id in dict.fromkeys(entities):
            if self._stopped or self.items.items.get(item['id']) is not item:
                break
            if entity_id in self._busy or not allowed(self.hass, [entity_id]):
                continue
            eligible = self.targets(item) if force else self.due(item, dt_util.utcnow())
            state = self.hass.states.get(entity_id)
            if entity_id not in eligible or not is_on(state):
                continue
            domain = entity_id.split('.', 1)[0]
            service = 'media_stop' if domain == 'media_player' and state.state == 'playing' else 'turn_off'
            ctx = Context(parent_id=self._parent.id)
            self._busy.add(entity_id)
            try:
                async with asyncio.timeout(REQUEST_TIMEOUT):
                    await self.hass.services.async_call(domain, service, {'entity_id': entity_id}, blocking=True, context=ctx)
            except Exception as err:  # a failed destination does not establish an off state
                _LOGGER.debug('Auto-off request failed for %s: %s', entity_id, err)
            else:
                accepted.append(entity_id)
            finally:
                self._busy.discard(entity_id)
        if not accepted or self._stopped or self.items.items.get(item['id']) is not item:
            return accepted
        now = dt_util.utcnow()
        self.items.update(item['id'], last=now.isoformat())
        self._switched = {e: v for e, v in self._switched.items() if now - v[0] < BACK_ON_WITHIN}
        for eid in accepted:
            self._switched[eid] = (now, item['id'])
        rt = self._rt()
        if rt is None:
            return accepted
        names = [st.name for eid in accepted if (st := self.hass.states.get(eid))]
        rt.timeline.add('light', f"Switch-off requested: {', '.join(names)}", f"auto-off ({item['name']}); service accepted, physical result unverified", evidence='attempted')
        if (say := (item.get('say') or '').strip()):
            # A configured 'off' sentence is appropriate only after every target reports off.
            if len(accepted) == len(set(entities)) and all((st := self.hass.states.get(eid)) is not None and st.state in ('off', 'idle', 'standby', 'paused') for eid in accepted):
                await rt.speech.async_say(say, 'low', None, source=f"autooff:{item['name']}")
        return accepted
