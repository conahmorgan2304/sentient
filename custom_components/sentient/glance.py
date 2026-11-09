"""Rooms at a glance (glance.py): what each area of the house looks like right now.

Built on demand from Home Assistant's areas — temperature and humidity, lights on, doors and windows
open, when someone last moved there and what's playing. Nothing is stored and no AI is used.
"""

from __future__ import annotations

from statistics import mean
from typing import Any

from homeassistant.core import HomeAssistant, State
from homeassistant.helpers import area_registry as ar, device_registry as dr, entity_registry as er, floor_registry as fr
from homeassistant.util import dt as dt_util

OPENINGS = ("door", "window", "opening", "garage_door")
PRESENCE = ("motion", "occupancy", "presence")
DEAD = ("unavailable", "unknown")
# integrations whose "lights" are a tablet's screen, not a lamp someone left on
SCREEN_PLATFORMS = ("browser_mod", "fully_kiosk")


def _num(st: State) -> float | None:
    try:
        return float(st.state)
    except (TypeError, ValueError):
        return None


def rooms(hass: HomeAssistant, likely: str | None = None, restless: set[str] | None = None) -> list[dict[str, Any]]:
    """One record per area that has anything worth showing."""
    areas, devices, ents, floors = ar.async_get(hass), dr.async_get(hass), er.async_get(hass), fr.async_get(hass)
    restless = restless or set()
    by_area: dict[str, list[State]] = {}
    for entry in ents.entities.values():
        if entry.disabled_by or entry.hidden_by or entry.entity_category is not None:
            continue
        if entry.domain == "light" and entry.platform in SCREEN_PLATFORMS:
            continue
        area_id = entry.area_id or (dev.area_id if entry.device_id and (dev := devices.async_get(entry.device_id)) else None)
        if not area_id or (st := hass.states.get(entry.entity_id)) is None:
            continue
        by_area.setdefault(area_id, []).append(st)

    now = dt_util.utcnow()
    floor_order = {f.floor_id: (f.level if f.level is not None else 0) for f in floors.async_list_floors()}
    out = []
    for area in areas.async_list_areas():
        states = by_area.get(area.id) or []
        temps, hums, lights_on, lights, opened, playing, heating = [], [], [], 0, [], [], None
        moving, last_move = False, None
        for st in states:
            domain, dc = st.domain, st.attributes.get("device_class")
            if st.state in DEAD:
                continue
            if domain == "sensor" and dc == "temperature" and (v := _num(st)) is not None:
                temps.append(v)
            elif domain == "sensor" and dc == "humidity" and (v := _num(st)) is not None:
                hums.append(v)
            elif domain == "light":
                lights += 1
                if st.state == "on":
                    lights_on.append(st.name)
            elif domain == "binary_sensor" and dc in OPENINGS and st.state == "on":
                opened.append(st.name)
            elif domain == "binary_sensor" and dc in PRESENCE and st.entity_id not in restless:
                if st.state == "on":
                    moving = True
                elif last_move is None or st.last_changed > last_move:
                    last_move = st.last_changed
            elif domain == "media_player" and st.state == "playing":
                title = st.attributes.get("media_title")
                playing.append(f"{title} ({st.name})" if title else st.name)
            elif domain == "climate":
                if (v := st.attributes.get("current_temperature")) is not None and not temps:
                    try:
                        temps.append(float(v))
                    except (TypeError, ValueError):
                        pass
                if st.attributes.get("hvac_action") == "heating":
                    heating = st.name
        if not (temps or hums or lights or opened or moving or last_move or playing):
            continue
        out.append({
            "area_id": area.id,
            "name": area.name,
            "icon": area.icon,
            "floor": area.floor_id,
            "level": floor_order.get(area.floor_id, 0) if area.floor_id else None,
            "temp": round(mean(temps), 1) if temps else None,
            "humidity": round(mean(hums)) if hums else None,
            "lights": lights,
            "lights_on": lights_on,
            "open": opened,
            "moving": moving,
            "moved_mins": None if moving or last_move is None else int((now - last_move).total_seconds() // 60),
            "playing": playing,
            "heating": heating,
            "likely": bool(likely) and likely.lower() in (area.name.lower(), area.id.lower()),
        })
    # ground floor first, then up; within a floor the order the areas are in; rooms with no floor last
    out.sort(key=lambda r: (r["level"] is None, r["level"] or 0))
    return out


def summary(rows: list[dict[str, Any]]) -> dict[str, Any]:
    """The overview's AROUND THE HOUSE card in a few numbers."""
    temps = [(r["temp"], r["name"]) for r in rows if r["temp"] is not None]
    return {
        "lights_on": [{"room": r["name"], "area_id": r["area_id"], "count": len(r["lights_on"])} for r in rows if r["lights_on"]],
        "open": [{"room": r["name"], "what": o} for r in rows for o in r["open"]],
        "moving": [r["name"] for r in rows if r["moving"]],
        "playing": [p for r in rows for p in r["playing"]],
        "warmest": {"room": max(temps)[1], "temp": max(temps)[0]} if len(temps) > 1 else None,
        "coolest": {"room": min(temps)[1], "temp": min(temps)[0]} if len(temps) > 1 else None,
    }


def knows(rt: Any) -> list[dict[str, Any]]:
    """What it has learned about the house so far, in plain sentences — only what is already known."""
    out: list[dict[str, Any]] = []

    def add(icon: str, text: str, why: str = "", warn: bool = False) -> None:
        out.append({"icon": icon, "text": text, "why": why, "warn": warn})

    if (sleep := getattr(rt, "sleep", None)) is not None and sleep.enabled:
        usual = sleep.usual()
        if usual.get("bedtime") or usual.get("up"):
            bits = [f"bed around {usual['bedtime']}" if usual.get("bedtime") else "", f"up around {usual['up']}" if usual.get("up") else ""]
            add("mdi:sleep", "The house usually goes to " + " and is ".join(b for b in bits if b) + ".", "from the last three weeks of nights")
        else:
            add("mdi:sleep", "Still learning when the house goes to bed and gets up.", "it needs three nights it can tell apart")
        for r in sleep.never_rests():
            add("mdi:motion-sensor-off", f"I'm setting aside the {r['name']} at night: it's on {round(r['on_share'] * 100)}% of the time.",
                "a sensor that never rests can't say whether anyone's up — it may be too sensitive or pointing at something that moves", True)
    heating = getattr(rt, "heating", None)
    for line in (heating.describe() if heating is not None else []):
        add("mdi:radiator", line, "learned from the heating's history each night")
    if (inner := getattr(rt, "inner", None)) is not None:
        p = (inner.summary() or {}).get("prediction") or {}
        if p.get("tried"):
            add("mdi:crystal-ball", f"I guess which room someone heads to next — right {round((p.get('rate') or 0) * 100)}% of the time ({p.get('right')} of {p.get('tried')}).",
                "used once it's reliably right" if not p.get("trusted") else "reliable enough to use")
    if (learner := getattr(rt, "learner", None)) is not None:
        rs = list(learner.all())
        if rs:
            on = sum(1 for r in rs if r.adopted)
            add("mdi:repeat", f"{len(rs)} routine{'s' if len(rs) != 1 else ''} spotted, {on} the house does by itself.", "checked each night against the last few weeks")
        else:
            add("mdi:repeat", "No routines spotted yet.", "it needs the same thing at about the same time on most days")
    if (memory := getattr(rt, "memory", None)) is not None:
        cats: dict[str, int] = {}
        for m in memory.items:
            cats[m.category] = cats.get(m.category, 0) + 1
        if cats:
            names = {"fact": "facts", "preference": "preferences", "person": "about people", "routine": "routines", "note": "notes", "correction": "corrections"}
            add("mdi:brain", f"{sum(cats.values())} memories: " + ", ".join(f"{n} {names.get(c, c)}" for c, n in sorted(cats.items(), key=lambda x: -x[1])) + ".",
                "corrections are how it's been told to do things differently")
    for a in (rt.appliances.listing() if getattr(rt, "appliances", None) is not None else []):
        if a.get("usual_minutes"):
            add("mdi:washing-machine", f"The {a['name']} usually takes about {a['usual_minutes']} minutes.", f"from {a['runs_seen']} runs")
        elif a.get("runs_seen"):
            add("mdi:washing-machine", f"Learning how long the {a['name']} takes ({a['runs_seen']} run{'s' if a['runs_seen'] != 1 else ''} so far).", "it needs a few runs")
    return out
