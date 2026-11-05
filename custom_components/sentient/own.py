"""Sentient's own actions: one record of everything the house itself switched.

Every change Sentient makes — alarms and automated routines, rules, auto-off, motion lights, away lights,
light shows, the heating, learned routines it was asked to take over — runs under a context registered
here. Anything that judges people's behaviour (learning habits, "did it actually happen?", the heating
time limit, "why did that happen?") asks here first, so the house never mistakes itself for a person: no
habit learned from its own 11pm alarm, no "didn't respond" about its own switching.

Changes are remembered for three weeks (what learning looks back over) in Sentient's own storage.
"""

from __future__ import annotations

from typing import Any

from homeassistant.const import EVENT_STATE_CHANGED
from homeassistant.core import Context, Event, HomeAssistant, callback
from homeassistant.helpers.storage import Store
from homeassistant.util import dt as dt_util

from .const import DOMAIN

STORAGE_VERSION = 1
KEEP_DAYS = 21
CONTEXT_SECONDS = 3600  # a one-off action's context is recognised for an hour (long enough for slow devices)


class OwnActions:
    def __init__(self, hass: HomeAssistant, entry_id: str) -> None:
        self.hass = hass
        self._store: Store[dict[str, Any]] = Store(hass, STORAGE_VERSION, f"{DOMAIN}.{entry_id}.own")
        self._contexts: dict[str, float] = {}  # id -> until (0 = for as long as Sentient runs)
        self._why: dict[str, str] = {}  # id -> what did it ("alarm: Heated blanket off")
        self.changes: dict[str, list[float]] = {}  # entity -> times Sentient changed it
        self._last: dict[str, str] = {}  # entity -> what last changed it (for "why")
        self._unsub = None

    async def async_start(self) -> None:
        data = await self._store.async_load() or {}
        self.changes = {k: [float(t) for t in v] for k, v in (data.get("changes") or {}).items()}
        self._last = dict(data.get("last") or {})
        self._prune()

        @callback
        def _ours(data) -> bool:
            new = data.get("new_state")
            return new is not None and self.is_own(new.context)

        self._unsub = self.hass.bus.async_listen(EVENT_STATE_CHANGED, self._changed, event_filter=_ours)

    @callback
    def async_stop(self) -> None:
        if self._unsub:
            self._unsub()
            self._unsub = None

    # ----- contexts -------------------------------------------------------------------------
    def context(self, why: str = "", lasting: bool = False) -> Context:
        """A new context for something Sentient is about to do."""
        ctx = Context()
        now = dt_util.utcnow().timestamp()
        self._contexts = {k: v for k, v in self._contexts.items() if v == 0 or v > now}
        self._contexts[ctx.id] = 0 if lasting else now + CONTEXT_SECONDS
        if why:
            self._why[ctx.id] = why
            if len(self._why) > 500:
                self._why = dict(list(self._why.items())[-250:])
        return ctx

    def is_own(self, ctx: Context | None) -> bool:
        if ctx is None:
            return False
        now = dt_util.utcnow().timestamp()
        for cid in (ctx.id, ctx.parent_id):
            until = self._contexts.get(cid) if cid else None
            if until is not None and (until == 0 or until > now):
                return True
        return False

    def why(self, ctx: Context | None) -> str | None:
        if ctx is None:
            return None
        return self._why.get(ctx.id) or (self._why.get(ctx.parent_id) if ctx.parent_id else None)

    # ----- what it changed ------------------------------------------------------------------
    @callback
    def _changed(self, event: Event) -> None:
        new = event.data["new_state"]
        self.changes.setdefault(new.entity_id, []).append(new.last_changed.timestamp())
        if why := self.why(new.context):
            self._last[new.entity_id] = why
        self._store.async_delay_save(self._data, 30)

    def _prune(self) -> None:
        cutoff = dt_util.utcnow().timestamp() - KEEP_DAYS * 86400
        self.changes = {k: kept for k, v in self.changes.items() if (kept := [t for t in v if t >= cutoff])}

    def _data(self) -> dict[str, Any]:
        self._prune()
        return {"changes": self.changes, "last": self._last}

    def times(self) -> dict[str, set[int]]:
        """Entity -> epoch seconds Sentient changed it (what learning should ignore)."""
        return {k: {int(t) for t in v} for k, v in self.changes.items()}

    def last_reason(self, entity_id: str) -> str | None:
        return self._last.get(entity_id)


def own_actions(hass: HomeAssistant) -> OwnActions | None:
    """The registry, kept on Sentient's config entry (None while it isn't running)."""
    for entry in hass.config_entries.async_entries(DOMAIN):
        if (own := getattr(getattr(entry, "runtime_data", None), "own", None)) is not None:
            return own
    return None


class LastingContext:
    """A long-lived 'the house did this' context for one feature, made the first time it's needed
    (Sentient may still be starting when the feature itself is created)."""

    def __init__(self, hass: HomeAssistant, why: str) -> None:
        self.hass = hass
        self.why = why
        self._context: Context | None = None

    def get(self) -> Context:
        if self._context is None:
            if own_actions(self.hass) is None:
                return Context()  # not running yet: don't keep a context that isn't registered
            self._context = own_context(self.hass, self.why, lasting=True)
        return self._context


def own_context(hass: HomeAssistant, why: str = "", lasting: bool = False) -> Context:
    """A context marking this as the house's own doing (a plain one if Sentient isn't running)."""
    own = own_actions(hass)
    return own.context(why, lasting) if own is not None else Context()


def is_own(hass: HomeAssistant, ctx: Context | None) -> bool:
    own = own_actions(hass)
    return bool(own and own.is_own(ctx))
