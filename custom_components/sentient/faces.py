"""Faces: putting names to people the cameras already recognise.

Sentient doesn't recognise faces itself. Frigate does (its "<name> last camera" sensors), and so do some
doorbells (a "last recognised face" sensor). When one of those names someone, Sentient notes it:
"Corey seen at the gate, 17:32" goes on the timeline, and the voice can answer "has Corey come home?"
or "when did Nan last come round?". Names are only ever said when someone asks — never announced, never
guessed (an unknown face stays "someone"). On/off, and which people, in the settings. Off by default.
"""

from __future__ import annotations

from datetime import timedelta
import logging
import re
from typing import Any, Callable

from homeassistant.const import EVENT_STATE_CHANGED
from homeassistant.core import Event, HomeAssistant, callback
from homeassistant.helpers.storage import Store
from homeassistant.util import dt as dt_util

from .const import CONF_DOOR_CAMERA, CONF_FACE_PEOPLE, CONF_FACE_SENSOR, CONF_FACES, DOMAIN

_LOGGER = logging.getLogger(__name__)

FRIGATE = re.compile(r"^sensor\.frigate_(.+)_last_camera$")
NOT_A_NAME = {"", "unknown", "unavailable", "none", "no one", "nobody", "null"}
REPEAT = timedelta(minutes=20)  # the same person on the same camera again within this is the same visit


class Faces:
    def __init__(self, hass: HomeAssistant, entry_id: str, options: dict[str, Any], rt_getter: Callable[[], Any]) -> None:
        self.hass = hass
        self.options = options
        self._rt = rt_getter
        self._store: Store[dict[str, Any]] = Store(hass, 1, f"{DOMAIN}.{entry_id}.faces")
        self.seen: dict[str, dict[str, str]] = {}  # name -> {camera, time}
        self._unsub = None

    async def async_start(self) -> None:
        self.seen = (await self._store.async_load() or {}).get("seen", {})
        self._unsub = self.hass.bus.async_listen(EVENT_STATE_CHANGED, self._changed)

    @callback
    def async_stop(self) -> None:
        if self._unsub:
            self._unsub()
            self._unsub = None

    @property
    def enabled(self) -> bool:
        return bool(self.options.get(CONF_FACES, False))

    def allowed(self, name: str) -> bool:
        people = [p.strip().lower() for p in self.options.get(CONF_FACE_PEOPLE) or [] if p.strip()]
        return not people or name.lower() in people

    @callback
    def _changed(self, event: Event) -> None:
        if not self.enabled:
            return
        new = event.data.get("new_state")
        if new is None or str(new.state).strip().lower() in NOT_A_NAME:
            return
        old = event.data.get("old_state")
        if old is not None and old.state == new.state and new.entity_id != self.options.get(CONF_FACE_SENSOR):
            return
        if m := FRIGATE.match(new.entity_id):
            name = (new.name or m.group(1)).removeprefix("Frigate ").removesuffix(" Last Camera").removesuffix(" last camera")
            camera = str(new.state)
        elif new.entity_id == self.options.get(CONF_FACE_SENSOR):
            name = str(new.state)
            door = self.hass.states.get(self.options.get(CONF_DOOR_CAMERA) or "")
            camera = door.name if door else "the door"
        else:
            return
        name = " ".join(w.capitalize() for w in re.split(r"[_\s]+", name.strip()) if w)
        if not name or not self.allowed(name):
            return
        self.saw(name, camera.replace("_", " "))

    @callback
    def saw(self, name: str, camera: str) -> None:
        now = dt_util.utcnow()
        last = self.seen.get(name)
        if last and last.get("camera") == camera and now - (dt_util.parse_datetime(last["time"]) or now) < REPEAT:
            self.seen[name]["time"] = now.isoformat()
            return
        self.seen[name] = {"camera": camera, "time": now.isoformat()}
        self._store.async_delay_save(lambda: {"seen": self.seen}, 5)
        if (rt := self._rt()) is not None:
            rt.timeline.add("noticed", f"{name} seen at the {camera.lower()}", "face recognition")

    def recent(self, hours: float = 24) -> list[dict[str, str]]:
        cutoff = dt_util.utcnow() - timedelta(hours=hours)
        out = []
        for name, s in self.seen.items():
            when = dt_util.parse_datetime(s.get("time") or "")
            if when and when >= cutoff and self.allowed(name):
                out.append({"name": name, "camera": s["camera"], "time": dt_util.as_local(when).strftime("%H:%M")})
        return sorted(out, key=lambda s: s["time"], reverse=True)

    def prompt_line(self) -> str | None:
        if not self.enabled or not (seen := self.recent()):
            return None
        return ("Recognised by the cameras in the last day (face recognition) — only mention this when someone asks "
                "who's been about or whether someone's home; never announce it: "
                + "; ".join(f"{s['name']} at the {s['camera'].lower()} {s['time']}" for s in seen[:8]) + ".")
