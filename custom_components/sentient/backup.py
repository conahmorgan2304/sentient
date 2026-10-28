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


# ----- automatic weekly backups -------------------------------------------------------------
import json  # noqa: E402
import logging  # noqa: E402
from pathlib import Path  # noqa: E402

from homeassistant.core import callback  # noqa: E402
from homeassistant.helpers.event import async_track_time_change  # noqa: E402

_LOGGER = logging.getLogger(__name__)
FOLDER = "sentient_backups"  # inside the config folder, so Home Assistant's own backups include it too
WEEKLY_AT = (6, 3, 40)  # Sunday, 03:40


class AutoBackup:
    """Once a week, the same backup as the download button, saved in the config folder; the newest few kept."""

    def __init__(self, hass: HomeAssistant, entry, options: dict[str, Any]) -> None:
        self.hass = hass
        self.entry = entry
        self.options = options
        self._unsub = None

    async def async_start(self) -> None:
        self._unsub = async_track_time_change(self.hass, self._tick, hour=WEEKLY_AT[1], minute=WEEKLY_AT[2], second=0)

    @callback
    def async_stop(self) -> None:
        if self._unsub:
            self._unsub()
            self._unsub = None

    @property
    def folder(self) -> Path:
        return Path(self.hass.config.path(FOLDER))

    @callback
    def _tick(self, now) -> None:
        from .const import CONF_AUTO_BACKUP

        if self.options.get(CONF_AUTO_BACKUP) and dt_util.as_local(now).weekday() == WEEKLY_AT[0]:
            self.hass.async_create_task(self.async_save(), eager_start=False)

    async def async_save(self) -> str:
        from .const import CONF_BACKUP_KEEP, DEFAULT_BACKUP_KEEP

        data = export_all(self.hass, self.entry)
        keep = max(1, int(float(self.options.get(CONF_BACKUP_KEEP, DEFAULT_BACKUP_KEEP) or DEFAULT_BACKUP_KEEP)))
        name = f"sentient-{dt_util.now():%Y-%m-%d-%H%M}.json"

        def write() -> None:
            self.folder.mkdir(exist_ok=True)
            (self.folder / name).write_text(json.dumps(data, indent=1, default=str), encoding="utf-8")
            for old in sorted(self.folder.glob("sentient-*.json"))[:-keep]:
                old.unlink(missing_ok=True)

        await self.hass.async_add_executor_job(write)
        self.entry.runtime_data.timeline.add("mode", "Sentient backed itself up", f"{name}, keeping the newest {keep}")
        return name

    async def async_list(self) -> list[dict[str, Any]]:
        def read() -> list[dict[str, Any]]:
            if not self.folder.is_dir():
                return []
            return [{"name": p.name, "size": p.stat().st_size} for p in sorted(self.folder.glob("sentient-*.json"), reverse=True)]

        return await self.hass.async_add_executor_job(read)

    async def async_restore(self, name: str, settings: bool = False) -> dict[str, int]:
        path = self.folder / Path(name).name  # never outside the folder
        if not path.name.startswith("sentient-") or path.suffix != ".json":
            raise ValueError("not a Sentient backup")
        data = json.loads(await self.hass.async_add_executor_job(path.read_text, "utf-8"))
        return import_all(self.hass, self.entry, data, settings)
