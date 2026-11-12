"""The house ledger: every change in the house, and what or who caused it.

Sentient's timeline only knows what Sentient did. The ledger records every device's changes, whoever made them,
so "when did the heating go off, and why?" always has an answer. The cause comes from the change's Home
Assistant context:

* ``sentient`` — Sentient itself (with the feature that did it);
* ``voice`` — a voice or chat request (with the room when known);
* ``person`` — someone in Home Assistant (dashboard, app), by user name;
* ``automation`` / ``script`` — which one;
* ``home_assistant`` — a service call from another integration;
* ``device`` — nothing in Home Assistant did it: the device itself, its own app, or someone at the device.

Fast-changing numbers (power, temperature) are recorded at most hourly unless they jump, and diagnostic numbers
not at all; Home Assistant's own history keeps the full detail. Nothing here uses AI. Switch: "House ledger" (Settings →
Memory & learning); turning it off stops recording, and "Clear the ledger" removes what was kept.
"""
from __future__ import annotations

from collections import OrderedDict, deque
import logging
from typing import Any, Callable

from homeassistant.const import EVENT_CALL_SERVICE, EVENT_STATE_CHANGED
from homeassistant.core import Context, Event, HomeAssistant, State, callback
from homeassistant.helpers import area_registry as ar, device_registry as dr, entity_registry as er
from homeassistant.helpers.event import async_call_later

from .const import DOMAIN

_LOGGER = logging.getLogger(__name__)

CONF_LEDGER = "house_ledger"
DEAD = ("unavailable", "unknown")
NUMBER_EVERY = 3600  # a number is recorded at most hourly unless it jumps
NUMBER_JUMP = 0.25  # ...or changes by a quarter or more
FLUSH_SECONDS = 5
ASKED_SECONDS = 300  # a device reporting within five minutes of being asked is doing what it was asked
RECENT = 3000
CAUSE_WORDS = {
    "sentient": "Sentient", "voice": "a voice request", "person": "someone in Home Assistant",
    "automation": "an automation", "script": "a script", "home_assistant": "Home Assistant",
    "device": "nothing in Home Assistant (the device itself, its app, or someone at the device)",
}
EVENT_AUTOMATION_TRIGGERED = "automation_triggered"
EVENT_SCRIPT_STARTED = "script_started"


def _number(value: str | None) -> float | None:
    try:
        return float(value) if value not in (None, *DEAD) else None
    except (TypeError, ValueError):
        return None


def describe(row: dict[str, Any]) -> str:
    """One plain line: "Heating: heat to off — nothing in Home Assistant (the device itself…)"."""
    cause = row.get("cause") or "device"
    by = f"{CAUSE_WORDS.get(cause, cause)}: {row['who']}" if row.get("who") and cause != "device" else CAUSE_WORDS.get(cause, cause)
    return f"{row.get('name') or row['entity']}: {row.get('old')} to {row.get('new')}, by {by}"


class Ledger:
    def __init__(self, hass: HomeAssistant, db: Any, own: Any = None) -> None:
        self.hass = hass
        self.db = db
        self.own = own
        self.enabled = False
        self.recent: deque[dict[str, Any]] = deque(maxlen=RECENT)
        self.hooks: list[Callable[[dict[str, Any]], None]] = []
        self.score: Callable[[dict[str, Any]], float | None] | None = None  # surprise, set by expectations
        self._buffer: list[dict[str, Any]] = []
        self._flush_unsub = None
        self._unsubs: list[Callable[[], None]] = []
        self._contexts: OrderedDict[str, tuple[str, str | None]] = OrderedDict()  # context id -> (cause, who)
        self._users: dict[str, str] = {}
        self._last_number: dict[str, tuple[float, float]] = {}  # entity -> (ts, value) last recorded
        self._asked: dict[str, tuple[float, str, str | None]] = {}  # entity -> (when, cause, who) of the last call for it
        self._skip_platforms = {DOMAIN}

    # ----- start and stop ---------------------------------------------------------------------
    async def async_start(self) -> None:
        if self.enabled:
            return
        self.enabled = True
        for user in await self.hass.auth.async_get_users():
            self._users[user.id] = user.name or "a user"
        bus = self.hass.bus
        self._unsubs = [
            bus.async_listen(EVENT_STATE_CHANGED, self._changed),
            bus.async_listen(EVENT_AUTOMATION_TRIGGERED, self._automation),
            bus.async_listen(EVENT_SCRIPT_STARTED, self._script),
            bus.async_listen(EVENT_CALL_SERVICE, self._service),
            bus.async_listen("user_added", self._users_changed),
            bus.async_listen("user_updated", self._users_changed),
        ]

    @callback
    def _users_changed(self, _event: Event) -> None:
        async def _refresh() -> None:
            for user in await self.hass.auth.async_get_users():
                self._users[user.id] = user.name or "a user"

        self.hass.async_create_background_task(_refresh(), "sentient ledger users")

    async def async_stop(self) -> None:
        self.enabled = False
        for unsub in self._unsubs:
            unsub()
        self._unsubs = []
        if self._flush_unsub:
            self._flush_unsub()
            self._flush_unsub = None
        await self.async_flush()

    # ----- causes -----------------------------------------------------------------------------
    def note_context(self, context: Context | None, cause: str, who: str | None = None) -> None:
        """Remember what a context stands for (a voice request in the kitchen, an automation…)."""
        if context is None:
            return
        self._contexts[context.id] = (cause, who)
        self._contexts.move_to_end(context.id)
        while len(self._contexts) > 2000:
            self._contexts.popitem(last=False)

    @callback
    def _automation(self, event: Event) -> None:
        self.note_context(event.context, "automation", event.data.get("name") or event.data.get("entity_id"))

    @callback
    def _script(self, event: Event) -> None:
        self.note_context(event.context, "script", event.data.get("name") or event.data.get("entity_id"))

    @callback
    def _service(self, event: Event) -> None:
        ctx = event.context
        if not (ctx.id in self._contexts or (ctx.parent_id and ctx.parent_id in self._contexts)):
            if ctx.user_id:
                self.note_context(ctx, "person", self._users.get(ctx.user_id))
            else:
                self.note_context(ctx, "home_assistant", event.data.get("domain"))
        # cloud devices often report a change a little later, under a new context: remember who asked for it
        data = event.data.get("service_data") or {}
        targets = data.get("entity_id") or (data.get("target") or {}).get("entity_id") or []
        if isinstance(targets, str):
            targets = [targets]
        if targets:
            cause = self.cause_of(ctx)
            now = event.time_fired.timestamp()
            for entity_id in targets[:50]:
                if isinstance(entity_id, str):
                    self._asked[entity_id] = (now, *cause)
            if len(self._asked) > 2000:
                for key in list(self._asked)[:1000]:
                    self._asked.pop(key, None)

    def cause_of(self, context: Context | None) -> tuple[str, str | None]:
        if context is None:
            return "device", None
        if self.own is not None and self.own.is_own(context):
            return "sentient", self.own.why(context)
        for cid in (context.id, context.parent_id):
            if cid and cid in self._contexts:
                cause, who = self._contexts[cid]
                if cause == "home_assistant" and context.user_id:
                    return "person", self._users.get(context.user_id)
                return cause, who
        if context.user_id:
            return "person", self._users.get(context.user_id)
        if context.parent_id:
            return "home_assistant", None
        return "device", None

    # ----- recording --------------------------------------------------------------------------
    def _area(self, entity_id: str) -> str | None:
        entry = er.async_get(self.hass).async_get(entity_id)
        if entry is None:
            return None
        area_id = entry.area_id
        if not area_id and entry.device_id and (dev := dr.async_get(self.hass).async_get(entry.device_id)):
            area_id = dev.area_id
        area = ar.async_get(self.hass).async_get_area(area_id) if area_id else None
        return area.name if area else None

    def _worth_recording(self, old: State, new: State) -> bool:
        if old.state == new.state:
            return False
        entry = er.async_get(self.hass).async_get(new.entity_id)
        if entry is not None and entry.platform in self._skip_platforms:
            return False
        if new.state in DEAD or old.state in DEAD:
            return True
        a, b = _number(old.state), _number(new.state)
        if a is None or b is None or new.domain not in ("sensor", "number", "input_number"):
            return True
        if entry is not None and entry.entity_category is not None:
            return False  # diagnostic numbers (signal strength, voltages…): Home Assistant's history has them
        now = new.last_changed.timestamp()
        last = self._last_number.get(new.entity_id)
        if last is None or now - last[0] >= NUMBER_EVERY:
            return True
        return abs(b - last[1]) >= max(abs(last[1]) * NUMBER_JUMP, 1e-9) and abs(b - last[1]) >= 1

    @callback
    def _changed(self, event: Event) -> None:
        old, new = event.data.get("old_state"), event.data.get("new_state")
        if old is None or new is None or not self.enabled:
            return
        try:
            if not self._worth_recording(old, new):
                return
            if (num := _number(new.state)) is not None:
                self._last_number[new.entity_id] = (new.last_changed.timestamp(), num)
            cause, who = self.cause_of(new.context)
            asked = self._asked.pop(new.entity_id, None)  # only the first change after a request can be its echo
            if cause == "device" and asked is not None and 0 <= new.last_changed.timestamp() - asked[0] <= ASKED_SECONDS:
                cause, who = asked[1], asked[2]  # the device caught up with a request from a few minutes ago
            row = {"ts": new.last_changed.timestamp(), "entity": new.entity_id, "name": new.name,
                   "domain": new.domain, "area": self._area(new.entity_id), "old": old.state, "new": new.state,
                   "cause": cause, "who": who, "surprise": None}
            if self.score is not None:
                row["surprise"] = self.score(row)
            self.record(row)
        except Exception:  # noqa: BLE001 - the ledger must never disturb Home Assistant
            _LOGGER.debug("Ledger could not record %s", new.entity_id, exc_info=True)

    def record(self, row: dict[str, Any]) -> None:
        self.recent.append(row)
        self._buffer.append(row)
        for hook in list(self.hooks):
            try:
                hook(row)
            except Exception:  # noqa: BLE001
                _LOGGER.debug("Ledger hook failed", exc_info=True)
        if self._flush_unsub is None:
            self._flush_unsub = async_call_later(self.hass, FLUSH_SECONDS, self._flush_later)

    @callback
    def _flush_later(self, _now: Any) -> None:
        self._flush_unsub = None
        self.hass.async_create_background_task(self.async_flush(), "sentient ledger flush")

    async def async_flush(self) -> None:
        rows, self._buffer = self._buffer, []
        if not rows:
            return
        tuples = [(r["ts"], r["entity"], r.get("name"), r.get("domain"), r.get("area"), r.get("old"), r.get("new"),
                   r.get("cause"), r.get("who"), r.get("surprise")) for r in rows]
        try:
            await self.db.call(lambda conn: self.db.add_changes(conn, tuples))
        except Exception:  # noqa: BLE001
            _LOGGER.warning("Sentient could not save %s house changes", len(rows), exc_info=True)

    # ----- reading ----------------------------------------------------------------------------
    async def async_changes(self, **kwargs: Any) -> list[dict[str, Any]]:
        await self.async_flush()
        return await self.db.call(lambda conn: self.db.changes(conn, **kwargs))

    def recent_rows(self, since: float, entity: str | None = None) -> list[dict[str, Any]]:
        return [r for r in self.recent if r["ts"] >= since and (entity is None or r["entity"] == entity)]


def resolve_entities(hass: HomeAssistant, words: str) -> list[str]:
    """Entity ids matching a spoken name or area ("the heating", "landing light", "kitchen")."""
    text = (words or "").strip().casefold()
    if not text:
        return []
    if hass.states.get(text) is not None:
        return [text]
    text = text.removeprefix("the ").strip()
    exact, partial = [], []
    areas = {a.id: a.name.casefold() for a in ar.async_get(hass).async_list_areas()}
    ents = er.async_get(hass)
    devs = dr.async_get(hass)
    for st in hass.states.async_all():
        name = (st.name or "").casefold()
        entry = ents.async_get(st.entity_id)
        area_id = entry.area_id if entry else None
        if entry and not area_id and entry.device_id and (dev := devs.async_get(entry.device_id)):
            area_id = dev.area_id
        area = areas.get(area_id or "", "")
        if name == text or st.entity_id.split(".", 1)[1].replace("_", " ") == text:
            exact.append(st.entity_id)
        elif text in name or (area and text == area) or (area and text in f"{area} {name}"):
            partial.append(st.entity_id)
    return exact or partial[:30]
