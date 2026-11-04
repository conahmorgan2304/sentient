"""Follow-through: did what someone just asked for actually happen?

When a device is told to do something within a minute of someone speaking to a
voice assistant, Sentient looks again a little later. If the device is offline
or didn't change, it says so on the speaker they used ("the heating didn't
respond, so it isn't on — it may be offline or its batteries may be flat").
Silent when it worked. No AI.
"""

from __future__ import annotations

from datetime import datetime, timedelta
import logging
from typing import TYPE_CHECKING, Any

from homeassistant.const import EVENT_CALL_SERVICE, EVENT_STATE_CHANGED
from homeassistant.core import Event, HomeAssistant, ServiceCall, callback
from homeassistant.helpers.event import async_call_later
from homeassistant.util import dt as dt_util

if TYPE_CHECKING:
    from .speech import SpeechRouter
    from .timeline import Timeline

_LOGGER = logging.getLogger(__name__)

VOICE_RECENT = timedelta(seconds=90)
CHECK_AFTER = 20  # seconds: the first look
LATE_CHECK = 90  # seconds: a device that hasn't caught up yet (a thermostat over a slow radio) gets this long more
# real devices only: helpers (input_boolean…) have no batteries to go flat
DOMAINS = {"light", "switch", "fan", "climate", "cover", "lock", "media_player",
           "vacuum", "water_heater", "humidifier", "valve"}
# someone speaking to it — not "responding", which is also the satellite reading out an announcement
VOICE_STATES = {"listening", "processing"}
_OFF = {"off", "closed", "locked", "idle", "standby", "docked", "paused"}


def expected(domain: str, service: str, data: dict[str, Any]) -> tuple[str, Any] | None:
    """What a successful call should leave behind: ("on"|"off"|"state"|"temperature", value)."""
    if service in ("turn_on",):
        return ("on", None)
    if service in ("turn_off",):
        return ("off", None)
    if domain == "climate" and service == "set_hvac_mode":
        mode = data.get("hvac_mode")
        return ("off", None) if mode == "off" else ("state", mode)
    if domain in ("climate", "water_heater") and service == "set_temperature" and "temperature" in data:
        return ("temperature", float(data["temperature"]))
    if domain == "cover" and service in ("open_cover", "close_cover"):
        return ("state", "open" if service == "open_cover" else "closed")
    if domain == "lock" and service in ("lock", "unlock"):
        return ("state", "locked" if service == "lock" else "unlocked")
    if domain == "valve" and service in ("open_valve", "close_valve"):
        return ("state", "open" if service == "open_valve" else "closed")
    return None


def worked(state, want: tuple[str, Any]) -> bool | None:
    """True if it did, False if it didn't, None if it can't tell."""
    if state is None:
        return None
    if state.state in ("unavailable", "unknown"):
        return False
    kind, value = want
    if kind == "on":
        return state.state not in _OFF
    if kind == "off":
        return state.state in _OFF or state.state == "off"
    if kind == "state":
        return state.state == value or (value in ("open", "closed") and state.state in (f"{value}ing",))
    if kind == "temperature":
        try:
            return abs(float(state.attributes.get("temperature")) - value) < 0.26 and state.state != "off"
        except (TypeError, ValueError):
            return None
    return None


class FollowThrough:
    """Checks that voice requests to devices actually happened."""

    def __init__(self, hass: HomeAssistant, speech: SpeechRouter, timeline: Timeline) -> None:
        self.hass = hass
        self.speech = speech
        self.timeline = timeline
        self._voice: tuple[str, datetime] | None = None  # the satellite last spoken to
        self.own_contexts: set[str] = set()  # changes the house made itself (not checked here)
        self._unsubs: list = []
        # entity -> what the latest voice request wants from it, whether it has got there yet, and its check
        self._pending: dict[str, dict[str, Any]] = {}

    @callback
    def async_start(self) -> None:
        self._unsubs.append(self.hass.bus.async_listen(EVENT_STATE_CHANGED, self._state))
        self._unsubs.append(self.hass.bus.async_listen(EVENT_CALL_SERVICE, self._call))

    @callback
    def async_stop(self) -> None:
        while self._unsubs:
            self._unsubs.pop()()

    @callback
    def _state(self, event: Event) -> None:
        new = event.data.get("new_state")
        if new is not None and new.domain == "assist_satellite" and new.state in VOICE_STATES:
            self._voice = (new.entity_id, dt_util.utcnow())
        if new is not None and (pending := self._pending.get(new.entity_id)) and worked(new, pending["want"]):
            # it got there — even if someone changes it again afterwards (switching the light back by hand),
            # the request worked
            pending["reached"] = True

    def recent_voice(self) -> str | None:
        if self._voice and dt_util.utcnow() - self._voice[1] <= VOICE_RECENT:
            return self._voice[0]
        return None

    @callback
    def _call(self, event: Event) -> None:
        domain, service = event.data.get("domain"), event.data.get("service")
        from .own import is_own

        if (domain not in DOMAINS or event.context.parent_id or event.context.id in self.own_contexts
                or is_own(self.hass, event.context)):
            return  # automations and scripts have a parent; the house's own changes are its business
        satellite = self.recent_voice()
        if satellite is None:
            return
        data = dict(event.data.get("service_data") or {})
        want = expected(domain, service, data)
        if want is None:
            return
        try:
            from homeassistant.helpers.service import async_extract_referenced_entity_ids

            call = ServiceCall(self.hass, domain, service, data)
            selected = async_extract_referenced_entity_ids(self.hass, call)
            targets = sorted(e for e in {*selected.referenced, *selected.indirectly_referenced} if e.startswith(f"{domain}."))
        except Exception:  # noqa: BLE001
            targets = [e for e in ([data.get("entity_id")] if isinstance(data.get("entity_id"), str) else data.get("entity_id") or [])]
        if not targets or len(targets) > 6:
            return  # whole-house commands are too broad to judge sensibly

        for entity_id in targets:
            # the newest request for a device is the one that counts: "20 degrees… actually make it 21"
            if (old := self._pending.pop(entity_id, None)) and old.get("cancel"):
                old["cancel"]()
            pending = {"want": want, "satellite": satellite, "reached": False}
            self._pending[entity_id] = pending
            pending["cancel"] = async_call_later(self.hass, CHECK_AFTER, self._later(entity_id, pending, late=False))

    def _later(self, entity_id: str, pending: dict[str, Any], late: bool):
        @callback
        def _run(_now: datetime) -> None:
            self._check(entity_id, pending, late)

        return _run

    @callback
    def _check(self, entity_id: str, pending: dict[str, Any], late: bool) -> None:
        if self._pending.get(entity_id) is not pending:
            return  # a newer request took over
        st = self.hass.states.get(entity_id)
        if pending["reached"] or worked(st, pending["want"]) is not False:
            self._pending.pop(entity_id, None)
            return
        offline = st is None or st.state in ("unavailable", "unknown")
        if not late and not offline:
            # not there yet but still talking to us: some devices report back slowly — look again later
            pending["cancel"] = async_call_later(self.hass, LATE_CHECK, self._later(entity_id, pending, late=True))
            return
        self._pending.pop(entity_id, None)
        self.hass.async_create_task(self._async_report([st], pending["want"], pending["satellite"]))

    async def _async_report(self, failed: list, want, satellite: str) -> None:
        failed = [s for s in failed if s is not None]
        if not failed:
            return
        names = [s.name for s in failed]
        what = names[0] if len(names) == 1 else ", ".join(names[:-1]) + " and " + names[-1]
        offline = all(s.state in ("unavailable", "unknown") for s in failed)
        kind, value = want
        verb = f"set to {value:g} degrees" if kind == "temperature" else str(value) if kind == "state" else kind
        why = (
            " It looks offline. If it runs on batteries, they may be flat." if offline
            else " It may not have received the command."
        )
        message = f"Sorry, the {what.lower()} didn't respond, so it isn't {verb}.{why}"
        self.timeline.add("health", f"Didn't happen: {what} {verb}", "asked by voice; the device didn't respond")
        try:
            await self.speech.async_say(message, "urgent", [satellite], source="followthrough")
        except Exception as err:  # noqa: BLE001
            _LOGGER.debug("Could not report a failed action: %s", err)
