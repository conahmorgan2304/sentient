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
import re
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
OWNER_QUESTION = re.compile(
    r"whose (?:cars?|vehicles?|vans?) (?:is|are) (?:(?:currently|parked) )?"
    r"(?:on|in) (?:the|my|our) drive(?:way)?(?: (?:right )?now)?[?.!]*", re.I)
_GENERIC = {"a", "an", "the", "car", "vehicle", "dark", "light", "small", "large", "big", "only", "one"}
_COLOURS = {"black", "blue", "brown", "gold", "green", "grey", "orange", "pink", "purple", "red", "silver", "white", "yellow"}
_UNCERTAIN = re.compile(r"\b(?:and|or|possibly|maybe|probably|appears|looks like|not|unclear|unknown)\b", re.I)


async def async_owner_answer(hass, rt, context, device_id, *, private=False) -> str:
    from .llm_api import _entity_read_permission, _exposed
    from .voice_id import private_history

    unavailable = "I can't verify whose vehicles are on the drive."
    drive = getattr(rt, "drive", None)
    if private or private_history(rt, device_id) or drive is None or not (spot := drive.spot()):
        return unavailable
    camera = spot["camera"]
    readable = await _entity_read_permission(hass, context)
    if private_history(rt, device_id) or drive.spot() != spot or not readable(camera) or not _exposed(hass, camera):
        return unavailable
    return drive.owner_answer()


def _vehicle_words(description: str) -> set[str]:
    words = description.casefold().replace("station wagon", "estate")
    return {{"gray": "grey", "wagon": "estate"}.get(word, word)
            for word in re.findall(r"\w+", words) if word not in _GENERIC}


def _source(spot: dict[str, Any] | None) -> dict[str, Any] | None:
    return {key: spot.get(key) for key in ("camera", "box", "points")} if spot else None


def _owners(cars: int | None, description: str, known: list[tuple[str, str]]) -> list[str]:
    """Name only a unique match supported by one parked vehicle's visible details."""
    observations = [part.strip() for part in description.split(",") if part.strip()]
    if not cars or len(observations) != cars:
        return []
    candidates = [(owner, _vehicle_words(details)) for owner, details in known]
    matches = []
    for observation in observations:
        if _UNCERTAIN.search(observation):
            continue
        visible = _vehicle_words(observation)
        owners = {owner for owner, needed in candidates
                  if len(needed) >= 2 and needed <= visible
                  and (not needed & _COLOURS or needed & _COLOURS == visible & _COLOURS)}
        if len(owners) == 1:
            matches.append(owners.pop())
    return [owner for owner in matches if matches.count(owner) == 1]


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
        self._last_source = None
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
        source = _source(spot)
        same_source = source == self.state.get("source")
        now = dt_util.utcnow()
        if not force and source == self._last_source and self._last_look and now - self._last_look < self.cooldown():
            return self.describe()
        from .spots import _key, changed_share

        fingerprint = await rt.spots.async_fingerprint(spot["camera"], _key(spot["name"]))
        if (not force and same_source and not self.state.get("unclear") and fingerprint and self.state.get("fingerprint")
                and changed_share(fingerprint, self.state["fingerprint"]) < CHANGED):
            self.skipped += 1
            return self.describe()  # the drive looks just as it did last time: no need to ask the AI
        self._last_look = now
        self._last_source = source

        data: dict[str, Any] = {
            "task_name": "Sentient: the drive",
            "instructions": (
                "This picture shows only the drive of a house. Count the cars or vans parked on it (ignore "
                "anything on the road beyond). Give one comma-separated description per parked vehicle, "
                "including visible colour, body type and a make or model only if clearly identifiable. "
                "Describe visual details only; do not infer an owner. If it's too dark or unclear to tell — "
                "or the picture is black, grey, frozen, "
                "a 'camera asleep/offline' screen or otherwise doesn't clearly show the drive — set unsure rather than "
                "saying it's empty."
            ),
            "structure": {
                "cars": {"description": "How many cars or vans are on the drive", "required": True,
                         "selector": {"number": {"min": 0, "max": 10}}},
                "description": {"description": "A few words on each, comma-separated; empty if none", "required": False,
                                "selector": {"text": {}}},
                "unsure": {"description": "Too dark, unclear, or not a real live view of the drive", "required": False,
                           "selector": {"boolean": {}}},
            },
            "attachments": [await rt.spots.async_attachment(spot["camera"], _key(spot["name"]))],
        }
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
        if _source(self.spot()) != source or not isinstance(result, dict):
            return self.describe()
        unsure = result.get("unsure") in (True, "true", "yes")
        try:
            cars = None if unsure else max(0, int(float(result.get("cars"))))
        except (TypeError, ValueError, OverflowError):
            return self.describe()
        description = " ".join(str(result.get("description") or "").split()).strip(" .")
        before = self.state.get("cars") if same_source else None
        if unsure:
            self.state["unclear"] = True
        else:
            known = self.known()
            had = set(_owners(before, self.state.get("description") or "", known))
            whose = set(_owners(cars, description, known))
            self._same = self._same + 1 if cars == before else 0
            self.state = {"cars": cars, "description": description, "checked": now.isoformat(), "source": source,
                          "changed": now.isoformat() if cars != before else self.state.get("changed")}
            if before is not None and cars != before:
                if cars > before:
                    came = whose - had if cars == before + 1 and len(had) == before and had <= whose else set()
                    name = f"{came.pop()}'s car" if len(came) == 1 else "A car"
                    text = f"{name} arrived on the drive" + (f" — now {description}" if description else "")
                else:
                    gone = had - whose if before == cars + 1 and len(whose) == cars and whose <= had else set()
                    name = f"{gone.pop()}'s car" if len(gone) == 1 else "A car"
                    text = f"{name} left the drive" + (
                        f" — {description} still there" if cars and description else " — it's clear now" if not cars else "")
                rt.timeline.add("drive", text, f"{cars} on the drive")
        if fingerprint and not unsure:
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
        unclear = bool(self.state.get("unclear")) or self.state.get("source") != _source(self.spot())
        return {
            "cars": self.state.get("cars"),
            "description": self.state.get("description") or "",
            "checked": dt_util.as_local(checked).strftime("%H:%M") if checked else None,
            "checked_at": self.state.get("checked"),
            "changed_at": self.state.get("changed"),
            "whose": [] if unclear else _owners(self.state.get("cars"), self.state.get("description") or "", self.known()),
            "unclear": unclear,
        }

    def owner_answer(self) -> str:
        """Answer from the latest clear observation and say how old it is."""
        from .watches import local_when

        result = self.describe()
        checked = dt_util.parse_datetime(result.get("checked_at") or "")
        if result.get("unclear") or checked is None or (cars := result.get("cars")) is None:
            return "I can't verify whose vehicles are on the drive."
        when = local_when(checked)
        if not cars:
            return f"The driveway was empty at the last clear check, {when}."
        count = f"{cars} {'vehicle' if cars == 1 else 'vehicles'}"
        if not (owners := result["whose"]):
            return f"The last clear driveway check, {when}, showed {count}. I couldn't verify the owner."
        names = ", ".join(owners)
        rest = " I couldn't verify the other owners." if len(owners) < cars else ""
        return f"The last clear driveway check, {when}, showed {count}. The visible details match the saved entries for {names}.{rest}"

    def prompt_line(self) -> str | None:
        d = self.describe()
        if d.get("cars") is None or d.get("unclear"):
            return None
        what = "nothing parked" if not d["cars"] else f"{d['cars']} — {d['description']}" if d["description"] else str(d["cars"])
        if d.get("whose"):
            what += " (" + ", ".join(f"{w}'s" for w in d["whose"]) + ")"
        line = f"On the drive (last checked {d['checked']}): {what}."
        # 15 Nov: "what time did Kayleigh get back?" — the drive knew (15:12, her car arrived) but the voice only
        # saw what's parked now, so it looked at the gate camera and couldn't say
        rt = self._rt()
        timeline = getattr(rt, "timeline", None) if rt is not None else None
        last = timeline.since(7 * 24, ["drive"], 1) if timeline is not None else []
        if last and (when := dt_util.parse_datetime(last[0]["time"])) is not None:
            from .watches import local_when

            line += f" Last change: {last[0]['text']}, at {local_when(when)}."
        return line + (" For when a car came or went, sentient_timeline lists the drive's arrivals and departures; "
                       "for a fresh look, call sentient_look with the drive spot.")
