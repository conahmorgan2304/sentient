"""Learning from feedback: the house gets quieter about what people don't want to hear.

Signals, all free (no AI):

* someone shushes a thing ("stop telling me about the washing machine");
* someone hushes the house within a couple of minutes of a remark — taken as
  "not that again" for whatever the remark was about;
* a learned routine or habit offered for something is declined or deleted;
* a thumbs down on something it said (the sidebar page) — about the things the remark was about, or,
  for its own kinds of remark (appliances, morning thoughts, welcomes…), that kind. A thumbs up takes
  a point off and is kept as an example of what people like hearing.

Each signal adds to a score per thing, which fades by half every month, so
nothing is held against it for ever. Unshushing takes the score back down.

* remarks: from 2 points a thing is only mentioned when it really matters (the
  AI is told so); from 3 it isn't noticed at all.
* offers: two declines or deletions for a thing, and no more are offered for it.

Everything learned is listed, and can be reset, on the Shushed settings page.
"""

from __future__ import annotations

from datetime import datetime, timedelta
import re
from typing import Any

from homeassistant.core import HomeAssistant, callback
from homeassistant.helpers.storage import Store
from homeassistant.util import dt as dt_util

from .const import DOMAIN

STORAGE_VERSION = 1
HALF_LIFE_DAYS = 30
IMPORTANT_ONLY = 2.0
QUIET = 3.0
NO_MORE_OFFERS = 2.0
HUSH_AFTER_REMARK = timedelta(minutes=2)

SHUSHED = 1.5
THUMB = 1.0
# kinds of remark a thumbs down can quieten as a whole (never safety, alarms, the doorbell or briefings asked for)
_GENERIC = {"the", "sensor", "status", "power", "state", "living", "room"}
QUIETENABLE = ("thought", "welcome", "appliance", "animals", "autooff", "learning", "curious",
               "followup", "reflection", "offer:")
HUSHED_AFTER = 1.0
DECLINED = 1.0
DELETED = 1.5


class Feedback:
    """Scores per thing and kind ("remarks" or "offers"), fading over time."""

    def __init__(self, hass: HomeAssistant, entry_id: str) -> None:
        self.hass = hass
        self._store: Store[dict[str, Any]] = Store(hass, STORAGE_VERSION, f"{DOMAIN}.{entry_id}.feedback")
        self.items: dict[str, dict[str, Any]] = {}  # "kind|entity_id" -> {score, updated, why}
        self.last_remark: tuple[datetime, list[str]] | None = None
        self.about: dict[str, list[str]] = {}  # recent remark text -> what it was about
        self.liked: list[str] = []  # remarks given a thumbs up

    async def async_load(self) -> None:
        data = await self._store.async_load() or {}
        self.items = data.get("items", {})
        self.liked = list(data.get("liked") or [])

    @callback
    def _save(self) -> None:
        self._store.async_delay_save(lambda: {"items": self.items, "liked": self.liked}, 2)

    @staticmethod
    def _faded(item: dict[str, Any], now: datetime) -> float:
        updated = dt_util.parse_datetime(item.get("updated") or "") or now
        days = max(0.0, (now - updated).total_seconds() / 86400)
        return float(item.get("score", 0.0)) * 0.5 ** (days / HALF_LIFE_DAYS)

    def score(self, entity_id: str, kind: str = "remarks") -> float:
        item = self.items.get(f"{kind}|{entity_id}")
        return self._faded(item, dt_util.utcnow()) if item else 0.0

    @callback
    def record(self, entity_id: str, weight: float, why: str, kind: str = "remarks") -> float:
        now = dt_util.utcnow()
        key = f"{kind}|{entity_id}"
        item = self.items.get(key) or {"score": 0.0, "why": []}
        score = max(0.0, self._faded(item, now) + weight)
        if score < 0.1:
            self.items.pop(key, None)
        else:
            whys = ([why] + [w for w in item.get("why", []) if w != why])[:4] if weight > 0 else item.get("why", [])
            self.items[key] = {"score": round(score, 2), "updated": now.isoformat(), "why": whys}
        self._save()
        return score

    # ----- what it means -------------------------------------------------------
    def remark_level(self, entity_id: str) -> str | None:
        s = self.score(entity_id)
        return "quiet" if s >= QUIET else "important_only" if s >= IMPORTANT_ONLY else None

    def offers_blocked(self, entity_id: str) -> bool:
        return self.score(entity_id, "offers") >= NO_MORE_OFFERS

    # ----- signals ------------------------------------------------------------------
    @callback
    def remarked(self, entity_ids: list[str], text: str | None = None) -> None:
        self.last_remark = (dt_util.utcnow(), entity_ids)
        if text:
            self.about[" ".join(text.split())] = list(entity_ids)
            while len(self.about) > 40:
                self.about.pop(next(iter(self.about)))

    def _named_in(self, text: str, candidates: list[str]) -> list[str]:
        """Which of these things the remark names ("the kettle…" -> the kettle's sensor)."""
        words = set(re.findall(r"[a-z]+", text.lower()))
        out = []
        for entity_id in candidates:
            st = self.hass.states.get(entity_id)
            name = [w for w in re.findall(r"[a-z]+", (st.name if st else entity_id).lower())
                    if len(w) > 2 and w not in _GENERIC]
            if name and name[0] in words:
                out.append(entity_id)
        return out

    @staticmethod
    def quietenable(source: str | None) -> bool:
        return bool(source) and source.startswith(QUIETENABLE)

    def source_quiet(self, source: str | None) -> bool:
        """A kind of remark people have given enough thumbs down to: not said any more."""
        return self.quietenable(source) and self.score(f"source:{source}") >= QUIET - 0.05  # three thumbs down

    @callback
    def thumb(self, text: str, source: str | None, up: bool, candidates: list[str] | None = None) -> dict[str, Any]:
        """A thumbs up or down from the page on something that was said."""
        text = " ".join((text or "").split())
        weight = -THUMB if up else THUMB
        why = "thumbs up" if up else "thumbs down"
        if up and text:
            self.liked = ([text] + [t for t in self.liked if t != text])[:8]
        entities = self.about.get(text) or self._named_in(text, candidates or [])
        if entities:
            for entity_id in entities:
                self.record(entity_id, weight, why)
            about = [st.name if (st := self.hass.states.get(e)) else e for e in entities]
        elif self.quietenable(source):
            self.record(f"source:{source}", weight, why)
            about = [source]
        else:
            about = []
        self._save()
        return {"about": about, "up": up}

    @callback
    def hushed(self) -> list[str]:
        """The house was hushed: if a remark was just made, count it against what it was about."""
        if self.last_remark is None:
            return []
        when, ids = self.last_remark
        self.last_remark = None
        if dt_util.utcnow() - when > HUSH_AFTER_REMARK:
            return []
        for entity_id in ids:
            self.record(entity_id, HUSHED_AFTER, "hushed straight after a remark")
        return ids

    # ----- page -------------------------------------------------------------------
    def listing(self) -> list[dict[str, Any]]:
        now = dt_util.utcnow()
        out = []
        for key, item in self.items.items():
            kind, entity_id = key.split("|", 1)
            score = self._faded(item, now)
            if score < 0.5:
                continue
            st = self.hass.states.get(entity_id)
            name = st.name if st else (f"{entity_id[7:].replace(':', ' — ')} remarks" if entity_id.startswith("source:") else entity_id)
            if kind == "offers":
                effect = "no more offers" if score >= NO_MORE_OFFERS else "fewer offers"
            else:
                effect = "not mentioned" if score >= QUIET else "only if it matters" if score >= IMPORTANT_ONLY else "noted"
            out.append({
                "key": key, "kind": kind, "entity_id": entity_id, "name": name,
                "score": round(score, 1), "effect": effect, "why": item.get("why", []),
            })
        return sorted(out, key=lambda i: i["score"], reverse=True)

    @callback
    def reset(self, key: str | None = None) -> int:
        if key is None:
            n = len(self.items)
            self.items = {}
        else:
            hits = [k for k in self.items if k == key or k.split("|", 1)[1] == key]
            for k in hits:
                self.items.pop(k)
            n = len(hits)
        self._save()
        return n
