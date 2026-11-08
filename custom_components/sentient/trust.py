"""Trust levels: how much the house may do by itself, per kind of device.

For what Sentient decides to do on its own — routines it learned and was told to take over, and warming
the heating before people get up — each kind of device has a level:

* **do** — just do it (as before);
* **tell** — say what it's doing, then do it;
* **ask** — ask first, and only do it on a yes (nothing happens if nobody answers).

Garage doors and gates always ask; locks and the alarm are never done by the house at all. After a run of
yeses, the self-check suggests it stops asking — on the Mind page, changed only on a yes.
"""

from __future__ import annotations

from collections.abc import Awaitable, Callable
from copy import deepcopy
import logging
from datetime import timedelta
from typing import Any

from homeassistant.core import HomeAssistant
from homeassistant.helpers.storage import Store
from homeassistant.util import dt as dt_util

from .const import CONF_TRUST_COVERS, CONF_TRUST_HEATING, CONF_TRUST_LIGHTS, CONF_TRUST_SWITCHES, DOMAIN
from .reasons import because

_LOGGER = logging.getLogger(__name__)
LEVELS = ["do", "tell", "ask"]
KINDS = {"lights": CONF_TRUST_LIGHTS, "switches": CONF_TRUST_SWITCHES, "blinds": CONF_TRUST_COVERS,
         "heating": CONF_TRUST_HEATING}
ALWAYS_ASK = {"garage", "gate", "door"}  # cover device classes
YES_RUN = 5  # yeses in a row before it offers to stop asking


def kind_of(hass: HomeAssistant, entity_id: str) -> str:
    domain = entity_id.split(".", 1)[0]
    return {"light": "lights", "cover": "blinds", "climate": "heating", "water_heater": "heating"}.get(domain, "switches")


class Trust:
    def __init__(self, hass: HomeAssistant, entry_id: str, options: dict[str, Any], rt_getter: Callable[[], Any]) -> None:
        self.hass = hass
        self.options = options
        self._rt = rt_getter
        self._store: Store[dict[str, Any]] = Store(hass, 1, f"{DOMAIN}.{entry_id}.trust")
        self.yes_run: dict[str, int] = {}
        self._stopped = False
        self._busy: set[str] = set()

    async def async_start(self) -> None:
        self._stopped = False
        self.yes_run = dict((await self._store.async_load() or {}).get("yes_run") or {})

    def async_stop(self) -> None:
        self._stopped = True

    def _save(self) -> None:
        self._store.async_delay_save(lambda: {"yes_run": self.yes_run}, 5)

    def level(self, entity_id: str) -> str:
        st = self.hass.states.get(entity_id)
        if entity_id.startswith("cover.") and st is not None and st.attributes.get("device_class") in ALWAYS_ASK:
            return "ask"
        value = self.options.get(KINDS[kind_of(self.hass, entity_id)], "do")
        return value if value in LEVELS else "do"

    async def async_act(self, entity_id: str, doing: str, asking: str, run: Callable[[], Awaitable[Any]],
                        why: str, *, valid: Callable[[], bool] | None = None) -> str:
        """Submit a current action using its configured trust level; a submission is not physical verification."""
        from .never import blocked
        rt = self._rt()
        if rt is None:
            return "not asked"
        level = self.level(entity_id)
        options, values = self.options, deepcopy(self.options)
        speech = rt.speech
        stamp = (getattr(speech, '_privacy_generation', 0), getattr(speech, '_guest_generation', 0))
        before = self.hass.states.get(entity_id)
        expires, consumed = dt_util.utcnow() + timedelta(minutes=2), False
        def policy_current():
            return (not self._stopped and self._rt() is rt and self.options is options and self.options == values
                    and stamp == (getattr(speech, '_privacy_generation', 0), getattr(speech, '_guest_generation', 0)))
        def current():
            state = self.hass.states.get(entity_id)
            return (policy_current() and dt_util.utcnow() < expires and self.level(entity_id) == level
                    and entity_id.split('.', 1)[0] not in ('lock', 'alarm_control_panel')
                    and not blocked(self.hass, entity_id) and before is not None and state is not None
                    and state.state not in ('unknown', 'unavailable')
                    and state.state == before.state and state.attributes == before.attributes
                    and (valid is None or valid()))
        async def submit():
            if entity_id in self._busy or not current():
                return False
            self._busy.add(entity_id)
            try:
                return await run() is not False
            finally:
                self._busy.discard(entity_id)
        if not current():
            return "not asked"
        if level == "do":
            return "done" if await submit() else "not asked"
        if level == "tell":
            with because(why):
                await speech.async_say(f"{doing}.", "low", None, source="trust")
            return "done" if await submit() else "not asked"
        kind = kind_of(self.hass, entity_id)
        async def answered(answer: str) -> str:
            nonlocal consumed
            if consumed:
                return "That answer has already been handled. No further action was taken."
            consumed = True
            if not current():
                return "Things have changed since I asked. No action was taken; please ask again."
            if answer == "yes":
                if not await submit():
                    return "Things have changed since I asked. No action was taken; please ask again."
                if policy_current():
                    self.yes_run[kind] = self.yes_run.get(kind, 0) + 1
                    self._save()
                return "Request sent."
            self.yes_run[kind] = 0
            self._save()
            return "Okay, I'll leave it." if answer == "no" else "Right, it's already sorted."
        with because(why):
            result = await speech.async_ask(asking, source="trust", on_answer=answered,
                extra_prompt="If they say yes, do exactly that and nothing else; if no, leave it.")
        if not policy_current():
            return "not asked"
        if not result.spoken:
            rt.timeline.add("mode", f"Didn't {asking[9:].rstrip('?')}" if asking.startswith("Shall I ") else f"Didn't: {asking}",
                            "set to ask first, and couldn't ask")
            return "not asked"
        return "asked"

    def findings(self) -> list[dict[str, Any]]:
        """Kinds it asks about that always get a yes — for the self-check's suggestions."""
        out = []
        for kind, n in self.yes_run.items():
            if n >= YES_RUN and self.options.get(KINDS.get(kind, ""), "do") == "ask":
                out.append({"kind": "trust", "about": kind,
                            "text": f"You've said yes the last {n} times I asked before doing something with the {kind}"})
        return out
