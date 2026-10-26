"""Camera checks: a standing yes-or-no question for a camera, asked when its own sensor sees
something — "is there a delivery van?" when the front camera spots a car. A yes goes on the
timeline (and so to phones) and can be said out loud. One AI look per trigger, with a cooldown,
counted against the daily AI limit.
"""

from __future__ import annotations

from datetime import timedelta
import logging
from typing import Any, Callable

from homeassistant.const import EVENT_STATE_CHANGED
from homeassistant.core import Event, HomeAssistant, callback
from homeassistant.exceptions import HomeAssistantError
from homeassistant.util import dt as dt_util

from .ai import async_generate
from .const import CONF_AI_TASK_ENTITY
from .items import Items
from .kinds import in_windows

_LOGGER = logging.getLogger(__name__)


def triggered(new, old) -> bool:
    if new is None or old is None or old.state in ("unavailable", "unknown") or new.state in ("unavailable", "unknown"):
        return False
    if new.domain == "event":
        return new.state != old.state
    if new.domain == "sensor":  # a count of objects (e.g. Frigate's "car count") going up
        try:
            return float(new.state) > float(old.state)
        except ValueError:
            return False
    return new.state == "on" and old.state != "on"


class CameraChecks:
    def __init__(self, hass: HomeAssistant, items: Items, runtime: Callable[[], Any]) -> None:
        self.hass = hass
        self.items = items
        self._rt = runtime
        self._busy: set[str] = set()
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
        entity_id = event.data.get("entity_id")
        for check in self.items.enabled():
            if entity_id in (check.get("triggers") or []) and triggered(event.data.get("new_state"), event.data.get("old_state")):
                if self._ready(check):
                    self.hass.async_create_task(self.async_check(check))

    def _ready(self, check: dict[str, Any]) -> bool:
        now = dt_util.utcnow()
        if check["id"] in self._busy or not in_windows(check.get("times"), dt_util.as_local(now)):
            return False
        last = dt_util.parse_datetime(check.get("last") or "")
        return not (last and now - last < timedelta(minutes=float(check.get("cooldown_minutes") or 0)))

    async def async_check(self, check: dict[str, Any], force: bool = False) -> dict[str, Any]:
        rt = self._rt()
        camera = check.get("camera")
        if not camera or self.hass.states.get(camera) is None:
            return {"error": "That camera isn't available"}
        if not rt.watches.spend("camera checks", force=force):
            return {"skipped": "daily AI limit reached"}
        self._busy.add(check["id"])
        self.items.update(check["id"], last=dt_util.utcnow().isoformat())
        try:
            data: dict[str, Any] = {
                "task_name": f"Sentient check: {check['name']}"[:100],
                "instructions": (
                    f"Look at this home camera picture and answer: {check.get('question')} "
                    "Set match to true only if you can clearly see it. Describe what you see in a few plain words."
                ),
                "structure": {
                    "match": {"description": "True only if clearly yes", "required": True, "selector": {"boolean": {}}},
                    "description": {"description": "A few plain words", "required": False, "selector": {"text": {}}},
                },
                "attachments": [await rt.watches._attachment(camera, check.get("spot") or None)],
            }
            if ai := rt.options.get(CONF_AI_TASK_ENTITY):
                data["entity_id"] = ai
            response = await async_generate(self.hass, data)
            result = (response or {}).get("data") or {}
        except (HomeAssistantError, Exception) as err:  # noqa: BLE001
            _LOGGER.warning("Camera check %s failed: %s", check["name"], err)
            return {"error": str(err)}
        finally:
            self._busy.discard(check["id"])
        match = bool(result.get("match")) if isinstance(result, dict) else False
        what = (result.get("description") if isinstance(result, dict) else "") or ""
        self.items.update(check["id"], last_answer=("yes: " if match else "no: ") + what)
        if match:
            text = f"{check['name']}: {what.strip().rstrip('.') or 'seen'}"
            rt.timeline.add("watch", text, self.hass.states.get(camera).name)
            if check.get("say"):
                speakers = [s for s in check.get("speakers") or [] if self.hass.states.get(s)] or None
                await rt.speech.async_say(f"Heads up: {what.strip().rstrip('.') or check['name']}.", "normal", speakers,
                                          source=f"check:{check['name']}")
        return {"check": check["name"], "match": match, "seen": what}
