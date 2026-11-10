"""Ready-made story games with blanks to fill in, so any house can play one with its own names and rooms.

A template never holds a name from any particular house. It has fields (the game master, the players, the rooms,
the helper characters...) with defaults taken from the house itself (the persona's name, Home Assistant's areas),
and turns the answers into an ordinary game: a setting, rules for the game master and a starting memory. After
that it is just a game, which can be edited like any other.
"""
from __future__ import annotations

import re
from typing import Any

MAX_ROOMS = 12
_CAPS = {"title": 60, "master": 40, "house_ai": 40, "players": 150, "rooms": 250, "helpers": 200, "start_room": 40}

ESCAPE = "escape-the-house"


def split_names(text: Any, limit: int = MAX_ROOMS) -> list[str]:
    """'Kitchen, Living room and Hall' -> ['Kitchen', 'Living room', 'Hall'] (no repeats, order kept)."""
    out: list[str] = []
    for part in re.split(r"[,;\n]|\band\b|&", str(text or "")):
        name = " ".join(part.split()).strip(" .")
        if name and name.lower() not in (n.lower() for n in out):
            out.append(name[:40])
    return out[:limit]


def _join(names: list[str]) -> str:
    return ", ".join(names[:-1]) + f" and {names[-1]}" if len(names) > 1 else (names[0] if names else "")


def templates() -> list[dict[str, Any]]:
    """What the page and the voice offer: an id, what it is, and the fields to fill in."""
    return [{
        "id": ESCAPE, "name": "Escape the house",
        "about": "The house AI has glitched and locked every door. Each room holds one locked core to restore with a puzzle, "
                 "riddle or challenge; restore them all and the front door opens.",
        "fields": [
            {"key": "title", "label": "Game name", "hint": "e.g. Escape the flat"},
            {"key": "master", "label": "Game master", "hint": "who tells the story; usually your assistant's name"},
            {"key": "house_ai", "label": "The glitching house AI", "hint": "e.g. the house, or a name for it"},
            {"key": "players", "label": "Players", "hint": "names, separated by commas (they can play solo or in teams)"},
            {"key": "rooms", "label": "Rooms", "hint": "one core to restore in each, separated by commas"},
            {"key": "start_room", "label": "Starting room", "hint": "where the players begin"},
            {"key": "helpers", "label": "Helper characters", "hint": "optional: who gives hints, e.g. Mo (blunt hints), Jess (sneaky ones)"},
            {"key": "kids", "label": "Kid-friendly", "hint": "yes or no: puzzles little ones can solve, nothing scary"},
        ],
    }]


def known(template_id: str) -> bool:
    return any(t["id"] == template_id for t in templates())


def _truthy(value: Any, default: bool = True) -> bool:
    text = str(value if value is not None else "").strip().lower()
    return default if not text else text not in ("no", "n", "false", "0", "off")


def build(template_id: str, values: dict[str, Any], defaults: dict[str, str]) -> dict[str, Any]:
    """Fill the blanks (anything left empty falls back to the house's defaults) and return the new game."""
    if not known(template_id):
        raise ValueError("No such template")
    v = {k: " ".join(str(values.get(k) or defaults.get(k) or "").split())[:_CAPS.get(k, 40)].strip() for k in (*_CAPS, "kids")}
    rooms = split_names(values.get("rooms") or defaults.get("rooms"))
    if not rooms:
        raise ValueError("Add at least one room")
    players = split_names(values.get("players") or defaults.get("players"), 8)
    start = v["start_room"] or rooms[0]
    master = v["master"] or "The assistant"
    ai = v["house_ai"] or "The house AI"
    kids = _truthy(values.get("kids"), True)
    count = len(rooms)
    setting = (
        f"{ai} has glitched and sealed the home. Every door is locked and the front door won't open until the players fix the "
        "glitch. Each room holds one locked core, restored with a puzzle, a riddle or a challenge. Restore them all and the front "
        f"door unlocks. The map is the real home: {_join(rooms)}. "
        + (f"Players: {_join(players)}; solo or in teams. " if players else "")
        + (f"Helpers who give hints: {v['helpers']}. " if v["helpers"] else "")
        + "Real features of the house can turn up in the story."
    )
    rules = (
        f"{master} is the game master. Keep turns short and spoken-friendly: describe the scene in 2-3 sentences, then give 2-3 "
        "clear choices. One room at a time; each room has one core, one puzzle and at least one item to find. "
        + ("Keep it kid-friendly: spooky is fine, scary is not, no real danger or injuries. Make every player matter: for little "
           "ones use puzzles they can solve (colours, animals, counting, sounds) and let them make the big choices. "
           if kids else "Keep it playful: tension is fine, nothing graphic. Make every player matter. ")
        + "Hints: after 2 wrong answers give a hint, after 3 a bigger one; never leave a room a dead end. Use what you know about "
        "the household (names, favourites) to personalise puzzles and characters, but never change the house's real memory. "
        "Sessions run about 15-20 minutes: end on a cliffhanger with a one-line recap. On resume, recap first: current room, "
        "what they carry, cores restored. Track exactly: room, inventory, cores restored, key choices, which player did what. "
        "When every core is restored the front door opens: announce the escape as a big finale and name the MVP."
    )
    entries = [("place", "where", start), ("flag", "cores restored", f"0 of {count}"), ("flag", "front door", "locked")]
    entries += [("flag", f"core: {room}", "locked") for room in rooms]
    return {"name": v["title"] or "Escape the house", "setting": setting, "rules": rules, "entries": entries}
