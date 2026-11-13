"""Replay: a real day from this house, so a new version can be checked against what the last one did.

Two parts live here, used by the running integration:

* ``decision_record`` — one shape for every decision on the timeline (said, held back, only noted, sent to a
  phone), built from what the timeline already stores: kind, source, reason, why, evidence, surprise and
  whether AI was used. Nothing new is recorded; it's a common reading of what's there.
* ``async_export_day`` — a recorded day for the replay benchmark (``scripts/replay.py``): every change the house
  ledger recorded in the period (with its cause), the state of those devices with their attributes, their rooms,
  Sentient's settings and the decisions it made. It's a download for an administrator (Settings → Setup doctor);
  nothing is sent anywhere. Secrets (tokens, addresses) are left out.

The replay itself runs outside Home Assistant: the recorded changes go through the real decision code of two
versions on a simulated clock, with nothing spoken and no device touched, and their decisions are compared.
"""
from __future__ import annotations

from datetime import timedelta
import re
from typing import Any

from homeassistant.core import HomeAssistant
from homeassistant.helpers import area_registry as ar, entity_registry as er
from homeassistant.util import dt as dt_util

from .const import DOMAIN

FORMAT = 1
MAX_HOURS = 48
MAX_CHANGES = 50000
_ENTITY = re.compile(r"^[a-z_]+\.[a-z0-9_]+$")
SECRET = re.compile(r"token|password|secret|api_key|_url$|^url$", re.I)

# what happened to a decision, from the timeline item's kind
_ACTION = {"said": "spoke", "held": "held", "critical": "spoke"}


def decision_record(item: dict[str, Any]) -> dict[str, Any]:
    """One timeline item as a decision: what was decided, by what, why, and on what evidence."""
    decision = item.get("decision") if isinstance(item.get("decision"), dict) else {}
    action = _ACTION.get(item.get("kind"), "noted")
    return {
        "time": item.get("time"),
        "kind": item.get("kind"),
        "action": action,
        "phoned": bool(item.get("phoned")),
        "source": item.get("source") or decision.get("kind") or item.get("kind"),
        "reason": item.get("reason") or (item.get("detail") if action == "held" else None),
        "why": item.get("why"),
        "priority": item.get("priority"),
        "text": item.get("text"),
        "evidence": list(decision.get("evidence") or [])[:6],
        "surprise": decision.get("surprise"),
        "ai_used": decision.get("ai_used"), "quietened": item.get("heads_up"),
    }


def _entities_in(value: Any, found: set[str]) -> None:
    if isinstance(value, str):
        for part in re.split(r"[\s,]+", value):
            if _ENTITY.match(part):
                found.add(part)
    elif isinstance(value, dict):
        for v in value.values():
            _entities_in(v, found)
    elif isinstance(value, (list, tuple)):
        for v in value:
            _entities_in(v, found)


async def async_export_day(hass: HomeAssistant, rt: Any, hours: float = 24) -> dict[str, Any]:
    """A recorded period of this house for the replay benchmark."""
    hours = max(1.0, min(float(hours), MAX_HOURS))
    end = dt_util.utcnow()
    start = end - timedelta(hours=hours)
    ledger = getattr(getattr(rt, "brain", None), "ledger", None)
    if ledger is None or not getattr(ledger, "enabled", False):
        return {"error": "The house ledger is off (Settings → Memory & learning), so there's no record of the day."}
    changes = await ledger.async_changes(since=start.timestamp(), until=end.timestamp(), limit=MAX_CHANGES,
                                         newest_first=False)
    options = {k: v for k, v in rt.options.items() if not SECRET.search(k)}
    wanted: set[str] = {c["entity"] for c in changes}
    _entities_in(options, wanted)
    ereg, areg = er.async_get(hass), ar.async_get(hass)
    first: dict[str, str] = {}
    for c in changes:
        first.setdefault(c["entity"], c.get("old"))
    states, entities = {}, {}
    for entity_id in sorted(wanted):
        entry = ereg.async_get(entity_id)
        if entry is not None and entry.platform == DOMAIN:
            continue  # Sentient's own entities are made by the replayed Sentient
        state = hass.states.get(entity_id)
        if state is None:
            continue
        attrs = {k: v for k, v in state.attributes.items() if k not in ("entity_picture", "access_token")}
        states[entity_id] = {"state": first.get(entity_id, state.state), "attributes": attrs}
        area = entry.area_id if entry else None
        if area is None and entry and entry.device_id:
            from homeassistant.helpers import device_registry as dr

            device = dr.async_get(hass).async_get(entry.device_id)
            area = device.area_id if device else None
        entities[entity_id] = {"area": area}
    from homeassistant.helpers import floor_registry as fr

    areas = {a.id: {"name": a.name, "floor": a.floor_id} for a in areg.async_list_areas()}
    floors = {f.floor_id: {"name": f.name, "level": f.level} for f in fr.async_get(hass).async_list_floors()}
    timeline = [decision_record(i) for i in rt.timeline.items
                if (when := dt_util.parse_datetime(i.get("time") or "")) and start <= when <= end]
    return {
        "format": FORMAT, "from": start.isoformat(), "to": end.isoformat(), "version": _version(hass),
        "options": options, "floors": floors, "areas": areas, "entities": entities, "states": states,
        "changes": [{"ts": c["ts"], "entity": c["entity"], "old": c.get("old"), "new": c.get("new"),
                     "cause": c.get("cause"), "who": c.get("who")} for c in changes if c["entity"] in states],
        "decisions": list(reversed(timeline)),
    }


def _version(hass: HomeAssistant) -> str | None:
    from homeassistant.loader import async_get_loaded_integration

    try:
        return str(async_get_loaded_integration(hass, DOMAIN).version)
    except Exception:  # noqa: BLE001 - the version is a label only
        return None
