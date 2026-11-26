"""Offers: the house asks when someone walks in — "fancy a brew?", "shall I put the heating on?".

Each offer (set up on the Settings page) has rooms, times of day, conditions (the kettle hasn't been
used for a while; it's below 18°), a few ways of asking and the actions for a yes. The question
opens the microphone; the voice agent answers through sentient_accept_offer. Saying no counts as
feedback, so an offer that keeps getting turned down is asked less.
"""

from __future__ import annotations

import re

from copy import deepcopy
from datetime import datetime, timedelta
import logging
from typing import Any, Callable

from homeassistant.const import EVENT_STATE_CHANGED
from homeassistant.core import Context, Event, HomeAssistant, callback
from homeassistant.helpers import area_registry as ar
from homeassistant.util import dt as dt_util

from .items import Items
from .reasons import because
from .numeric import finite_float
from .kinds import in_windows, variant
from .presence import ActivityTracker, area_of
from .shortcuts import async_run_actions

_LOGGER = logging.getLogger(__name__)
ANSWER_FOR = timedelta(minutes=2)


_SHALL_I = re.compile(r"\b(?:shall|should|can|could) i ([^?.!]+)[?.!]", re.I)


def agreed(question: str) -> str:
    """What to say after a yes, from the question asked: "Shall I put the kettle on?" -> "Okay, I'll put the kettle on." """
    if found := _SHALL_I.search(question or ""):
        return f"Okay, I'll {found.group(1).strip()}."
    return "Okay, I'll do that now."


def version(offer):
    return deepcopy({k: v for k, v in offer.items() if k != 'last'})


GAP = timedelta(minutes=5)  # never two offers on top of each other


def reading_attr(st, name: str) -> float | None:
    return finite_float(st.attributes.get(name))


def reading(hass: HomeAssistant, entity_id: str | None) -> float | None:
    st = hass.states.get(entity_id) if entity_id else None
    if st is None:
        return None
    value = st.attributes.get("current_temperature") if st.domain in ("climate", "water_heater") else st.state
    return finite_float(value)


class Offers:
    def __init__(self, hass: HomeAssistant, items: Items, runtime: Callable[[], Any]) -> None:
        self.hass = hass
        self.items = items
        self._rt = runtime
        self._last_any: datetime | None = None
        self._unsub = None
        self._generation = 0
        self._stopped = False

    @callback
    def async_start(self) -> None:
        self._stopped = False
        self._unsub = self.hass.bus.async_listen(EVENT_STATE_CHANGED, self._changed)

    @callback
    def async_stop(self) -> None:
        self._stopped = True
        self._generation += 1
        if self._unsub:
            self._unsub()
            self._unsub = None

    @callback
    def _changed(self, event: Event) -> None:
        new, old = event.data.get("new_state"), event.data.get("old_state")
        if new is None or old is None or new.state != "on" or old.state == "on":
            return
        if not ActivityTracker.is_presence(new) or not self.items.items:
            return
        area = area_of(self.hass, new.entity_id)
        if area is None:
            return
        profile = getattr(self._rt(), "profiles", None)
        if profile is not None and not (profile.for_area(area) or {}).get("offers", True):
            return  # this room doesn't take offers
        for offer in self.items.enabled():
            if area in (offer.get("areas") or []) and self.ready(offer):
                self.hass.async_create_task(self.async_ask(offer, area, force=False))
                break

    def ready(self, offer: dict[str, Any], now: datetime | None = None) -> bool:
        rt = self._rt()
        now = now or dt_util.utcnow()
        local = dt_util.as_local(now)
        speech = rt.speech
        if self._stopped or not offer.get('enabled', True) or speech.guest_mode or speech.hushed() or speech.quiet_now():
            return False
        if self._last_any and now - self._last_any < GAP:
            return False
        if not in_windows(offer.get("times"), local):
            return False
        last = dt_util.parse_datetime(offer.get("last") or "")
        if last and now - last < timedelta(hours=float(offer.get("cooldown_hours") or 0)):
            return False
        if rt.feedback.offers_blocked(f"offer:{offer['id']}"):
            return False
        if idle := offer.get("idle_entity"):
            st = self.hass.states.get(idle)
            minutes = float(offer.get("idle_minutes") or 0)
            if st is None or st.state not in ("off", "standby", "idle") or now - st.last_changed < timedelta(minutes=minutes):
                return False
        if (below_entity := offer.get("below_entity")) and offer.get("below") is not None:
            value = reading(self.hass, below_entity)
            if value is None or value >= float(offer["below"]):
                return False
        if self._already_warming(offer):
            return False  # "shall I put the heating on?" when it's already set to warm up
        return True

    def _already_warming(self, offer: dict[str, Any]) -> bool:
        import json
        import re

        for entity_id in set(re.findall(r"climate\.[a-z0-9_]+", json.dumps(offer.get("actions") or []))):
            st = self.hass.states.get(entity_id)
            if st is None or st.state in ("off", "unavailable", "unknown"):
                continue
            target, current = reading_attr(st, "temperature"), reading_attr(st, "current_temperature")
            if st.attributes.get("hvac_action") == "heating" or (target is not None and current is not None and target > current + 0.3):
                return True
        # it's already set to what saying yes would set it to
        for action in offer.get("actions") or []:
            if not isinstance(action, dict) or str(action.get("action") or action.get("service")) != "climate.set_temperature":
                continue
            wanted = (action.get("data") or {}).get("temperature")
            ids = (action.get("target") or {}).get("entity_id") or (action.get("data") or {}).get("entity_id") or []
            for entity_id in [ids] if isinstance(ids, str) else ids:
                st = self.hass.states.get(entity_id)
                if st is None or st.state in ("off", "unavailable", "unknown") or wanted is None:
                    continue
                target = reading_attr(st, "temperature")
                try:
                    if target is not None and target >= float(wanted):
                        return True
                except (TypeError, ValueError):
                    continue
        return False

    def _turn_up_instead(self, offer: dict[str, Any], value: float | None) -> str | None:
        """When the heating it would set is already on, ask to turn it up rather than to put it on.

        23 Nov: "It has dropped to 18 degrees in here. Shall I put the heating on?" — the heating was on, holding
        the 18 it was set to. Saying yes still sets the offer's temperature; only the question is put right."""
        for action in offer.get("actions") or []:
            if not isinstance(action, dict) or str(action.get("action") or action.get("service")) != "climate.set_temperature":
                continue
            wanted = finite_float((action.get("data") or {}).get("temperature"))
            ids = (action.get("target") or {}).get("entity_id") or (action.get("data") or {}).get("entity_id") or []
            for entity_id in [ids] if isinstance(ids, str) else ids:
                st = self.hass.states.get(entity_id)
                if st is None or st.state in ("off", "unavailable", "unknown") or wanted is None:
                    continue
                target = reading_attr(st, "temperature")
                now = f"It's {value:g} degrees in here" if value is not None else "It's a bit chilly in here"
                set_to = f", and the heating's on at {target:g}" if target is not None else ", and the heating's on"
                return f"{now}{set_to}. Shall I turn it up to {wanted:g}?"
        return None

    async def async_ask(self, offer: dict[str, Any], area: str | None = None, *, force=True) -> dict[str, Any]:
        rt = self._rt()
        if self._stopped or self.items.items.get(offer['id']) is not offer or (not force and not self.ready(offer)):
            return {'error': 'That offer is no longer ready'}
        generation, options = self._generation, rt.options
        privacy = (getattr(rt.speech, '_privacy_generation', 0), getattr(rt.speech, '_guest_generation', 0))
        expected = version(offer)
        now = dt_util.utcnow()
        until = now + ANSWER_FOR
        consumed = False
        def valid():
            return (not self._stopped and generation == self._generation and rt.options is options
                    and (getattr(rt.speech, '_privacy_generation', 0), getattr(rt.speech, '_guest_generation', 0)) == privacy and not rt.speech.guest_mode
                    and self.items.items.get(offer['id']) is offer and version(offer) == expected
                    and dt_util.utcnow() <= until)
        self._last_any = now
        self.items.update(offer["id"], last=now.isoformat())
        value = reading(self.hass, offer.get("value_entity") or offer.get("below_entity"))
        question = variant(offer.get("questions"), value=f"{value:g}" if value is not None else "") or f"Shall I {offer['name'].lower()}?"
        if (up := self._turn_up_instead(offer, value)) is not None:
            question = up
        targets = None
        if area:
            a = ar.async_get(self.hass).async_get_area(area)
            found = rt.speech.find_speaker(a.name if a else area)
            targets = [found] if found else None
        async def answered(kind: str) -> str:
            nonlocal consumed
            if consumed or not valid():
                return 'That offer is no longer awaiting an answer. Please ask again.'
            consumed = True
            if kind == "yes":
                out = await self.async_accept(offer["id"], True, expected=expected, allowed=valid, allow_disabled=force)
                # 17 Nov: "Fancy a brew? Shall I put the kettle on?" — "yes" got "Request sent." Say what's happening
                return agreed(question) if out.get("done") else str(out.get("error") or "That didn't work.")
            if kind == "done":  # "it's already on": not a no to the offer itself
                rt.timeline.add("mode", f"Already done: {offer['name']}")
                return "Right, it's already sorted."
            await self.async_accept(offer["id"], False)
            return "Okay."

        with because(self.why(offer, area)):
            result = await rt.speech.async_ask(
                question, source=f"offer:{offer['name']}", targets=targets, on_answer=answered,
                extra_prompt="The pending offer is handled by Sentient. If the answer is unclear, ask for yes or no.",
            )
        return result.as_dict()

    def why(self, offer: dict[str, Any], area: str | None) -> str:
        """The plain reasons it asked, for 'why did you offer that?'."""
        bits = []
        if area:
            a = ar.async_get(self.hass).async_get_area(area)
            bits.append(f"someone moved in the {a.name if a else area}")
        below = offer.get("below_entity")
        if offer.get("below") is not None and below and (value := reading(self.hass, below)) is not None:
            bits.append(f"it read {value:g}, below the {float(offer['below']):g} the offer waits for")
        if idle := offer.get("idle_entity"):
            st = self.hass.states.get(idle)
            bits.append(f"{st.name if st else idle} had been idle {float(offer.get('idle_minutes') or 0):g} minutes")
        if offer.get("times"):
            bits.append("it was within the offer's times")
        return f"the '{offer['name']}' offer: " + (", ".join(bits) if bits else "its conditions were met")

    async def async_accept(self, offer_id: str, accept: bool, note: str | None = None, *, expected=None, allowed=None, allow_disabled=False) -> dict[str, Any]:
        rt = self._rt()
        offer = self.items.find(offer_id)
        if offer is None:
            return {"error": "No offer like that"}
        if not accept:
            from .feedback import DECLINED

            rt.feedback.record(f"offer:{offer['id']}", DECLINED, "said no", "offers")
            rt.timeline.add("mode", f"Said no to: {offer['name']}")
            return {"offer": offer["name"], "done": False}
        from .never import touches

        expected = version(offer) if expected is None else expected
        generation, options = self._generation, rt.options
        privacy = (getattr(rt.speech, '_privacy_generation', 0), getattr(rt.speech, '_guest_generation', 0))
        actions = deepcopy(offer.get('actions') or [])
        if not actions:
            return {'error': 'That offer has no configured actions'}
        def valid():
            return (not self._stopped and generation == self._generation and rt.options is options
                    and (getattr(rt.speech, '_privacy_generation', 0), getattr(rt.speech, '_guest_generation', 0)) == privacy and not rt.speech.guest_mode
                    and (offer.get('enabled', True) or allow_disabled)
                    and self.items.items.get(offer['id']) is offer and version(offer) == expected
                    and (allowed is None or allowed()) and not touches(self.hass, actions))
        if not valid():
            return {'error': 'That offer changed or is no longer allowed; please ask again'}
        try:
            await async_run_actions(self.hass, actions, offer['name'], context=Context(), before_run=valid)
        except Exception as err:  # noqa: BLE001
            return {"offer": offer["name"], "error": f"It didn't work: {err}"}
        if (self._stopped or generation != self._generation or rt.options is not options
                or (getattr(rt.speech, '_privacy_generation', 0), getattr(rt.speech, '_guest_generation', 0)) != privacy or rt.speech.guest_mode):
            return {'done': None, 'error': 'Actions were started, but the offer context changed. Check the devices before asking again.'}
        rt.timeline.add("mode", f"Yes to: {offer['name']}", note or None)
        return {"offer": offer["name"], "done": True, "note": "The configured actions completed; physical device results are not independently verified."}
