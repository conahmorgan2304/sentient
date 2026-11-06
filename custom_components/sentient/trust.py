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
from typing import Any

from homeassistant.core import HomeAssistant
from homeassistant.helpers.storage import Store

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

    async def async_start(self) -> None:
        self.yes_run = dict((await self._store.async_load() or {}).get("yes_run") or {})

    def async_stop(self) -> None:
        return

    def _save(self) -> None:
        self._store.async_delay_save(lambda: {"yes_run": self.yes_run}, 5)

    def level(self, entity_id: str) -> str:
        st = self.hass.states.get(entity_id)
        if entity_id.startswith("cover.") and st is not None and st.attributes.get("device_class") in ALWAYS_ASK:
            return "ask"
        value = self.options.get(KINDS[kind_of(self.hass, entity_id)], "do")
        return value if value in LEVELS else "do"

    async def async_act(self, entity_id: str, doing: str, asking: str, run: Callable[[], Awaitable[Any]],
                        why: str) -> str:
        """Do *run* as this kind's trust level says. doing: 'Putting the lamp on'; asking: 'Shall I put the lamp on?'.

        Returns 'done', 'asked' (waiting for a yes) or 'not asked' (couldn't ask, so not done)."""
        level = self.level(entity_id)
        rt = self._rt()
        if level == "do" or rt is None:
            await run()
            return "done"
        if level == "tell":
            with because(why):
                await rt.speech.async_say(f"{doing}.", "low", None, source="trust")
            await run()
            return "done"
        kind = kind_of(self.hass, entity_id)

        before = self.hass.states.get(entity_id)
        options = deepcopy(self.options)

        async def answered(answer: str) -> str:
            if answer == "yes":
                from .never import blocked

                current = self.hass.states.get(entity_id)
                if (options != self.options or blocked(self.hass, entity_id) or current is None
                        or current.state in ("unknown", "unavailable") or before is None
                        or current.state != before.state or current.attributes != before.attributes):
                    return "Things have changed since I asked. No action was taken; please ask again."
                await run()
                self.yes_run[kind] = self.yes_run.get(kind, 0) + 1
                self._save()
                return "Done."
            self.yes_run[kind] = 0
            self._save()
            return "Okay, I'll leave it." if answer == "no" else "Right, it's already sorted."

        with because(why):
            result = await rt.speech.async_ask(
                asking, source="trust", on_answer=answered,
                extra_prompt="If they say yes, do exactly that and nothing else; if no, leave it.",
            )
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
