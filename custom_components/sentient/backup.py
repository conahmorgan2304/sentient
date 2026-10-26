"""Backup: everything Sentient has learned and been told, as one JSON file.

Export covers memories, camera spots, motion lights, named routines, shushes, which
learned routines were adopted or declined, and (optionally on import) the settings.
Import adds to what's there — nothing is deleted — so it's safe to run twice.
"""

from __future__ import annotations

from dataclasses import asdict
from typing import Any

from homeassistant.core import HomeAssistant
from homeassistant.util import dt as dt_util

from .memory import Memory

FORMAT = 1


def export_all(hass: HomeAssistant, entry) -> dict[str, Any]:
    rt = entry.runtime_data
    ml = rt.motion_lights
    return {
        "sentient_backup": FORMAT,
        "created": dt_util.now().isoformat(),
        "persona": rt.options.get("persona"),
        "memories": [m.as_dict() for m in rt.memory.items],
        "spots": list(rt.spots.spots.values()),
        "motion_lights": {
            "rules": [{k: v for k, v in asdict(r).items() if k not in ("last", "last_time")} for r in ml.rules.values()],
            "night_start": ml.night_start,
            "night_end": ml.night_end,
        },
        "routines": [{k: v for k, v in s.items() if k != "last_run"} for s in rt.shortcuts.listing()],
        "shushed": [m for m in rt.mutes.items],
        "deleted_routines": dict(rt.learner.dismissed),
        "lists": {kind: items.listing() for kind, items in rt.lists.items()},
        "learned_choices": [
            {"id": r.id, "adopted": r.adopted, "declined": r.declined}
            for r in rt.learner.all() if r.adopted or r.declined
        ],
        "settings": {k: v for k, v in {**entry.data, **entry.options}.items()},
    }


def import_all(hass: HomeAssistant, entry, data: dict[str, Any], settings: bool = False) -> dict[str, int]:
    if data.get("sentient_backup") != FORMAT:
        raise ValueError("unknown format")
    rt = entry.runtime_data
    counts = {"memories": 0, "spots": 0, "motion_lights": 0, "routines": 0, "shushed": 0, "learned_choices": 0}

    known = {m.text.strip().lower() for m in rt.memory.items}
    added = False
    for raw in data.get("memories", []):
        text = str(raw.get("text", "")).strip()
        if text and text.lower() not in known:
            rt.memory._items.append(Memory(**{k: v for k, v in raw.items() if k in Memory.__dataclass_fields__}))
            known.add(text.lower())
            counts["memories"] += 1
            added = True
    if added:
        rt.memory._save()

    for spot in data.get("spots", []):
        if spot.get("camera") and spot.get("name") and spot.get("box"):
            try:
                rt.spots.set(spot["camera"], spot["name"], spot["box"])
                counts["spots"] += 1
            except Exception:  # noqa: BLE001 - skip a bad one
                pass

    ml = data.get("motion_lights") or {}
    have = {r.light for r in rt.motion_lights.rules.values()}
    for rule in ml.get("rules", []):
        if rule.get("light") and rule["light"] not in have:
            try:
                rt.motion_lights.upsert({k: v for k, v in rule.items() if k != "id"})
                counts["motion_lights"] += 1
            except ValueError:
                pass
    if ml.get("night_start") and ml.get("night_end"):
        rt.motion_lights.set_night(ml["night_start"], ml["night_end"])

    for item in data.get("routines", []):
        existing = rt.shortcuts.find(item.get("name", ""))
        payload = {k: v for k, v in item.items() if k != "id"}
        if existing:
            payload["id"] = existing.id
        try:
            rt.shortcuts.upsert(payload)
            counts["routines"] += 1
        except ValueError:
            pass

    have_mutes = {m["what"] for m in rt.mutes.items}
    for mute in data.get("shushed", []):
        if mute.get("what") and mute["what"] not in have_mutes and (not mute.get("until") or mute["until"] > dt_util.utcnow().isoformat()):
            rt.mutes.items.append(mute)
            counts["shushed"] += 1
    if counts["shushed"]:
        rt.mutes._save()

    for choice in data.get("learned_choices", []):
        routine = rt.learner.get(choice.get("id"))
        if routine is not None:
            routine.adopted = bool(choice.get("adopted"))
            routine.declined = bool(choice.get("declined"))
            counts["learned_choices"] += 1
    for kind, rows in (data.get("lists") or {}).items():
        target = rt.lists.get(kind)
        if target is None or not isinstance(rows, list):
            continue
        for row in rows:
            if isinstance(row, dict) and row.get("name") and target.find(row["name"]) is None:
                target.upsert({k: v for k, v in row.items() if k != "id"})
                counts[kind] = counts.get(kind, 0) + 1
    deleted = data.get("deleted_routines") or {}
    if isinstance(deleted, dict) and deleted:
        for k, v in deleted.items():
            rt.learner.dismissed.setdefault(str(k), str(v))
            rt.learner.routines.pop(str(k), None)
            rt.learner.links.pop(str(k), None)
        counts["learned_choices"] += len(deleted)
    if counts["learned_choices"]:
        rt.learner._save()

    if settings and isinstance(data.get("settings"), dict):
        hass.config_entries.async_update_entry(entry, options={**entry.options, **data["settings"]})
        counts["settings"] = 1
    return counts
