"""Expectations: Sentient predicts the house, and learns from being wrong.

For every device that switches between states (lights, doors, the heating, people…) it keeps how often each
change happens at each hour, on weekdays and at weekends. Every new change is then scored for **surprise**
from 0 (happens most days around now) to 1 (never seen at this time). Counts fade gently (about a month's
half-life), so new habits take over and a holiday doesn't rewrite the house.

It also learns what usually comes before what ("the back door, then the gate"), so it can tell when a
familiar sequence is broken.

Every change it was ready to judge counts towards the **prediction score**: the share of the day's changes it
expected. That number rising week on week is how Sentient shows it understands the house better.

Warm-up reads the last three weeks from Home Assistant's own history once, so it is useful on day one. Plain
counting, no AI. Switch: "Expectations and surprise" (Settings → Memory & learning).
"""
from __future__ import annotations

from datetime import datetime, timedelta
import logging
from typing import Any

from homeassistant.core import HomeAssistant
from homeassistant.util import dt as dt_util

_LOGGER = logging.getLogger(__name__)

CONF_EXPECTATIONS = "expectations"
DISCRETE = ("binary_sensor", "switch", "light", "lock", "cover", "climate", "media_player", "person",
            "device_tracker", "input_boolean", "fan", "alarm_control_panel", "water_heater", "vacuum", "valve",
            "humidifier", "siren", "lawn_mower", "assist_satellite")
DEAD = ("unavailable", "unknown")
EXPECTED_RATE = 0.25  # happening on a quarter of days around this hour counts as fully expected
KNOWN_AFTER = 5  # changes seen before an entity is judged at all
DECAY = 0.98  # per day
SEQUENCE_SECONDS = 120
WARMUP_DAYS = 21
WARMUP_ENTITIES = 400
MAX_PAIRS = 6000


def bucket(ts: float) -> tuple[int, int]:
    """(day type, hour): day type 0 = weekday, 1 = weekend, in local time."""
    local = dt_util.as_local(dt_util.utc_from_timestamp(ts))
    return (1 if local.weekday() >= 5 else 0), local.hour


def judged(row: dict[str, Any]) -> bool:
    return row.get("domain") in DISCRETE and row.get("new") not in DEAD and row.get("old") not in DEAD


class Expectations:
    def __init__(self) -> None:
        self.counts: dict[str, dict[str, list[float]]] = {}  # entity -> new state -> 48 buckets
        self.days = [0.0, 0.0]  # days observed: weekdays, weekends
        self.pairs: dict[str, float] = {}  # "a=state|b=state" -> times b followed a within two minutes
        self.singles: dict[str, float] = {}  # "b=state" -> times seen (for sequences)
        self.scores: dict[str, dict[str, Any]] = {}  # day -> known, expected, surprises
        self.warmed = False
        self.last_day: str | None = None

    # ----- saving -----------------------------------------------------------------------------
    def dump(self) -> dict[str, Any]:
        return {"counts": self.counts, "days": self.days, "pairs": self.pairs, "singles": self.singles,
                "scores": self.scores, "warmed": self.warmed, "last_day": self.last_day}

    def load(self, data: dict[str, Any] | None) -> None:
        data = data or {}
        self.counts = {e: {s: list(v) for s, v in states.items()} for e, states in (data.get("counts") or {}).items()}
        self.days = list(data.get("days") or [0.0, 0.0])
        self.pairs = dict(data.get("pairs") or {})
        self.singles = dict(data.get("singles") or {})
        self.scores = dict(data.get("scores") or {})
        self.warmed = bool(data.get("warmed"))
        self.last_day = data.get("last_day")

    # ----- learning ---------------------------------------------------------------------------
    def learn(self, entity: str, state: str, ts: float, weight: float = 1.0) -> None:
        kind, hour = bucket(ts)
        row = self.counts.setdefault(entity, {}).setdefault(state, [0.0] * 48)
        row[kind * 24 + hour] += weight

    def learn_sequence(self, row: dict[str, Any], before: list[dict[str, Any]]) -> None:
        key = f"{row['entity']}={row['new']}"
        self.singles[key] = self.singles.get(key, 0.0) + 1
        seen = set()
        for prev in before:
            if prev["entity"] == row["entity"] or not judged(prev) or row["ts"] - prev["ts"] > SEQUENCE_SECONDS:
                continue
            pkey = f"{prev['entity']}={prev['new']}|{key}"
            if pkey in seen:
                continue
            seen.add(pkey)
            self.pairs[pkey] = self.pairs.get(pkey, 0.0) + 1
        if len(self.pairs) > MAX_PAIRS:
            keep = sorted(self.pairs.items(), key=lambda kv: -kv[1])[: MAX_PAIRS // 2]
            self.pairs = dict(keep)

    def new_day(self, day: datetime) -> None:
        """Called once a local day: count the day and let old evidence fade."""
        self.days[1 if day.weekday() >= 5 else 0] += 1
        for states in self.counts.values():
            for values in states.values():
                for i, v in enumerate(values):
                    values[i] = v * DECAY
        self.days = [d * DECAY for d in self.days]
        self.pairs = {k: v * DECAY for k, v in self.pairs.items() if v * DECAY >= 0.2}
        self.singles = {k: v * DECAY for k, v in self.singles.items() if v * DECAY >= 0.2}
        cutoff = (day - timedelta(days=60)).date().isoformat()
        self.scores = {k: v for k, v in self.scores.items() if k >= cutoff}

    # ----- judging ----------------------------------------------------------------------------
    def known(self, entity: str) -> bool:
        states = self.counts.get(entity)
        return bool(states) and sum(sum(v) for v in states.values()) >= KNOWN_AFTER and sum(self.days) >= 3

    def rate(self, entity: str, state: str, ts: float) -> float:
        """Roughly how many times a day this change happens around this hour."""
        values = (self.counts.get(entity) or {}).get(state)
        if not values:
            return 0.0
        kind, hour = bucket(ts)
        base = kind * 24
        near = values[base + hour] + 0.5 * values[base + (hour - 1) % 24] + 0.5 * values[base + (hour + 1) % 24]
        return near / 2 / max(1.0, self.days[kind])

    def surprise(self, row: dict[str, Any]) -> float | None:
        """0 = expected, 1 = never seen at this time; None while it is still learning this device."""
        if not judged(row) or not self.known(row["entity"]):
            return None
        return round(1 - min(1.0, self.rate(row["entity"], row["new"], row["ts"]) / EXPECTED_RATE), 2)

    def usual_before(self, entity: str, state: str) -> tuple[str, float] | None:
        """The change that most often comes just before this one, and how often it does."""
        key = f"{entity}={state}"
        total = self.singles.get(key, 0.0)
        if total < 5:
            return None
        best = max(((k.split("|")[0], v) for k, v in self.pairs.items() if k.endswith("|" + key)),
                   key=lambda kv: kv[1], default=None)
        return (best[0], round(best[1] / total, 2)) if best else None

    def broken_sequence(self, row: dict[str, Any], before: list[dict[str, Any]]) -> str | None:
        """'Back door=on' when that usually comes first and didn't this time."""
        usual = self.usual_before(row["entity"], row["new"])
        if usual is None or usual[1] < 0.6:
            return None
        happened = {f"{r['entity']}={r['new']}" for r in before if row["ts"] - r["ts"] <= SEQUENCE_SECONDS}
        return None if usual[0] in happened else usual[0]

    def observe(self, row: dict[str, Any], before: list[dict[str, Any]]) -> float | None:
        """Judge the change, then learn from it. Returns its surprise."""
        if not judged(row):
            return None
        surprise = self.surprise(row)
        day = dt_util.as_local(dt_util.utc_from_timestamp(row["ts"])).date().isoformat()
        score = self.scores.setdefault(day, {"known": 0, "expected": 0, "surprises": []})
        if surprise is not None:
            score["known"] += 1
            if surprise < 0.5:
                score["expected"] += 1
            elif surprise >= 0.9 and row.get("cause") not in ("sentient",):
                score["surprises"] = (score["surprises"] + [{"entity": row["entity"], "name": row.get("name"),
                                                             "new": row["new"], "ts": row["ts"]}])[-10:]
        self.learn(row["entity"], row["new"], row["ts"])
        self.learn_sequence(row, before)
        return surprise

    def score(self, day: str | None = None) -> dict[str, Any]:
        day = day or dt_util.now().date().isoformat()
        s = self.scores.get(day) or {"known": 0, "expected": 0, "surprises": []}
        return {"day": day, "judged": s["known"], "expected": s["expected"],
                "percent": round(100 * s["expected"] / s["known"]) if s["known"] else None,
                "surprises": s["surprises"]}

    def trend(self, days: int = 21) -> list[dict[str, Any]]:
        out = []
        for key in sorted(self.scores)[-days:]:
            s = self.scores[key]
            if s["known"]:
                out.append({"day": key, "percent": round(100 * s["expected"] / s["known"]), "judged": s["known"]})
        return out


async def async_warm_up(hass: HomeAssistant, model: Expectations) -> int:
    """Learn from the last three weeks of Home Assistant's history (once). Returns changes learned."""
    try:
        from homeassistant.components.recorder import get_instance, history
    except ImportError:
        return 0
    if "recorder" not in hass.config.components:
        return 0
    entities = [s.entity_id for s in hass.states.async_all() if s.domain in DISCRETE][:WARMUP_ENTITIES]
    if not entities:
        return 0
    end = dt_util.utcnow()
    start = end - timedelta(days=WARMUP_DAYS)

    def _read() -> dict[str, list[Any]]:
        return history.get_significant_states(hass, start, end, entities, significant_changes_only=True,
                                              minimal_response=True, no_attributes=True)

    try:
        states = await get_instance(hass).async_add_executor_job(_read)
    except Exception:  # noqa: BLE001 - warm-up is a bonus
        _LOGGER.debug("Expectations warm-up could not read history", exc_info=True)
        return 0
    import asyncio

    learned = 0
    for entity, rows in states.items():
        await asyncio.sleep(0)  # let Home Assistant breathe between devices
        prev = None
        for item in rows:
            value = item.get("state") if isinstance(item, dict) else getattr(item, "state", None)
            when = item.get("last_changed") if isinstance(item, dict) else getattr(item, "last_changed", None)
            if isinstance(when, str):
                when = dt_util.parse_datetime(when)
            if value is None or when is None:
                continue
            if prev is not None and value != prev and value not in DEAD and prev not in DEAD:
                model.learn(entity, value, when.timestamp())
                learned += 1
            prev = value
    day = start
    while day < end:
        local = dt_util.as_local(day)
        model.days[1 if local.weekday() >= 5 else 0] += 1
        day += timedelta(days=1)
    model.warmed = True
    return learned


def information(surprise: float | None) -> str:
    if surprise is None:
        return "still learning"
    if surprise >= 0.9:
        return "very unusual for this time"
    if surprise >= 0.6:
        return "unusual for this time"
    return "usual for this time"

