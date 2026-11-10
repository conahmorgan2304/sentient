"""Story games: a game master the whole house shares, with a memory of its own.

A game is a name, a setting and rules for the game master, plus its own saved state: where the players are,
what they carry, what they have found and chosen. The state is exact (a key and a value, not a sentence the
model might half-remember), is never pruned or rewritten by the house's own tidying, and is only ever shown to
a voice while a game is being played. The house's ordinary memory stays readable to the game master
(sentient_recall) but is never changed by it. Nothing here is generic chat: the tools, the prompt lines and
the page all disappear when the feature is switched off in Settings; saved games are kept, not cleared.
"""
from __future__ import annotations

from datetime import timedelta
import re
import uuid
from typing import Any

from homeassistant.core import HomeAssistant, callback
from homeassistant.helpers import area_registry as ar
from homeassistant.helpers.storage import Store
from homeassistant.util import dt as dt_util

from . import game_templates
from .const import CONF_GAMES, CONF_PERSONA, DOMAIN

STORAGE_VERSION = 1
KINDS = ("place", "item", "flag", "person", "clue", "note")
KIND_WORDS = {"place": "Where", "item": "Carrying or found", "flag": "State of things", "person": "People met",
              "clue": "Clues", "note": "Notes"}
MAX_ENTRIES = 300  # per game: a full game says so instead of dropping something
MAX_LOG = 200  # the story so far; the oldest line goes first
MAX_TEXT = 300
IDLE_HOURS = 6  # a game nobody has touched for this long is put away (its save is kept)
PROMPT_LOG = 8


def _clean(text: Any, limit: int = MAX_TEXT) -> str:
    return " ".join(str(text or "").split())[:limit].strip()


def _slug(text: str) -> str:
    return re.sub(r"[^a-z0-9]+", "-", text.lower()).strip("-")[:40] or uuid.uuid4().hex[:8]


class Games:
    def __init__(self, hass: HomeAssistant, entry_id: str, options: dict[str, Any], rt_getter=None) -> None:
        self.hass = hass
        self.options = options
        self._rt = rt_getter
        self._store: Store[dict[str, Any]] = Store(hass, STORAGE_VERSION, f"{DOMAIN}.{entry_id}.games")
        self.games: dict[str, dict[str, Any]] = {}
        self._active: dict[str, Any] | None = None  # {"game": id, "since": iso, "last": iso}

    @property
    def enabled(self) -> bool:
        return bool(self.options.get(CONF_GAMES))

    async def async_start(self) -> None:
        data = await self._store.async_load() or {}
        self.games = {gid: g for gid, g in (data.get("games") or {}).items() if isinstance(g, dict)}
        self._active = data.get("active") if isinstance(data.get("active"), dict) else None

    @callback
    def async_stop(self) -> None:
        self._store.async_delay_save(self._data, 0)

    def _data(self) -> dict[str, Any]:
        return {"games": self.games, "active": self._active}

    @callback
    def _save(self) -> None:
        self._store.async_delay_save(self._data, 2)

    # ----- games ----------------------------------------------------------------------------------
    def find(self, name: str | None) -> dict[str, Any] | None:
        """A game by id or by what it is called: exact first, then a name that contains the words."""
        wanted = _clean(name).lower()
        if not wanted:
            return None
        for g in self.games.values():
            if wanted in (g["id"], g["name"].lower()):
                return g
        words = set(re.findall(r"[a-z0-9]+", wanted))
        scored = [(len(words & set(re.findall(r"[a-z0-9]+", g["name"].lower()))), g) for g in self.games.values()]
        scored = [s for s in scored if s[0]]
        return max(scored, key=lambda s: s[0])[1] if scored else None

    def save_game(self, name: str, setting: str = "", rules: str = "", game_id: str | None = None) -> dict[str, Any]:
        name = _clean(name, 60)
        if not name:
            raise ValueError("Give the game a name")
        if game_id and game_id in self.games:
            game = self.games[game_id]
            game.update(name=name, setting=_clean(setting, 1200), rules=_clean(rules, 1200))
        else:
            base = gid = _slug(name)
            n = 2
            while gid in self.games:
                gid, n = f"{base}-{n}", n + 1
            game = self.games[gid] = {"id": gid, "name": name, "setting": _clean(setting, 1200), "rules": _clean(rules, 1200),
                                      "entries": [], "log": [], "created": dt_util.now().isoformat(), "plays": 0}
        self._save()
        return game

    # ----- templates ------------------------------------------------------------------------------
    def template_defaults(self) -> dict[str, str]:
        """What a template's blanks start as: this house's own persona name and Home Assistant areas."""
        areas = sorted((a.name for a in ar.async_get(self.hass).areas.values()), key=str.lower)[: game_templates.MAX_ROOMS]
        start = next((a for a in areas if "living" in a.lower()), areas[0] if areas else "")
        return {"master": _clean(self.options.get(CONF_PERSONA), 40), "rooms": ", ".join(areas), "start_room": start}

    def template_listing(self) -> list[dict[str, Any]]:
        defaults = self.template_defaults()
        return [{**t, "fields": [{**f, "default": defaults.get(f["key"], "")} for f in t["fields"]]} for t in game_templates.templates()]

    def create_from_template(self, template_id: str, values: dict[str, Any] | None = None) -> dict[str, Any]:
        built = game_templates.build(template_id, values or {}, self.template_defaults())
        game = self.save_game(built["name"], built["setting"], built["rules"])
        for kind, key, value in built["entries"]:
            self.set_entry(game["id"], kind, key, value)
        return game

    def delete_game(self, game_id: str) -> bool:
        if game_id not in self.games:
            return False
        del self.games[game_id]
        if self._active and self._active.get("game") == game_id:
            self._active = None
        self._save()
        return True

    # ----- the state of a game ---------------------------------------------------------------------
    def set_entry(self, game_id: str, kind: str, key: str, value: str = "", entry_id: str | None = None) -> dict[str, Any]:
        """Add or change one fact, found by its kind and name (exact, so 'brass key' is always the same fact)."""
        game = self.games.get(game_id)
        if game is None:
            raise ValueError("No such game")
        kind, key, value = str(kind or "note").lower(), _clean(key, 80), _clean(value)
        if kind not in KINDS:
            kind = "note"
        if not key:
            raise ValueError("Give it a name")
        entries = game["entries"]
        existing = next((e for e in entries if (entry_id and e["id"] == entry_id) or (not entry_id and e["kind"] == kind and e["key"].lower() == key.lower())), None)
        if existing is None:
            if len(entries) >= MAX_ENTRIES:
                raise ValueError(f"This game's memory is full ({MAX_ENTRIES}); remove something before adding more")
            existing = {"id": uuid.uuid4().hex[:8], "created": dt_util.now().isoformat()}
            entries.append(existing)
        existing.update(kind=kind, key=key, value=value, updated=dt_util.now().isoformat())
        self._touch(game_id)
        self._save()
        return existing

    def remove_entry(self, game_id: str, key_or_id: str, kind: str | None = None) -> bool:
        game = self.games.get(game_id)
        if game is None:
            return False
        wanted = _clean(key_or_id, 80).lower()
        keep = [e for e in game["entries"] if not (e["id"] == key_or_id or (e["key"].lower() == wanted and (not kind or e["kind"] == kind)))]
        gone = len(keep) != len(game["entries"])
        game["entries"] = keep
        if gone:
            self._touch(game_id)
            self._save()
        return gone

    def log(self, game_id: str, text: str) -> None:
        game = self.games.get(game_id)
        text = _clean(text)
        if game is None or not text:
            raise ValueError("Nothing to log")
        game["log"].append({"at": dt_util.now().isoformat(), "text": text})
        del game["log"][:-MAX_LOG]
        self._touch(game_id)
        self._save()

    def restart(self, game_id: str) -> bool:
        """Start the story again: the state and the log go, the setting and rules stay."""
        game = self.games.get(game_id)
        if game is None:
            return False
        game["entries"], game["log"] = [], []
        self._save()
        return True

    # ----- playing ---------------------------------------------------------------------------------
    def _touch(self, game_id: str) -> None:
        if self._active and self._active.get("game") == game_id:
            self._active["last"] = dt_util.utcnow().isoformat()

    @property
    def active(self) -> dict[str, Any] | None:
        """The game being played right now, or None (also when it has sat untouched for hours)."""
        if not self.enabled or not self._active:
            return None
        game = self.games.get(self._active.get("game", ""))
        last = dt_util.parse_datetime(self._active.get("last") or "")
        if game is None or last is None or dt_util.utcnow() - last > timedelta(hours=IDLE_HOURS):
            self._active = None
            self._save()
            return None
        return game

    def start(self, name: str) -> dict[str, Any] | None:
        game = self.find(name)
        if game is None or not self.enabled:
            return None
        now = dt_util.utcnow().isoformat()
        game["plays"] = int(game.get("plays") or 0) + (0 if self._active and self._active.get("game") == game["id"] else 1)
        self._active = {"game": game["id"], "since": now, "last": now}
        self._save()
        return game

    def stop(self) -> dict[str, Any] | None:
        game = self.active
        self._active = None
        self._save()
        return game

    # ----- what a voice is shown -------------------------------------------------------------------
    def state(self, game: dict[str, Any]) -> dict[str, list[str]]:
        out: dict[str, list[str]] = {}
        for kind in KINDS:
            rows = [f"{e['key']}: {e['value']}" if e.get("value") else e["key"] for e in game["entries"] if e["kind"] == kind]
            if rows:
                out[kind] = rows
        return out

    def prompt_lines(self) -> list[str]:
        """What the game master is told while a game is on; nothing at all otherwise."""
        if not self.enabled:
            return []
        game = self.active
        if game is None:
            return ["Story games are played out loud here. For \"let's play\", \"start a game\" or \"what games are there\" "
                    "call sentient_game; never make up a game's progress yourself."]
        lines = [f"A story game is being played: \"{game['name']}\". You are its game master until it is stopped "
                 "(\"stop the game\" -> sentient_game stop).",
                 *([f"Setting: {game['setting']}"] if game.get("setting") else []),
                 *([f"Rules for the game master: {game['rules']}"] if game.get("rules") else []),
                 "The game's own saved memory is below and is the truth: describe the story from it and never contradict it. "
                 "Whenever the players move, pick something up, use it, meet someone, find a clue or choose, record it at once "
                 "with sentient_game_set (kind: place, item, flag, person, clue or note; give remove=true when an item is used up "
                 "or a fact stops being true) and sentient_game_log (one short line for each choice). Keep spoken replies short. "
                 "The house's ordinary memory can be read with sentient_recall (to bring in the family's names or likes) but "
                 "never put game facts in it."]
        state = self.state(game)
        if state:
            lines.append("Game memory:\n" + "\n".join(f"- {KIND_WORDS[k]}: " + "; ".join(v) for k, v in state.items()))
        else:
            lines.append("Game memory: empty, so this is a fresh start: set the scene, then record where the players are.")
        if game["log"]:
            lines.append("The story so far (latest last): " + " | ".join(l["text"] for l in game["log"][-PROMPT_LOG:]))
        return lines

    def listing(self) -> list[dict[str, Any]]:
        active = self.active
        return [{"id": g["id"], "name": g["name"], "setting": g.get("setting", ""), "rules": g.get("rules", ""),
                 "entries": g["entries"], "log": g["log"], "plays": g.get("plays", 0),
                 "active": bool(active and active["id"] == g["id"])} for g in self.games.values()]
