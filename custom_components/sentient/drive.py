"""The drive: which cars are parked there, kept up to date cheaply.

Uses a camera spot (named "drive" by default — any spot drawn on the Spots tab will do). When the
camera's motion or car sensors settle down after activity, Sentient takes one AI look at just that
spot: how many cars, and a few words on each. Arrivals and departures go on the timeline, the
answer goes to every voice ("is my car on the drive?") and a sensor shows it.

Cheap: one look per burst of activity at most, never more than once every few minutes, and only
when the drive actually looks different from last time (a quick picture comparison, no AI). Looks
count towards the daily AI limit. Nothing set up (no spot): it does nothing.
"""

from __future__ import annotations

from datetime import timedelta
import logging
from typing import Any, Callable

from homeassistant.const import EVENT_STATE_CHANGED, STATE_ON
from homeassistant.core import Event, EventStateChangedData, HomeAssistant, callback
from homeassistant.helpers import entity_registry as er
from homeassistant.helpers.event import async_call_later
from homeassistant.helpers.storage import Store
from homeassistant.util import dt as dt_util

from .ai import async_generate
from .const import (
    CONF_AI_TASK_ENTITY,
    CONF_DRIVE_CAMERA,
    CONF_DRIVE_SPOT,
    CONF_DRIVE_TRIGGERS,
    CONF_KNOWN_CARS,
    DEFAULT_DRIVE_SPOT,
    DOMAIN,
)

_LOGGER = logging.getLogger(__name__)

EVENT_DRIVE = f"{DOMAIN}_drive"
SETTLE_SECONDS = 25  # after the last movement: the car's parked, the doors are shut
COOLDOWN = timedelta(minutes=3)
CHANGED = 0.06  # share of the drive picture that must look different before the AI is asked
MAX_COOLDOWN = timedelta(hours=1)  # cars passing on the road: each look that finds nothing new waits twice as long
_TRIGGER_WORDS = ("motion", "car", "vehicle", "occupancy", "all_occupancy")


class Drive:
    def __init__(self, hass: HomeAssistant, entry_id: str, options: dict[str, Any], rt_getter: Callable[[], Any]) -> None:
        self.hass = hass
        self.options = options
        self._rt = rt_getter
        self._store: Store[dict[str, Any]] = Store(hass, 1, f"{DOMAIN}.{entry_id}.drive")
        self.state: dict[str, Any] = {}  # cars, description, checked
        self._unsub = None
        self._pending = None
        self._last_look = None
        self._same = 0  # looks in a row that found nothing new
        self.skipped = 0  # looks saved because the picture hadn't changed
        self._triggers: set[str] = set()

    async def async_start(self) -> None:
        self.state = (await self._store.async_load() or {}).get("state", {})
        self.async_setup_triggers()

    @callback
    def async_setup_triggers(self) -> None:
        self.async_stop()
        if self.spot() is None:
            return
        self._triggers = set(self.triggers())
        if not self._triggers:
            return

        @callback
        def _wanted(data) -> bool:
            return data["entity_id"] in self._triggers

        self._unsub = self.hass.bus.async_listen(EVENT_STATE_CHANGED, self._changed, event_filter=_wanted)

    @callback
    def async_stop(self) -> None:
        if self._unsub:
            self._unsub()
            self._unsub = None
        if self._pending:
            self._pending()
            self._pending = None

    # ----- set-up -----------------------------------------------------------------------
    def spot(self) -> dict[str, Any] | None:
        rt = self._rt()
        if rt is None:
            return None
        name = self.options.get(CONF_DRIVE_SPOT) or DEFAULT_DRIVE_SPOT
        key = rt.spots.find_any(name)
        spot = rt.spots.spots.get(key or "")
        if spot is None:
            return None
        camera = self.options.get(CONF_DRIVE_CAMERA)
        return spot if not camera or spot["camera"] == camera else None

    def triggers(self) -> list[str]:
        """The sensors chosen, or the camera's own motion/car sensors."""
        if chosen := self.options.get(CONF_DRIVE_TRIGGERS):
            return list(chosen)
        spot = self.spot()
        if spot is None:
            return []
        reg = er.async_get(self.hass)
        cam = reg.async_get(spot["camera"])
        if cam is None or not cam.device_id:
            return []
        return [
            e.entity_id for e in er.async_entries_for_device(reg, cam.device_id)
            if e.domain == "binary_sensor" and not e.disabled_by
            and any(w in e.entity_id for w in _TRIGGER_WORDS) and "person" not in e.entity_id
            and "cat" not in e.entity_id and "dog" not in e.entity_id and "bicycle" not in e.entity_id
        ]

    # ----- watching ---------------------------------------------------------------------
    @callback
    def _changed(self, event: Event[EventStateChangedData]) -> None:
        new = event.data["new_state"]
        if new is None:
            return
        if self._pending:
            self._pending()
            self._pending = None
        if new.state == STATE_ON:
            return  # still moving: wait for it to settle
        if any((s := self.hass.states.get(t)) is not None and s.state == STATE_ON for t in self._triggers):
            return

        @callback
        def _go(_now) -> None:
            self._pending = None
            self.hass.async_create_task(self.async_check(), eager_start=False)

        self._pending = async_call_later(self.hass, SETTLE_SECONDS, _go)

    async def async_check(self, force: bool = False) -> dict[str, Any]:
        rt = self._rt()
        spot = self.spot()
        if rt is None or spot is None:
            return {"error": "No drive spot: draw one called 'drive' on the camera that can see it"}
        now = dt_util.utcnow()
        if not force and self._last_look and now - self._last_look < self.cooldown():
            return self.describe()
        from .spots import _key, changed_share

        fingerprint = await rt.spots.async_fingerprint(spot["camera"], _key(spot["name"]))
        if (not force and fingerprint and self.state.get("fingerprint")
                and changed_share(fingerprint, self.state["fingerprint"]) < CHANGED):
            self.skipped += 1
            return self.describe()  # the drive looks just as it did last time: no need to ask the AI
        self._last_look = now

        data: dict[str, Any] = {
            "task_name": "Sentient: the drive",
            "instructions": (
                "This picture shows only the drive of a house. Count the cars or vans parked on it (ignore "
                "anything on the road beyond). For each, a few plain words — colour and type (e.g. 'silver "
                "estate', 'small white van'). If it's too dark or unclear to tell, set unsure."
            ),
            "structure": {
                **({"whose": {"description": "Whose cars these are, from the known cars, comma-separated; empty if none clearly match",
                              "required": False, "selector": {"text": {}}}} if self.known() else {}),
                "cars": {"description": "How many cars or vans are on the drive", "required": True,
                         "selector": {"number": {"min": 0, "max": 10}}},
                "description": {"description": "A few words on each, comma-separated; empty if none", "required": False,
                                "selector": {"text": {}}},
                "unsure": {"description": "Too dark or unclear to be sure", "required": False,
                           "selector": {"boolean": {}}},
            },
            "attachments": [await rt.spots.async_attachment(spot["camera"], _key(spot["name"]))],
        }
        if known := self.known():
            data["instructions"] += (" Known household cars: " + "; ".join(f"{who} — {what}" for who, what in known)
                                     + ". In 'whose', name a car only if it clearly matches one of these.")
        if ai_entity := self.options.get(CONF_AI_TASK_ENTITY):
            data["entity_id"] = ai_entity
        if not rt.watches.spend("drive", force=force):
            return self.describe()
        try:
            response = await async_generate(self.hass, data)
        except Exception as err:  # noqa: BLE001
            _LOGGER.debug("Drive look failed: %s", err)
            return {"error": str(err)}
        result = (response or {}).get("data") or {}
        if not isinstance(result, dict):
            return self.describe()
        unsure = result.get("unsure") in (True, "true", "yes")
        try:
            cars = max(0, int(float(result.get("cars"))))
        except (TypeError, ValueError):
            return self.describe()
        description = " ".join(str(result.get("description") or "").split()).strip(" .")
        names = {who.lower(): who for who, _ in self.known()}
        whose = [names[w.strip().lower()] for w in str(result.get("whose") or "").split(",") if w.strip().lower() in names]
        before = self.state.get("cars")
        self._same = 0 if before is None or cars != before else self._same + 1
        if unsure and before is not None:
            self.state["checked"] = now.isoformat()  # keep what we knew; it's just dark
        else:
            had = set(self.state.get("whose") or [])
            self.state = {"cars": cars, "description": description, "checked": now.isoformat(), "whose": whose,
                          "changed": now.isoformat() if cars != before else self.state.get("changed")}
            if before is not None and cars != before:
                came = [w for w in whose if w not in had]
                if cars > before and came:
                    text = f"{came[0]}'s car arrived on the drive{f' — now {description}' if description else ''}"
                elif cars > before:
                    text = f"A car arrived on the drive{f' — now {description}' if description else ''}"
                else:
                    gone = [w for w in had if w not in whose]
                    text = (f"{gone[0]}'s car left the drive" if gone else "A car left the drive") + (
                        f" — {description} still there" if cars and description else " — it's clear now" if not cars else "")
                rt.timeline.add("drive", text, f"{cars} on the drive")
        if fingerprint:
            self.state["fingerprint"] = fingerprint
        self._store.async_delay_save(lambda: {"state": self.state}, 2)
        self.hass.bus.async_fire(EVENT_DRIVE, self.describe())
        return self.describe()

    def known(self) -> list[tuple[str, str]]:
        """[(whose, what it looks like)] from the settings: one 'Sam: dark grey hatchback' per line."""
        out = []
        for line in str(self.options.get(CONF_KNOWN_CARS) or "").splitlines():
            who, _, what = line.partition(":")
            if who.strip() and what.strip():
                out.append((who.strip(), what.strip()))
        return out

    def cooldown(self) -> timedelta:
        return min(MAX_COOLDOWN, COOLDOWN * (2 ** min(self._same, 6)))

    def describe(self) -> dict[str, Any]:
        if not self.state:
            return {"cars": None}
        checked = dt_util.parse_datetime(self.state.get("checked") or "")
        return {
            "cars": self.state.get("cars"),
            "description": self.state.get("description") or "",
            "checked": dt_util.as_local(checked).strftime("%H:%M") if checked else None,
            "checked_at": self.state.get("checked"),
            "changed_at": self.state.get("changed"),
            "whose": self.state.get("whose") or [],
        }

    def prompt_line(self) -> str | None:
        d = self.describe()
        if d.get("cars") is None:
            return None
        what = "nothing parked" if not d["cars"] else f"{d['cars']} — {d['description']}" if d["description"] else str(d["cars"])
        if d.get("whose"):
            what += " (" + ", ".join(f"{w}'s" for w in d["whose"]) + ")"
        return (f"On the drive (last checked {d['checked']}): {what}. For a fresh look, call sentient_look "
                "with the drive spot.")
