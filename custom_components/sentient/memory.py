"""Long-term memory for Sentient.

Memories are short, durable facts ("the rabbits live in the hutch by the shed",
"Alex likes the kitchen light at 60% in the evening"). They are stored in Home
Assistant's own storage, shared by every voice and automation, and surfaced to
any conversation agent that uses the Sentient LLM API.

Memory 2.0, all plain rules (no AI):
* **Dated things expire.** "The boiler engineer's coming on Thursday" is kept until the day after
  Thursday, then let go. Habits ("every Thursday", "on Thursdays") never expire. Pinned ones never do.
* **Newer replaces older.** "The heating's comfortable at 21" replaces "...at 20"; "the cooler is no
  longer used" replaces "the cooler is used". Only when the wording is the same apart from numbers,
  on/off or a negation, so different facts never overwrite each other, and never when an overnight
  inference would overwrite something a person said. What changed is kept briefly for the Mind page.
* **Linked to the house.** A memory that names a device, a room or a person is linked to it, so asking
  about "the lamp in the living room" finds a memory about "the living room lamp", and everyday
  synonyms (lamp/light, telly/TV, heating/thermostat) match.
"""

from __future__ import annotations

from dataclasses import asdict, dataclass, field
from datetime import date, datetime, timedelta
import re
from typing import Any
import uuid
import unicodedata

from homeassistant.core import HomeAssistant, callback
from homeassistant.helpers.storage import Store
from homeassistant.util import dt as dt_util

from .const import (
    DOMAIN,
    EVENT_MEMORY_CHANGED,
    MEMORY_CATEGORIES,
    MEMORY_MAX_ITEMS,
    MEMORY_STORAGE_VERSION,
)

_WORD = re.compile(r"[^\W_]+(?:'[^\W_]+)*", re.UNICODE)
_STOP = {
    "the", "a", "an", "is", "are", "was", "to", "of", "and", "in", "on", "at",
    "it", "my", "me", "i", "you", "do", "does", "what", "who", "when", "where",
    "for", "with", "that", "this", "about", "like", "likes",
}


def _words(text: str) -> set[str]:
    return {w for w in _WORD.findall(_normalise(text)) if w not in _STOP and (len(w) > 1 or w.isdigit())}


# everyday words for the same thing (both ways), so recall isn't defeated by a choice of word
_SYNONYMS = [
    {"lamp", "light", "lights", "lamps"}, {"telly", "tv", "television"}, {"heating", "thermostat", "radiator", "radiators", "boiler"},
    {"lounge", "living"}, {"bins", "bin", "rubbish", "refuse", "recycling"}, {"fridge", "refrigerator"},
    {"hoover", "vacuum", "hoovering"}, {"car", "vehicle"}, {"kids", "children"}, {"mum", "mother"}, {"dad", "father"},
]


def _expand(words: set[str]) -> set[str]:
    out = set(words)
    for group in _SYNONYMS:
        if words & group:
            out |= group
    return out


_DAYNAMES = ["monday", "tuesday", "wednesday", "thursday", "friday", "saturday", "sunday"]
_MONTHS = ["january", "february", "march", "april", "may", "june", "july", "august", "september", "october", "november", "december"]
_HABIT = re.compile(r"\b(every|each|usually|always|normally|most)\b")


def expiry_for(text: str, today: date | None = None) -> str | None:
    """The day after the date a memory is about ("on Thursday", "tomorrow", "12th November"), or None for
    anything that isn't tied to a day — including habits like "every Thursday" or "on Thursdays"."""
    t = _normalise(text)
    if _HABIT.search(t):
        return None
    today = today or dt_util.now().date()
    when: date | None = None
    if re.search(r"\b(today|tonight|this (morning|afternoon|evening))\b", t):
        when = today
    elif re.search(r"\btomorrow\b", t):
        when = today + timedelta(days=1)
    elif m := re.search(r"\b(this|next)?\s*(monday|tuesday|wednesday|thursday|friday|saturday|sunday)\b(?!s)", t):
        ahead = (_DAYNAMES.index(m.group(2)) - today.weekday()) % 7
        if m.group(1) == "next" and ahead == 0:
            ahead = 7
        when = today + timedelta(days=ahead)
    elif re.search(r"\bthis (week|weekend)\b", t):
        when = today + timedelta(days=(6 - today.weekday()))
    elif m := re.search(r"\b(\d{1,2})(?:st|nd|rd|th)?\s+(?:of\s+)?(" + "|".join(_MONTHS) + r")\b", t):
        day, month = int(m.group(1)), _MONTHS.index(m.group(2)) + 1
        try:
            when = date(today.year, month, day)
            if (today - when).days > 30:
                when = date(today.year + 1, month, day)
        except ValueError:
            when = None
    return (when + timedelta(days=1)).isoformat() if when else None


_CHANGEABLE = re.compile(r"^(\d+(?:\.\d+)?|on|off|not|no|never|longer|anymore|now|yes|open|closed|up|down)$")


def old_is_said(m: "Memory") -> bool:
    return m.source not in (None, "reflection")


def _subject(text: str) -> tuple[str, ...]:
    """The wording with the parts that change (numbers, on/off, negations) taken out."""
    return tuple(w for w in _identity(text) if not _CHANGEABLE.match(w))


def _normalise(text: str) -> str:
    return unicodedata.normalize("NFC", text).casefold().replace("’", "'")


def _identity(text: str) -> tuple[str, ...]:
    # Order, targets, numbers and negation all carry meaning. Only ignore articles and punctuation.
    return tuple(w for w in _WORD.findall(_normalise(text)) if w not in {"the", "a", "an"})


@dataclass
class Memory:
    """A single remembered item."""

    text: str
    category: str = "fact"
    id: str = field(default_factory=lambda: uuid.uuid4().hex[:8])
    created: str = field(default_factory=lambda: dt_util.now().isoformat())
    source: str | None = None
    hits: int = 0
    pinned: bool = False  # always given to the voice, never tidied away
    used: str | None = None  # last time it was recalled or matched what was asked
    expires: str | None = None  # a date (the day after the one it's about); gone after that unless pinned
    links: list[str] = field(default_factory=list)  # entities, rooms (area:...) and people it names

    def as_dict(self) -> dict[str, Any]:
        return asdict(self)


class MemoryStore:
    """Persisted, de-duplicated memory list."""

    def __init__(self, hass: HomeAssistant, entry_id: str) -> None:
        self.hass = hass
        self._store: Store[dict[str, Any]] = Store(
            hass, MEMORY_STORAGE_VERSION, f"{DOMAIN}.{entry_id}.memory"
        )
        self._items: list[Memory] = []
        self.changed: list[dict[str, str]] = []  # recent replacements and expiries, newest last (for the Mind page)
        self._index: tuple[float, list[tuple[str, str]]] | None = None

    async def async_load(self) -> None:
        data = await self._store.async_load() or {}
        known = set(Memory.__dataclass_fields__)
        self._items = [Memory(**{k: v for k, v in item.items() if k in known}) for item in data.get("items", [])]
        self.changed = list(data.get("changed") or [])[-30:]

    @callback
    def _save(self) -> None:
        self._store.async_delay_save(
            lambda: {"items": [m.as_dict() for m in self._items], "changed": self.changed[-30:]}, 5
        )
        self.hass.bus.async_fire(EVENT_MEMORY_CHANGED, {"count": len(self._items)})

    @property
    def items(self) -> list[Memory]:
        return list(self._items)

    def add(self, text: str, category: str = "fact", source: str | None = None) -> Memory:
        """Add a memory; reuse only a wording-equivalent item, preserving meaningful differences."""
        text = " ".join(text.split()).strip()
        if not text:
            raise ValueError("Nothing to remember")
        if category not in MEMORY_CATEGORIES:
            category = "fact"
        identity = _identity(text)
        for existing in self._items:
            if existing.category == category and identity and _identity(existing.text) == identity:
                # A repeated inference must not rewrite an explicit fact, or make old facts look new.
                if source and source != "reflection" and existing.source in (None, "reflection"):
                    existing.source = source
                    self._save()
                return existing
        if len(self._items) >= MEMORY_MAX_ITEMS:
            candidates = [m for m in self._items if not m.pinned]
            if not candidates:
                raise ValueError("Memory is full of pinned items; unpin or forget one before adding another")
            drop = min(candidates, key=lambda m: (m.category != "note", m.hits, m.created))
            self._items.remove(drop)
        memory = Memory(text=text, category=category, source=source, expires=expiry_for(text), links=self.links_for(text))
        # newer replaces older: same wording apart from numbers, on/off or a negation
        subject = _subject(text)
        if len(subject) >= 3:
            # an overnight inference never overwrites something a person said; a person can overwrite anything
            for old in [m for m in self._items if m.category == category and not m.pinned and _subject(m.text) == subject
                        and not (source == "reflection" and old_is_said(m))]:
                self._items.remove(old)
                self._note("replaced", old.text, text)
        self._items.append(memory)
        self._save()
        return memory

    def _note(self, kind: str, old: str, new: str = "") -> None:
        self.changed.append({"kind": kind, "old": old, "new": new, "time": dt_util.now().isoformat()})
        self.changed = self.changed[-30:]

    # ----- linking to the house ---------------------------------------------------------
    def _names(self) -> list[tuple[str, str]]:
        """(lower-case name, link) for devices, rooms and people, longest first; rebuilt every ten minutes."""
        now = dt_util.utcnow().timestamp()
        if self._index and now - self._index[0] < 600:
            return self._index[1]
        from homeassistant.helpers import area_registry as ar

        names: list[tuple[str, str]] = []
        for st in self.hass.states.async_all(("light", "switch", "climate", "fan", "cover", "media_player", "lock", "vacuum", "person", "camera")):
            name = _normalise(str(st.name or ""))  # composed name (2026.11 naming), not the attribute
            if len(name) >= 4:
                names.append((name, st.entity_id))
        for area in ar.async_get(self.hass).async_list_areas():
            for alias in {area.name, *(area.aliases or [])}:
                if len(alias or "") >= 4:
                    names.append((_normalise(alias), f"area:{area.id}"))
        names.sort(key=lambda n: -len(n[0]))
        self._index = (now, names)
        return names

    def links_for(self, text: str) -> list[str]:
        t = " " + " ".join(_WORD.findall(_normalise(text))) + " "
        found: list[str] = []
        for name, link in self._names():
            words = " " + " ".join(_WORD.findall(name)) + " "
            if words.strip() and words in t and link not in found:
                found.append(link)
        return found[:6]

    # ----- expiry ----------------------------------------------------------------------
    def live(self, memory: Memory, today: date | None = None) -> bool:
        if memory.pinned or not memory.expires:
            return True
        return (today or dt_util.now().date()).isoformat() < memory.expires

    def prune_expired(self, today: date | None = None) -> list[Memory]:
        """Let go of memories whose day has passed (pinned ones stay)."""
        gone = [m for m in self._items if not self.live(m, today)]
        for m in gone:
            self._items.remove(m)
            self._note("expired", m.text)
        if gone:
            self._save()
        return gone

    def get(self, memory_id: str) -> Memory | None:
        return next((m for m in self._items if m.id == memory_id), None)

    def update(self, memory_id: str, text: str, category: str | None = None) -> Memory | None:
        """Edit a memory in place (keeps its id and usage count)."""
        memory = self.get(memory_id)
        text = " ".join(text.split()).strip()
        if memory is None or not text:
            return None
        memory.text = text
        memory.expires = expiry_for(text)
        memory.links = self.links_for(text)
        if category in MEMORY_CATEGORIES:
            memory.category = category
        self._save()
        return memory

    def forget(self, memory_id_or_text: str) -> Memory | None:
        """Remove by id, or by the best text match."""
        target = next((m for m in self._items if m.id == memory_id_or_text), None)
        if target is None and _words(memory_id_or_text):
            found = self.search(memory_id_or_text, limit=1, count_hits=False)
            target = found[0] if found else None
        if target is not None:
            self._items.remove(target)
            self._save()
        return target

    def search(self, query: str, limit: int = 8, count_hits: bool = True) -> list[Memory]:
        """Keyword-ranked recall. Good enough for hundreds of short facts."""
        q = _expand(_words(query))
        if not q:
            return self.recent(limit)
        asked_links = set(self.links_for(query))
        scored: list[tuple[float, Memory]] = []
        for memory in self._items:
            if not self.live(memory):
                continue
            words = _words(memory.text)
            score = len(q & words) + 2 * len(asked_links & set(memory.links))
            # partial matches (rabbit / rabbits)
            score += 0.5 * sum(
                1 for a in q for b in words if a != b and (a in b or b in a) and len(a) > 3
            )
            if score:
                scored.append((score, memory))
        scored.sort(key=lambda s: (s[0], s[1].created), reverse=True)
        result = [m for _, m in scored[:limit]]
        if count_hits and result:
            now = dt_util.now().isoformat()
            for m in result:
                m.hits += 1
                m.used = now
            self._save()
        return result

    def pin(self, memory_id: str, pinned: bool = True) -> Memory | None:
        memory = self.get(memory_id)
        if memory is not None:
            memory.pinned = pinned
            self._save()
        return memory

    def for_prompt(self, asked: str, limit: int) -> list[Memory]:
        """What the voice is given: pinned ones, then those matching what was asked (words and word stems),
        then the most used lately, then the newest — never more than *limit*."""
        everything = [m for m in self._items if m.category != "correction" and self.live(m)]
        if limit <= 0:
            return []
        pinned = [m for m in everything if m.pinned][:limit]
        rest = [m for m in everything if not m.pinned]
        q = _expand(_words(asked))
        asked_links = set(self.links_for(asked)) if asked else set()
        matched: list[Memory] = []
        if q or asked_links:
            scored = []
            for i, m in enumerate(rest):
                words = _words(m.text)
                score = len(q & words) + 2 * len(asked_links & set(m.links)) + 0.5 * sum(1 for a in q for b in words if a != b and len(a) > 3 and (a in b or b in a))
                if score:
                    scored.append((score, i, m))
            scored.sort(key=lambda t: (-t[0], -t[1]))
            matched = [m for _, _, m in scored][: max(0, (limit - len(pinned)) // 2)]
            if matched:
                now = dt_util.now().isoformat()
                for m in matched:
                    m.used = now
        chosen = pinned + matched
        left = [m for m in rest if m not in chosen]
        used = sorted((m for m in left if m.used), key=lambda m: (m.used, m.hits), reverse=True)
        newest = list(reversed(left))
        for m in used[: max(0, (limit - len(chosen)) // 2)] + newest:
            if len(chosen) >= limit:
                break
            if m not in chosen:
                chosen.append(m)
        return chosen

    def recent(self, limit: int) -> list[Memory]:
        return sorted(self._items, key=lambda m: (m.hits, m.created), reverse=True)[:limit]
