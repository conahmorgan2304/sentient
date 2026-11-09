"""Visitors: a quiet log of who came to the door.

When a chosen doorbell (or any trigger the user picks) fires, Sentient takes
one AI look through the door camera and logs the visit — "2:02pm, a delivery
driver holding a small parcel". By default it says nothing about who is
there; voice agents can answer "who came earlier?" from the log. If the
visitor looked like a delivery, it looks again a few minutes later and can
say that a parcel has been left.

Optional doorstep sensors (e.g. a Frigate zone's person occupancy) catch the
deliveries that don't ring: when a visit there ends and the bell wasn't
pressed, one look checks for a parcel or post left behind. It only speaks
when a parcel appears that wasn't there at the last look.
"""

from __future__ import annotations

import asyncio
from collections import deque
from datetime import datetime, timedelta
import logging
from typing import TYPE_CHECKING, Any, Callable

from homeassistant.const import EVENT_STATE_CHANGED, STATE_ON, STATE_UNAVAILABLE, STATE_UNKNOWN
from homeassistant.core import Event, EventStateChangedData, HomeAssistant, callback
from homeassistant.exceptions import HomeAssistantError
from homeassistant.helpers.event import async_call_later
from homeassistant.helpers.storage import Store
from homeassistant.util import dt as dt_util

from .ai import async_generate
from .const import (
    CONF_AI_TASK_ENTITY,
    CONF_ANNOUNCE_VISITORS,
    CONF_DOOR_CAMERA,
    CONF_DOOR_TRIGGERS,
    CONF_DOORSTEP_SENSORS,
    CONF_ZONE_DOOR_SENSORS,
    CONF_ZONE_VISITS,
    CONF_FACE_SENSOR,
    CONF_FACES,
    CONF_PARCEL_ALERTS,
    DEFAULT_ANNOUNCE_VISITORS,
    DEFAULT_PARCEL_ALERTS,
    DOMAIN,
    EVENT_VISITOR,
)
from .watches import local_when

if TYPE_CHECKING:
    from .speech import SpeechRouter
    from .watches import WatchManager

_LOGGER = logging.getLogger(__name__)

STORAGE_VERSION = 1
COOLDOWN = timedelta(seconds=60)
PARCEL_RECHECK = 180  # seconds after a delivery
KEEP = 30
DOORSTEP_SETTLE = 10  # seconds after a doorstep visit ends, so the visitor has stepped away
DOORSTEP_COOLDOWN = timedelta(minutes=2)
ZONE_DOOR_WINDOW = timedelta(seconds=90)  # the front door opened this recently: the zone is someone coming or going
ZONE_SETTLE = 4  # seconds after someone enters the zone, so the picture has them at the door, not the gate
RANG_RECENTLY = timedelta(minutes=5)  # a doorbell visit already does its own parcel check
FACE_FRESH = timedelta(minutes=2)
_NO_NAME = {"", "none", "unknown", "unavailable", "no face", "no_face", "nobody", "null"}
KINDS = ["delivery", "visitor", "neighbour", "child", "unknown", "nobody"]


class Visitors:
    """Watches the door triggers and keeps the log."""

    def __init__(
        self, hass: HomeAssistant, entry_id: str, options: dict[str, Any], speech: SpeechRouter, watches: WatchManager
    ) -> None:
        self.hass = hass
        self.options = options
        self.speech = speech
        self.watches = watches
        self._store: Store[dict[str, Any]] = Store(hass, STORAGE_VERSION, f"{DOMAIN}.{entry_id}.visitors")
        self.log: deque[dict[str, Any]] = deque(maxlen=KEEP)
        self._last: datetime | None = None
        self._busy = False
        self._unsubs: list = []
        self._doorstep_last: datetime | None = None
        self._doorstep_pending = False
        self._zone_last: datetime | None = None
        self._door_opened: datetime | None = None
        self.parcel_waiting = False  # a parcel was there at the last look
        self.expecting: Callable[[], bool] = lambda: False  # set by the runtime: a parcel's out for delivery today
        self.on_visit: Callable[[str, str], Any] | None = None  # set by the runtime: (camera, text) for the security timeline

    async def async_start(self) -> None:
        data = await self._store.async_load() or {}
        self.log.extend(data.get("visits", [])[:KEEP])
        self.parcel_waiting = bool(data.get("parcel_waiting"))
        self._unsubs.append(self.hass.bus.async_listen(EVENT_STATE_CHANGED, self._on_state_changed))

    @callback
    def async_stop(self) -> None:
        while self._unsubs:
            self._unsubs.pop()()

    @callback
    def _save(self) -> None:
        self._store.async_delay_save(lambda: {"visits": list(self.log), "parcel_waiting": self.parcel_waiting}, 2)
        self.hass.bus.async_fire(EVENT_VISITOR, {"count": len(self.log)})

    @property
    def camera(self) -> str | None:
        return self.options.get(CONF_DOOR_CAMERA)

    @property
    def triggers(self) -> set[str]:
        return set(self.options.get(CONF_DOOR_TRIGGERS) or [])

    @property
    def doorstep(self) -> set[str]:
        return set(self.options.get(CONF_DOORSTEP_SENSORS) or [])

    @property
    def zone_doors(self) -> set[str]:
        return set(self.options.get(CONF_ZONE_DOOR_SENSORS) or [])

    def today(self) -> list[dict[str, Any]]:
        start = dt_util.start_of_local_day()
        return [v for v in self.log if (t := dt_util.parse_datetime(v["time"])) and t >= start]

    def summary(self, limit: int = 8) -> list[str]:
        out = []
        for v in list(self.log)[:limit]:
            when = dt_util.parse_datetime(v["time"])
            who = f"{v['name']} — " if v.get("name") else ""
            line = f"{local_when(when) if when else ''}: {who}{v['description']}"
            if v.get("parcel_left"):
                line += " (left a parcel)"
            out.append(line)
        return out

    def recognised_name(self) -> str | None:
        """A name from the face-recognition sensor, if one appeared just now."""
        if not self.options.get(CONF_FACES):
            return None  # Faces is off: names stay out of the timeline, notifications and summaries
        entity_id = self.options.get(CONF_FACE_SENSOR)
        st = self.hass.states.get(entity_id) if entity_id else None
        if st is None or st.state.strip().lower() in _NO_NAME:
            return None
        if dt_util.utcnow() - st.last_changed > FACE_FRESH:
            return None
        return st.state.strip().replace("_", " ").title()

    # ----- trigger -------------------------------------------------------------
    @callback
    def _on_state_changed(self, event: Event[EventStateChangedData]) -> None:
        new = event.data["new_state"]
        if new is None or not self.camera:
            return
        old = event.data["old_state"]
        if new.state in (STATE_UNAVAILABLE, STATE_UNKNOWN) or old is None or old.state == STATE_UNAVAILABLE:
            return
        if new.entity_id in self.zone_doors:
            if new.state == STATE_ON and old.state != STATE_ON:
                self._door_opened = dt_util.utcnow()
            if new.entity_id not in self.doorstep and new.entity_id not in self.triggers:
                return
        if new.entity_id in self.doorstep:
            if new.state == STATE_ON and old.state != STATE_ON:
                self._zone_entered(new.entity_id)
            elif old.state == STATE_ON and new.state != STATE_ON:
                self._doorstep_visit_ended()
            return
        if new.entity_id not in self.triggers:
            return
        if new.domain == "event":
            fired = old.state != new.state  # event entities change state to the time they fired
        else:
            fired = new.state == STATE_ON and old.state != STATE_ON
        if not fired:
            return
        now = dt_util.utcnow()
        if self._busy or (self._last and now - self._last < COOLDOWN):
            return
        if self.options.get(CONF_ZONE_VISITS, False) and self._zone_last and now - self._zone_last < RANG_RECENTLY:
            return  # the zone got there first: the bell is the same visit
        self._last = now
        self.hass.async_create_task(self.async_visit(new.entity_id))

    # ----- someone in the door camera's zone, without the bell ----------------------
    def door_opened_first(self, now: datetime) -> bool:
        """The front door is open, or was opened a moment ago: whoever the camera sees is coming or going."""
        if any((st := self.hass.states.get(e)) and st.state == STATE_ON for e in self.zone_doors):
            return True
        return self._door_opened is not None and now - self._door_opened < ZONE_DOOR_WINDOW

    @callback
    def _zone_entered(self, entity_id: str) -> None:
        """Whichever comes first, the bell or the zone, is the visit; the other is ignored."""
        if not self.options.get(CONF_ZONE_VISITS, False):
            return
        now = dt_util.utcnow()
        if self._busy or (self._last and now - self._last < RANG_RECENTLY):
            return  # the bell (or an earlier zone look) got there first: this is the same visit
        if self.door_opened_first(now):
            _LOGGER.debug("Zone %s ignored: the front door opened first", entity_id)
            return
        self._last = self._zone_last = now
        self.hass.async_create_task(self.async_visit(entity_id, via_zone=True))

    # ----- doorstep visits without the bell -------------------------------------
    @callback
    def _doorstep_visit_ended(self) -> None:
        if not (self.options.get(CONF_PARCEL_ALERTS, DEFAULT_PARCEL_ALERTS) or self.expecting()) or self._doorstep_pending:
            return
        now = dt_util.utcnow()
        if self._doorstep_last and now - self._doorstep_last < DOORSTEP_COOLDOWN:
            return
        if self._last and now - self._last < RANG_RECENTLY:
            return  # the bell rang: that visit has its own parcel check
        if any((st := self.hass.states.get(e)) and st.state == STATE_ON for e in self.doorstep):
            return  # someone's still there
        self._doorstep_pending = True

        @callback
        def _go(_now: datetime) -> None:
            self.hass.async_create_task(self.async_doorstep_check())

        async_call_later(self.hass, DOORSTEP_SETTLE, _go)

    async def async_doorstep_check(self) -> bool | None:
        """Look once for a parcel left by someone who didn't ring. Returns whether one is there."""
        self._doorstep_pending = False
        if self._busy or not self.camera or not self.watches.spend("doorbell"):
            return None
        self._doorstep_last = dt_util.utcnow()
        self._busy = True
        try:
            result = await self._look(
                "Sentient: anything left on the doorstep?",
                "This is the doorstep just after someone walked up to the house and left without "
                "ringing. Is a parcel, package, bag or post visibly left on the doorstep or by the "
                "door? If so, describe it in a few plain words (e.g. 'a small brown box').",
                {
                    "parcel_left": {"description": "True if a parcel or post is visibly left", "required": True,
                                    "selector": {"boolean": {}}},
                    "description": {"description": "What was left, a few plain words", "required": False,
                                    "selector": {"text": {}}},
                },
            )
        except HomeAssistantError as err:
            _LOGGER.warning("Sentient could not check the doorstep: %s", err)
            return None
        finally:
            self._busy = False
        there = bool(result.get("parcel_left"))
        was = self.parcel_waiting
        self.parcel_waiting = there
        if there and not was:
            what = (result.get("description") or "a parcel").strip().rstrip(".")
            self.log.appendleft({
                "name": None,
                "time": dt_util.now().isoformat(),
                "kind": "delivery",
                "description": f"someone left {what} without ringing",
                "parcel": True,
                "parcel_left": True,
                "trigger": "doorstep",
            })
            await self.speech.async_say("A parcel's been left at the front door.", "normal", None, source="visitors")
        self._save()
        return there

    # ----- looking ---------------------------------------------------------------
    async def _look(self, task: str, instructions: str, structure: dict) -> dict[str, Any]:
        data: dict[str, Any] = {
            "task_name": task,
            "instructions": instructions,
            "structure": structure,
            "attachments": [
                {"media_content_id": f"media-source://camera/{self.camera}", "media_content_type": "image/jpeg"}
            ],
        }
        if ai_entity := self.options.get(CONF_AI_TASK_ENTITY):
            data["entity_id"] = ai_entity
        response = await async_generate(self.hass, data)
        result = (response or {}).get("data")
        return result if isinstance(result, dict) else {}

    async def async_visit(self, trigger: str | None = None, via_zone: bool = False) -> dict[str, Any] | None:
        if self._busy:
            return None
        look = self.watches.spend("doorbell")  # today's limit used up: still logged and said, in plain words
        self._busy = True
        try:
            await asyncio.sleep(ZONE_SETTLE if via_zone else 1)
            result = {} if not look else await self._look(
                "Sentient: who's at the door",
                ("This is a doorbell camera picture taken as someone reached the front door. " if via_zone else "This is a doorbell camera picture taken as the bell rang. ") + "Describe who is at the door "
                "in a few plain words (e.g. 'a delivery driver holding a small parcel', 'a man in a "
                "red coat', 'two children'). Don't guess names. Set kind to the best match, and parcel "
                "to true if they are carrying or have put down a parcel or package.",
                {
                    "kind": {"description": "Best match", "required": True,
                             "selector": {"select": {"options": KINDS}}},
                    "description": {"description": "A few plain words", "required": True,
                                    "selector": {"text": {}}},
                    "parcel": {"description": "Carrying or put down a parcel", "required": False,
                               "selector": {"boolean": {}}},
                },
            )
        except HomeAssistantError as err:
            _LOGGER.info("Sentient couldn't look at the door, so it's plain: %s", err)
            result = {}
        finally:
            self._busy = False
        kind = result.get("kind") if result.get("kind") in KINDS else "unknown"
        name = self.recognised_name()
        visit = {
            "name": name,
            "time": dt_util.now().isoformat(),
            "kind": kind,
            "description": (result.get("description") or "someone at the door").strip().rstrip("."),
            "parcel": bool(result.get("parcel")),
            "parcel_left": False,
            "trigger": trigger,
        }
        self.log.appendleft(visit)
        self._save()
        if self.on_visit is not None and self.camera:
            how = "Someone reached the front door" if via_zone else "Doorbell pressed"
            self.on_visit(self.camera, f"{how}: {visit['description']}")
        if self.options.get(CONF_ANNOUNCE_VISITORS, DEFAULT_ANNOUNCE_VISITORS) and kind != "nobody":
            line = (f"{name}'s at the door." if name else f"Someone's at the door: {visit['description']}."
                    if result.get("description") else "Someone's at the door.")
            await self.speech.async_say(line, "normal", None, source="visitors")
        if (kind == "delivery" or visit["parcel"]) and (self.options.get(CONF_PARCEL_ALERTS, DEFAULT_PARCEL_ALERTS) or self.expecting()):
            @callback
            def _recheck(_now: datetime) -> None:
                self.hass.async_create_task(self._async_parcel_check(visit))

            async_call_later(self.hass, PARCEL_RECHECK, _recheck)
        return visit

    async def _async_parcel_check(self, visit: dict[str, Any]) -> None:
        if not self.watches.spend("doorbell"):
            return
        try:
            result = await self._look(
                "Sentient: parcel left?",
                "This is the doorstep a few minutes after a delivery. Is a parcel, package or bag left "
                "on the doorstep or by the door?",
                {"parcel_left": {"description": "True if a parcel is visibly left", "required": True,
                                 "selector": {"boolean": {}}}},
            )
        except HomeAssistantError as err:
            _LOGGER.warning("Sentient could not check for a parcel: %s", err)
            return
        if result.get("parcel_left"):
            visit["parcel_left"] = True
            self.parcel_waiting = True
            self._save()
            await self.speech.async_say("A parcel's been left at the front door.", "normal", None, source="visitors")
