"""Backup: everything Sentient has learned and been told, as one JSON file.

Export covers memories, camera spots, motion lights, named routines, shushes, which
learned routines were adopted or declined, and (optionally on import) the settings.
Import adds to what's there — nothing is deleted — so it's safe to run twice.
"""

from __future__ import annotations

from dataclasses import asdict
from copy import deepcopy
import asyncio
import uuid
from typing import Any

from homeassistant.core import HomeAssistant
from homeassistant.util import dt as dt_util

from .memory import Memory, _identity
from .const import MEMORY_MAX_ITEMS, MEMORY_CATEGORIES
from .shortcuts import _norm

FORMAT = 1


def export_all(hass: HomeAssistant, entry) -> dict[str, Any]:
    rt = entry.runtime_data
    ml = rt.motion_lights
    return deepcopy({
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
        "heating_programmes": rt.heat_prog.programmes(),
        "learned_choices": [
            {"id": r.id, "adopted": r.adopted, "declined": r.declined, "plan": _plan(rt.learner, r)}
            for r in rt.learner.all() if r.adopted or r.declined
        ],
        "settings": {k: v for k, v in {**entry.data, **entry.options}.items()},
    })


def _plan(learner, routine):
    return {key: value for key, value in learner._version(routine).items() if key not in ('adopted', 'declined')}


def _preflight(data, settings):
    if not isinstance(data, dict) or type(data.get('sentient_backup')) is not int or data['sentient_backup'] != FORMAT:
        raise ValueError('unknown format')
    for key in ('memories', 'spots', 'routines', 'shushed', 'learned_choices', 'heating_programmes'):
        if key in data and (not isinstance(data[key], list) or any(not isinstance(row, dict) for row in data[key])):
            raise ValueError(f'{key} must be a list of objects')
    for key in ('motion_lights', 'lists', 'deleted_routines', 'settings'):
        if key in data and not isinstance(data[key], dict):
            raise ValueError(f'{key} must be an object')
    rules = data.get('motion_lights', {}).get('rules', [])
    if not isinstance(rules, list) or any(not isinstance(row, dict) for row in rules):
        raise ValueError('motion-light rules must be objects')
    for rows in data.get('lists', {}).values():
        if not isinstance(rows, list) or any(not isinstance(row, dict) for row in rows):
            raise ValueError('named lists must contain objects')
    ml = data.get('motion_lights', {})
    for key in ('night_start', 'night_end'):
        if key in ml:
            from datetime import time
            try:
                time.fromisoformat(ml[key])
            except (ValueError, TypeError):
                raise ValueError('invalid motion-light night hours') from None
    validated = {}
    if settings:
        import voluptuous as vol
        from .config_flow import _speech_schema, _briefing_schema
        from homeassistant.helpers.selector import BooleanSelector, NumberSelector
        from .numeric import finite_float
        schemas = (_speech_schema({}), _briefing_schema({}))
        validators = {str(key): validator for schema in schemas for key, validator in schema.schema.items()}
        optional = {str(key) for schema in schemas for key in schema.schema if isinstance(key, vol.Optional)}
        for key, value in data.get('settings', {}).items():
            if key == 'persona':
                if not isinstance(value, str) or not value.strip():
                    raise ValueError('invalid persona')
                validated[key] = value.strip()
            elif key in validators:
                if value is None and key in optional:
                    validated[key] = None
                    continue
                try:
                    if value is None:
                        raise ValueError('this setting cannot be null')
                    if isinstance(validators[key], BooleanSelector) and type(value) is not bool:
                        raise ValueError('expected a boolean')
                    if isinstance(validators[key], NumberSelector) and (type(value) not in (int, float) or finite_float(value) is None):
                        raise ValueError('expected a finite number')
                    validated[key] = validators[key](value)
                except (vol.Invalid, ValueError, TypeError) as err:
                    raise ValueError(f'invalid setting {key}: {err}') from err
    return validated


def import_all(hass: HomeAssistant, entry, data: dict[str, Any], settings: bool = False) -> dict[str, int]:
    restored_settings = _preflight(data, settings)  # reject malformed sections before any store changes
    data = deepcopy(data)
    rt = entry.runtime_data
    counts = {"memories": 0, "spots": 0, "motion_lights": 0, "routines": 0, "shushed": 0, "learned_choices": 0}
    def skip():
        counts['skipped'] = counts.get('skipped', 0) + 1

    known = {(m.category, _identity(m.text)) for m in rt.memory.items}
    ids = {m.id for m in rt.memory.items}
    for raw in data.get('memories', []):
        text, category = raw.get('text'), raw.get('category', 'fact')
        if (not isinstance(text, str) or not text.strip() or category not in MEMORY_CATEGORIES
                or type(raw.get('pinned', False)) is not bool or type(raw.get('hits', 0)) is not int
                or raw.get('hits', 0) < 0 or not isinstance(raw.get('id', ''), str)
                or (raw.get('source') is not None and not isinstance(raw['source'], str))
                or (raw.get('created') is not None and (not isinstance(raw['created'], str) or dt_util.parse_datetime(raw['created']) is None))
                or (raw.get('used') is not None and (not isinstance(raw['used'], str) or dt_util.parse_datetime(raw['used']) is None))):
            skip()
            continue
        if (category, _identity(text)) in known:
            continue
        if raw.get('id') in ids or len(rt.memory.items) >= MEMORY_MAX_ITEMS:
            skip()
            continue
        payload = {k: v for k, v in raw.items() if k in Memory.__dataclass_fields__ and v is not None}
        payload['text'] = ' '.join(text.split())
        if not payload.get('id'):
            payload.pop('id', None)
        memory = Memory(**payload)
        rt.memory._items.append(memory)
        ids.add(memory.id)
        known.add((category, _identity(text)))
        counts['memories'] += 1
    if counts['memories']:
        rt.memory._save()

    from .spots import _key
    for spot in data.get('spots', []):
        if not isinstance(spot.get('name'), str) or not isinstance(spot.get('camera'), str):
            skip()
            continue
        if _key(spot['name']) in rt.spots.spots:
            continue
        try:
            rt.spots.set(spot['camera'], spot['name'], spot.get('box'), spot.get('points'), spot.get('aliases', ''))
            counts['spots'] += 1
        except Exception:  # one invalid new spot must not overwrite existing geometry
            skip()

    ml = data.get('motion_lights', {})
    have = {r.light for r in rt.motion_lights.rules.values()}
    for rule in ml.get('rules', []):
        if not isinstance(rule.get('light'), str) or not rule['light'] or rule['light'] in have:
            continue
        try:
            rt.motion_lights.upsert({k: v for k, v in rule.items() if k != 'id'})
            have.add(rule['light'])
            counts['motion_lights'] += 1
        except (ValueError, TypeError, KeyError):
            skip()
    # Night hours are global configuration; restore them only with the explicit settings option.
    if settings and ml.get('night_start') and ml.get('night_end'):
        rt.motion_lights.set_night(ml['night_start'], ml['night_end'])

    for item in data.get('routines', []):
        if not isinstance(item.get('name'), str):
            skip()
            continue
        if any(_norm(s.name) == _norm(item['name']) for s in rt.shortcuts.items.values()):
            continue
        if (not isinstance(item.get('actions', []), list) or any(not isinstance(a, dict) for a in item.get('actions', []))
                or not isinstance(item.get('aliases', ''), str) or not isinstance(item.get('say', ''), str)):
            skip()
            continue
        try:
            rt.shortcuts.upsert({k: v for k, v in item.items() if k != 'id'})
            counts['routines'] += 1
        except (ValueError, TypeError, AttributeError):
            skip()

    have_mutes = {m['what'] for m in rt.mutes.items}
    mute_ids = {m.get('id') for m in rt.mutes.items}
    for mute in data.get('shushed', []):
        what, until = mute.get('what'), mute.get('until')
        if (not isinstance(what, str) or mute.get('kind') not in ('entity', 'words') or (until is not None and (not isinstance(until, str) or dt_util.parse_datetime(until) is None))):
            skip()
            continue
        what = what.strip().lower() if mute['kind'] == 'words' else what.strip()
        mute['what'] = what
        if what and what not in have_mutes and (not until or dt_util.as_utc(dt_util.parse_datetime(until)) > dt_util.utcnow()):
            mute['id'] = mute.get('id') if isinstance(mute.get('id'), str) and mute['id'] and mute['id'] not in mute_ids else uuid.uuid4().hex[:6]
            if until:
                mute['until'] = dt_util.as_utc(dt_util.parse_datetime(until)).isoformat()
            mute_ids.add(mute['id'])
            rt.mutes.items.append(mute)
            have_mutes.add(what)
            counts['shushed'] += 1
    if counts['shushed']:
        rt.mutes._save()

    for choice in data.get('learned_choices', []):
        routine = rt.learner.get(choice.get('id'))
        if routine is None:
            continue
        if (type(choice.get('adopted')) is not bool or type(choice.get('declined')) is not bool
                or choice['adopted'] and choice['declined'] or choice.get('plan') != _plan(rt.learner, routine)):
            skip()
            continue
        routine.adopted, routine.declined = choice['adopted'], choice['declined']
        counts['learned_choices'] += 1
    for kind, rows in data.get('lists', {}).items():
        target = rt.lists.get(kind)
        if target is None:
            continue
        names = {row['name'].strip().casefold() for row in target.listing()}
        for row in rows:
            name = row.get('name')
            if not isinstance(name, str) or not name.strip():
                skip()
                continue
            if name.strip().casefold() in names:
                continue
            target.upsert({k: v for k, v in row.items() if k != 'id'})
            names.add(name.strip().casefold())
            counts[kind] = counts.get(kind, 0) + 1
    from .heating_schedule import clean_programme, programme_conflict

    programmes = rt.heat_prog.items
    existing_programmes = programmes.listing()
    programme_ids = {p["id"] for p in existing_programmes}
    options = {**rt.options, **restored_settings}
    for row in data.get("heating_programmes", []):
        if isinstance(row.get("id"), str) and row["id"] in programme_ids:
            continue
        try:
            programme = clean_programme(options, row)
        except ValueError:
            skip()
            continue
        if any(all(existing.get(k) == v for k, v in programme.items()) for existing in existing_programmes):
            continue
        if programme_conflict(programme, existing_programmes):
            skip()
            continue
        existing_programmes.append(programmes.upsert(programme))
        if isinstance(row.get("id"), str):
            programme_ids.add(row["id"])
        counts["heating_programmes"] = counts.get("heating_programmes", 0) + 1

    # Restoring old deletion records must not delete a routine that currently exists.
    for key, value in data.get('deleted_routines', {}).items():
        if isinstance(key, str) and isinstance(value, str) and rt.learner.get(key) is None:
            rt.learner.dismissed.setdefault(key, value)
            counts['learned_choices'] += 1
    if counts['learned_choices']:
        rt.learner._save()
    if settings and restored_settings:
        hass.config_entries.async_update_entry(entry, options={**entry.options, **restored_settings})
        counts['settings'] = 1
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
        self._save_lock = asyncio.Lock()
        self._stopped = False

    async def async_start(self) -> None:
        self._stopped = False
        self._unsub = async_track_time_change(self.hass, self._tick, hour=WEEKLY_AT[1], minute=WEEKLY_AT[2], second=0)

    @callback
    def async_stop(self) -> None:
        self._stopped = True
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
            self.hass.async_create_task(self.async_save(force=False), eager_start=False)

    async def async_save(self, *, force: bool = True) -> str | None:
        async with self._save_lock:
            if self._stopped or (not force and not self.options.get('auto_backup')):
                return None
            return await self._async_save()

    async def _async_save(self) -> str:
        from .const import CONF_BACKUP_KEEP, DEFAULT_BACKUP_KEEP

        options = self.options
        data = export_all(self.hass, self.entry)
        keep = max(1, int(float(self.options.get(CONF_BACKUP_KEEP, DEFAULT_BACKUP_KEEP) or DEFAULT_BACKUP_KEEP)))
        name = f"sentient-{dt_util.now():%Y-%m-%d-%H%M%S%f}-{uuid.uuid4().hex[:8]}.json"

        def write() -> None:
            self.folder.mkdir(exist_ok=True)
            import os
            import tempfile

            payload = json.dumps(data, indent=1, default=str)
            temporary = None
            try:
                with tempfile.NamedTemporaryFile(mode="w", encoding="utf-8", dir=self.folder,
                                                 prefix=".sentient-", suffix=".tmp", delete=False) as stream:
                    temporary = Path(stream.name)
                    stream.write(payload)
                    stream.flush()
                    os.fsync(stream.fileno())
                os.replace(temporary, self.folder / name)
            finally:
                if temporary is not None:
                    temporary.unlink(missing_ok=True)
            previous = sorted((p for p in self.folder.glob('sentient-*.json') if p.name != name),
                              key=lambda p: (p.stat().st_mtime_ns, p.name))
            for old in previous[:max(0, len(previous) - (keep - 1))]:
                old.unlink(missing_ok=True)

        await self.hass.async_add_executor_job(write)
        if not self._stopped and self.options is options:
            self.entry.runtime_data.timeline.add("mode", "Sentient backed itself up", f"{name}, keeping the newest {keep}")
        return name

    async def async_list(self) -> list[dict[str, Any]]:
        def read() -> list[dict[str, Any]]:
            if not self.folder.is_dir():
                return []
            return [{"name": p.name, "size": p.stat().st_size} for p in sorted(self.folder.glob("sentient-*.json"), reverse=True)]

        async with self._save_lock:
            return await self.hass.async_add_executor_job(read)

    async def async_restore(self, name: str, settings: bool = False) -> dict[str, int]:
        before = (deepcopy(dict(self.entry.data)), deepcopy(dict(self.entry.options)))
        path = self.folder / Path(name).name  # never outside the folder
        if not path.name.startswith("sentient-") or path.suffix != ".json":
            raise ValueError("not a Sentient backup")
        def read():
            if path.resolve().parent != self.folder.resolve():
                raise ValueError('backup points outside its folder')
            return path.read_text('utf-8')
        async with self._save_lock:
            data = json.loads(await self.hass.async_add_executor_job(read))
        if self._stopped or (settings and before != (self.entry.data, self.entry.options)):
            raise ValueError('settings changed while reading the backup; please try again')
        return import_all(self.hass, self.entry, data, settings)
