"""Explicit, locally verified household tasks."""
from __future__ import annotations

import asyncio
from datetime import timedelta
import logging
import re

from homeassistant.auth.permissions.const import POLICY_READ
from homeassistant.components.homeassistant.exposed_entities import async_should_expose
from homeassistant.exceptions import HomeAssistantError
from homeassistant.util import dt as dt_util

from .sessions import normalise

CONF_BEDTIME = "verified_bedtime"
CONF_BEDTIME_LIGHTS = "verified_bedtime_lights"
CONF_BEDTIME_CONTACTS = "verified_bedtime_contacts"

COMMANDS = {
    "run downstairs bedtime": "start",
    "start downstairs bedtime": "start",
    "downstairs bedtime status": "status",
    "is downstairs bedtime done": "status",
    "is downstairs bedtime complete": "status",
    "cancel downstairs bedtime": "cancel",
}
CONTACT_CLASSES = {"door", "window", "garage_door", "opening"}
RETENTION = timedelta(hours=2)
_LOGGER = logging.getLogger(__name__)


def requested(options, text):
    if options.get(CONF_BEDTIME):
        text = re.sub(r"[,!.?]+$", "", normalise(text).casefold()).strip()
        return COMMANDS.get(text)


class HouseholdTasks:
    def __init__(self, hass, control):
        self.hass = hass
        self.control = control
        self.runs = {}
        self.generation = 0
        self._lock = asyncio.Lock()

    def clear(self):
        self.generation += 1
        self.runs.clear()

    async def answer(self, request, user_input, options, allowed):
        owner = (user_input.context.user_id,) if user_input.context.user_id else (
            user_input.device_id, user_input.satellite_id,
        )
        if not any(owner):
            return "Downstairs bedtime needs a recognised Assist device or Home Assistant user."
        generation = self.generation

        def valid():
            return generation == self.generation and allowed()

        async with self._lock:
            if not valid():
                return "Downstairs bedtime is unavailable."
            self.runs = {key: started for key, started in self.runs.items()
                         if dt_util.utcnow() - started < RETENTION}
            user_id = user_input.context.user_id
            user = await self.hass.auth.async_get_user(user_id) if user_id else None
            if not valid() or (user_id and (not user or not user.is_active)):
                return "Downstairs bedtime is unavailable."
            if request == "cancel":
                if self.runs.pop(owner, None) is None:
                    return "There is no downstairs bedtime check to cancel."
                return "I stopped tracking downstairs bedtime. Any lights already switched off will stay off."
            if request == "status" and owner not in self.runs:
                return "There is no recent downstairs bedtime check. Say run downstairs bedtime to start one."

            lights = list(dict.fromkeys(options.get(CONF_BEDTIME_LIGHTS) or []))
            contacts = list(dict.fromkeys(options.get(CONF_BEDTIME_CONTACTS) or []))
            if not lights or not contacts or len(lights) > 20 or len(contacts) > 20:
                return "Choose one to twenty individual lights and one to twenty opening contacts in Sentient settings."
            if request == "start":
                self.runs.pop(owner, None)
                self.runs[owner] = dt_util.utcnow()
                while len(self.runs) > 20:
                    self.runs.pop(next(iter(self.runs)))

            def can_read(eid):
                if user and not user.permissions.check_entity(eid, POLICY_READ):
                    return False
                try:
                    return async_should_expose(self.hass, "conversation", eid)
                except KeyError:  # Assist exposure data may still be loading
                    return False

            failed = set()
            if request == "start":
                await asyncio.gather(*(self._turn_off(eid, user_input.context, can_read, valid, failed) for eid in lights))

            if not valid():
                return "I stopped the downstairs bedtime check."
            pending = []
            hidden = 0
            for eid, domain, target in [(eid, "light", "off") for eid in lights] + [
                (eid, "binary_sensor", "off") for eid in contacts
            ]:
                if not can_read(eid):
                    hidden += 1
                    continue
                state = self.hass.states.get(eid)
                name = state.name if state else eid
                if not state or state.state in ("unavailable", "unknown") or state.attributes.get("restored"):
                    pending.append(f"{name} has no current report")
                elif state.domain != domain or (domain == "light" and "entity_id" in state.attributes) or (
                    domain == "binary_sensor" and state.attributes.get("device_class") not in CONTACT_CLASSES
                ):
                    pending.append(f"{name} needs a different selection in settings")
                elif state.state != target:
                    if state.state == "on" and domain == "binary_sensor":
                        pending.append(f"{name} reports open")
                    elif eid in failed:
                        pending.append(f"I couldn't verify {name} switched off")
                    else:
                        pending.append(f"{name} hasn't reported off")
            if hidden:
                pending.append(f"{hidden} configured checks are outside your Assist access")
            if pending:
                return "Downstairs bedtime is pending. " + ". ".join(pending) + "."
            return "Downstairs bedtime is complete. All configured lights report off and all configured contacts report closed."

    async def _turn_off(self, eid, context, can_read, valid, failed):
        def switchable():
            state = self.hass.states.get(eid)
            return (valid() and can_read(eid) and state and state.domain == "light" and state.state == "on"
                    and "entity_id" not in state.attributes and not state.attributes.get("restored"))

        if not switchable():
            return
        try:
            await self.control.validate([{"entity_id": eid, "action": "turn_off"}], context)
        except (HomeAssistantError, ValueError):
            failed.add(eid)
            return
        if not switchable():
            return
        try:
            async with asyncio.timeout(3):
                await self.hass.services.async_call("light", "turn_off", {"entity_id": eid}, blocking=True, context=context)
        except Exception:  # noqa: BLE001 - an integration's service failure must not abort other lights
            failed.add(eid)
            _LOGGER.debug("Downstairs bedtime could not switch off %s", eid, exc_info=True)
