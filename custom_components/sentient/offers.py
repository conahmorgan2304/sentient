"""Offers: the house asks when someone walks in — "fancy a brew?", "shall I put the heating on?".

Each offer (set up on the Settings page) has rooms, times of day, conditions (the kettle hasn't been
used for a while; it's below 18°), a few ways of asking and the actions for a yes. The question
opens the microphone; the voice agent answers through sentient_accept_offer. Saying no counts as
feedback, so an offer that keeps getting turned down is asked less.
"""

from __future__ import annotations

from datetime import datetime, timedelta
import logging
from typing import Any, Callable

from homeassistant.const import EVENT_STATE_CHANGED
from homeassistant.core import Event, HomeAssistant, callback
from homeassistant.helpers import area_registry as ar
from homeassistant.util import dt as dt_util

from .items import Items
from .kinds import in_windows, variant
from .presence import ActivityTracker, area_of
from .shortcuts import async_run_actions

_LOGGER = logging.getLogger(__name__)
GAP = timedelta(minutes=5)  # never two offers on top of each other


def reading_attr(st, name: str) -> float | None:
    try:
        return float(st.attributes.get(name))
    except (TypeError, ValueError):
        return None


def reading(hass: HomeAssistant, entity_id: str | None) -> float | None:
    st = hass.states.get(entity_id) if entity_id else None
    if st is None:
        return None
    value = st.attributes.get("current_temperature") if st.domain in ("climate", "water_heater") else st.state
    try:
        return float(value)
    except (TypeError, ValueError):
        return None


class Offers:
    def __init__(self, hass: HomeAssistant, items: Items, runtime: Callable[[], Any]) -> None:
        self.hass = hass
        self.items = items
        self._rt = runtime
        self._last_any: datetime | None = None
        self._unsub = None

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
        if new is None or old is None or new.state != "on" or old.state == "on":
            return
        if not ActivityTracker.is_presence(new) or not self.items.items:
            return
        area = area_of(self.hass, new.entity_id)
        if area is None:
            return
        for offer in self.items.enabled():
            if area in (offer.get("areas") or []) and self.ready(offer):
                self.hass.async_create_task(self.async_ask(offer, area))
                break

    def ready(self, offer: dict[str, Any], now: datetime | None = None) -> bool:
        rt = self._rt()
        now = now or dt_util.utcnow()
        local = dt_util.as_local(now)
        speech = rt.speech
        if speech.guest_mode or speech.hushed() or speech.quiet_now():
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

    async def async_ask(self, offer: dict[str, Any], area: str | None = None) -> dict[str, Any]:
        rt = self._rt()
        now = dt_util.utcnow()
        self._last_any = now
        self.items.update(offer["id"], last=now.isoformat())
        value = reading(self.hass, offer.get("value_entity") or offer.get("below_entity"))
        question = variant(offer.get("questions"), value=f"{value:g}" if value is not None else "") or f"Shall I {offer['name'].lower()}?"
        targets = None
        if area:
            a = ar.async_get(self.hass).async_get_area(area)
            found = rt.speech.find_speaker(a.name if a else area)
            targets = [found] if found else None
        result = await rt.speech.async_ask(
            question, source=f"offer:{offer['name']}", targets=targets,
            extra_prompt=(
                f"This is the '{offer['name']}' offer. If they say yes or agree, call sentient_accept_offer with "
                f"offer '{offer['id']}' and accept true (put any detail they give, like a temperature, in 'note'). "
                f"If they say no, call it with accept false. Then answer briefly."
            ),
        )
        return result.as_dict()

    async def async_accept(self, offer_id: str, accept: bool, note: str | None = None) -> dict[str, Any]:
        rt = self._rt()
        offer = self.items.find(offer_id)
        if offer is None:
            return {"error": "No offer like that"}
        if not accept:
            from .feedback import DECLINED

            rt.feedback.record(f"offer:{offer['id']}", DECLINED, "said no", "offers")
            rt.timeline.add("mode", f"Said no to: {offer['name']}")
            return {"offer": offer["name"], "done": False}
        try:
            await async_run_actions(self.hass, offer.get("actions") or [], offer["name"])
        except Exception as err:  # noqa: BLE001
            return {"offer": offer["name"], "error": f"It didn't work: {err}"}
        rt.timeline.add("mode", f"Yes to: {offer['name']}", note or None)
        return {"offer": offer["name"], "done": True, "note": "Done — tell them briefly, in your own words."}
