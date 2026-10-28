"""Animals outside, by camera: a look at the hutch at dusk, and a cat watch at night.

* **Dusk hutch check** — half an hour after sunset, one AI look at the hutch's spot: are they in, and is
  it shut? Only if not is anything said ("The hutch door looks open"). Once a day.
* **Night cat watch** — after dark, when a cat sensor on that camera goes on (Frigate's cat occupancy),
  one AI look at the hutch's spot: is a cat at or near it? Kept for the morning ("A cat was by the hutch
  at 2:14am"), or said straight away if that's chosen. At most once every fifteen minutes, and skipped
  when the picture hasn't changed.

Both off by default; each counts against the daily AI limit.
"""

from __future__ import annotations

from datetime import datetime, timedelta
import logging
from typing import Any, Callable

from homeassistant.const import EVENT_STATE_CHANGED
from homeassistant.core import Event, HomeAssistant, callback
from homeassistant.helpers import entity_registry as er
from homeassistant.helpers.event import async_track_time_interval
from homeassistant.util import dt as dt_util

from .ai import async_generate
from .const import (
    CONF_AI_TASK_ENTITY,
    CONF_CAT_LOUD,
    CONF_CAT_SENSORS,
    CONF_CAT_WATCH,
    CONF_HUTCH_CHECK,
    CONF_HUTCH_SPOT,
    CONF_NIGHT_WATCH_SPEAKERS,
    CONF_QUIET_END,
    DEFAULT_HUTCH_SPOT,
    DEFAULT_QUIET_END,
)

_LOGGER = logging.getLogger(__name__)

AFTER_SUNSET = timedelta(minutes=30)
CAT_GAP = timedelta(minutes=15)


class Pets:
    def __init__(self, hass: HomeAssistant, options: dict[str, Any], rt_getter: Callable[[], Any]) -> None:
        self.hass = hass
        self.options = options
        self._rt = rt_getter
        self.checked_day: str | None = None
        self._last_cat: datetime | None = None
        self.last: dict[str, Any] = {}
        self._unsubs: list = []

    async def async_start(self) -> None:
        self._unsubs += [
            self.hass.bus.async_listen(EVENT_STATE_CHANGED, self._changed),
            async_track_time_interval(self.hass, self._tick, timedelta(minutes=5)),
        ]

    @callback
    def async_stop(self) -> None:
        while self._unsubs:
            self._unsubs.pop()()

    # ----- where -------------------------------------------------------------------------
    def spot(self) -> dict[str, Any] | None:
        rt = self._rt()
        if rt is None:
            return None
        key = rt.spots.find_any(self.options.get(CONF_HUTCH_SPOT) or DEFAULT_HUTCH_SPOT)
        return rt.spots.spots.get(key or "")

    def cat_sensors(self) -> list[str]:
        if chosen := self.options.get(CONF_CAT_SENSORS):
            return list(chosen)
        spot = self.spot()
        reg = er.async_get(self.hass)
        cam = reg.async_get(spot["camera"]) if spot else None
        if cam is None or not cam.device_id:
            return []
        return [e.entity_id for e in er.async_entries_for_device(reg, cam.device_id)
                if e.domain == "binary_sensor" and "cat" in e.entity_id and not e.disabled_by]

    def dark(self) -> bool:
        sun = self.hass.states.get("sun.sun")
        return sun is not None and sun.state == "below_horizon"

    # ----- dusk ------------------------------------------------------------------------
    @callback
    def _tick(self, now: datetime) -> None:
        if not self.options.get(CONF_HUTCH_CHECK) or self.spot() is None:
            return
        local = dt_util.now()
        if self.checked_day == local.date().isoformat() or not self.dark():
            return
        sun = self.hass.states.get("sun.sun")
        last_set = dt_util.parse_datetime(str(sun.attributes.get("last_setting") or "")) if sun else None
        if last_set is None or dt_util.utcnow() - last_set < AFTER_SUNSET or dt_util.utcnow() - last_set > timedelta(hours=4):
            return
        self.checked_day = local.date().isoformat()
        self.hass.async_create_task(self.async_hutch(), eager_start=False)

    async def async_hutch(self, force: bool = False) -> dict[str, Any]:
        rt, spot = self._rt(), self.spot()
        if rt is None or spot is None:
            return {"error": "No hutch spot: draw one called 'rabbit pen' (or choose it in the settings)"}
        if not force and not self.options.get(CONF_HUTCH_CHECK):
            return {"skipped": "the dusk hutch check is off"}
        if not rt.watches.spend("animals", force=force):
            return {"skipped": "daily AI limit reached"}
        result = await self._ask(spot, (
            "This is a rabbit hutch or pen at dusk. Are the animals safely inside, with the hutch door or lid closed? "
            "Set ok true if it all looks shut and safe. If not, say what looks wrong in one short spoken sentence "
            "(e.g. 'The hutch door looks open.'). If it's too dark to tell, set ok true and unsure true."),
            {"ok": {"description": "All shut and safe", "required": True, "selector": {"boolean": {}}},
             "problem": {"description": "One short spoken sentence on what looks wrong, or empty", "required": False,
                         "selector": {"text": {}}},
             "unsure": {"description": "Too dark or unclear", "required": False, "selector": {"boolean": {}}}})
        self.last["hutch"] = {"time": dt_util.utcnow().isoformat(), **result}
        ok = result.get("ok") in (True, "true", "yes")
        if not ok and (problem := str(result.get("problem") or "").strip()):
            rt.timeline.add("animal", problem, "dusk hutch check")
            await rt.speech.async_say(problem, "normal", None, source="animals")
        return {"ok": ok, "problem": result.get("problem")}

    # ----- cats at night -----------------------------------------------------------------
    @callback
    def _changed(self, event: Event) -> None:
        if not self.options.get(CONF_CAT_WATCH):
            return
        new, old = event.data.get("new_state"), event.data.get("old_state")
        if new is None or new.state != "on" or (old is not None and old.state == "on"):
            return
        if new.entity_id not in self.cat_sensors() or not self.dark():
            return
        now = dt_util.utcnow()
        if self._last_cat and now - self._last_cat < CAT_GAP:
            return
        self._last_cat = now
        self.hass.async_create_task(self.async_cat(), eager_start=False)

    async def async_cat(self, force: bool = False) -> dict[str, Any]:
        rt, spot = self._rt(), self.spot()
        if rt is None or spot is None:
            return {"error": "No hutch spot"}
        if not force and await rt.watches.unchanged("cat-watch", spot["camera"], spot["name"]):
            return {"skipped": "the picture hasn't changed"}
        if not rt.watches.spend("animals", force=force):
            return {"skipped": "daily AI limit reached"}
        result = await self._ask(spot, (
            "Night camera picture of a rabbit hutch or pen. Is there a cat at or right next to the hutch or pen? "
            "Only true if you can clearly see a cat there."),
            {"cat": {"description": "A cat clearly at or by the hutch", "required": True, "selector": {"boolean": {}}}})
        seen = result.get("cat") in (True, "true", "yes")
        self.last["cat"] = {"time": dt_util.utcnow().isoformat(), "cat": seen}
        if not seen:
            return {"cat": False}
        local = dt_util.now()
        text = f"A cat was by the hutch at {local:%-I:%M%p}".replace("AM", "am").replace("PM", "pm") + "."
        rt.timeline.add("animal", text, "night cat watch")
        if self.options.get(CONF_CAT_LOUD):
            speakers = [s for s in self.options.get(CONF_NIGHT_WATCH_SPEAKERS) or [] if self.hass.states.get(s)] or None
            await rt.speech.async_say(f"Heads up: there's a cat by the hutch.", "urgent", speakers, source="animals")
        elif rt.speech.quiet_hours_now():
            from .followyou import next_quiet_end

            morning = next_quiet_end(local, self.options.get(CONF_QUIET_END) or DEFAULT_QUIET_END)
            rt.followyou.keep(f"Overnight, {text[0].lower()}{text[1:]}", source="animals", not_before=morning, hours=4)
        else:
            await rt.speech.async_say("There's a cat by the hutch.", "normal", None, source="animals")
        return {"cat": True}

    async def _ask(self, spot: dict[str, Any], instructions: str, structure: dict[str, Any]) -> dict[str, Any]:
        rt = self._rt()
        from .spots import _key

        data: dict[str, Any] = {
            "task_name": "Sentient: the hutch",
            "instructions": instructions,
            "structure": structure,
            "attachments": [await rt.spots.async_attachment(spot["camera"], _key(spot["name"]))],
        }
        if ai := self.options.get(CONF_AI_TASK_ENTITY):
            data["entity_id"] = ai
        try:
            response = await async_generate(self.hass, data)
        except Exception as err:  # noqa: BLE001
            _LOGGER.debug("Hutch look failed: %s", err)
            return {}
        result = (response or {}).get("data")
        return result if isinstance(result, dict) else {}
