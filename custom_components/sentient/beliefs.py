"""Beliefs: claims Sentient forms about the house, tested against what actually happens.

Text lessons are only as good as the moment they were written. A belief makes a prediction, so every day can
prove it right or wrong:

* **by itself** — "the heating goes off by itself around 11am": something nothing in Home Assistant did,
  seen on three or more days at about the same time. Each night it is tested against the day just gone.
* **sequence** — "the gate usually opens after the back door": a change that almost always follows another.

Confidence is (held + 1) / (held + missed + 2). A belief that keeps holding is used (named in answers, asked
about once); one that keeps missing is dropped. No AI. Switch: "Form and test beliefs"
(Settings → Memory & learning).
"""
from __future__ import annotations

from datetime import datetime, timedelta
import hashlib
from typing import Any

from homeassistant.util import dt as dt_util

CONF_BELIEFS = "beliefs"
DEAD = ("unavailable", "unknown")
MIN_DAYS = 3
HELD = 0.7
DROPPED = 0.25
WINDOW_HOURS = 1


def confidence(support: int, against: int) -> float:
    return round((support + 1) / (support + against + 2), 2)


def _id(*parts: Any) -> str:
    return hashlib.sha1("|".join(str(p) for p in parts).encode()).hexdigest()[:12]


def _hour_words(hour: int) -> str:
    return f"{12 if hour % 12 == 0 else hour % 12}{'am' if hour < 12 else 'pm'}"


def find_by_itself(rows: list[dict[str, Any]]) -> list[dict[str, Any]]:
    """Unexplained changes seen on MIN_DAYS or more days at about the same hour (rows: cause 'device')."""
    days: dict[tuple[str, str, int], set[str]] = {}
    names: dict[str, str] = {}
    for r in rows:
        if r.get("old") in DEAD or r.get("new") in DEAD:
            continue  # coming back after a restart or dropout isn't something it did by itself
        local = dt_util.as_local(dt_util.utc_from_timestamp(r["ts"]))
        days.setdefault((r["entity"], r["new"], local.hour), set()).add(local.date().isoformat())
        names[r["entity"]] = r.get("name") or r["entity"]
    out = []
    for (entity, state, hour), seen in days.items():
        near = seen | days.get((entity, state, (hour + 1) % 24), set()) | days.get((entity, state, (hour - 1) % 24), set())
        if len(near) >= MIN_DAYS and len(seen) >= max(1, MIN_DAYS - 1):
            out.append({
                "id": _id("by_itself", entity, state, hour), "kind": "by_itself", "subject": entity,
                "claim": f"{names[entity]} goes to {state} by itself around {_hour_words(hour)}",
                "params": {"entity": entity, "state": state, "hour": hour}, "support": len(near),
                "formed_from": max(near),  # the days it was formed from aren't tested again
            })
    return out


def held_on(belief: dict[str, Any], rows: list[dict[str, Any]]) -> bool:
    """Did a 'by itself' belief happen in these rows (one day's unexplained changes)?"""
    p = belief["params"]
    for r in rows:
        if r["entity"] != p["entity"] or r["new"] != p["state"] or r.get("old") in DEAD:
            continue
        hour = dt_util.as_local(dt_util.utc_from_timestamp(r["ts"])).hour
        if min((hour - p["hour"]) % 24, (p["hour"] - hour) % 24) <= WINDOW_HOURS:
            return True
    return False


def status(support: int, against: int, current: str = "open") -> str:
    conf = confidence(support, against)
    if conf >= HELD and support >= 4:
        return "held"
    if conf <= DROPPED and against >= 5:
        return "dropped"
    return "held" if current == "held" and conf >= 0.5 else "open"


class Beliefs:
    """Read and write beliefs in the database (every call runs in the executor)."""

    def __init__(self, db: Any) -> None:
        self.db = db

    def all(self, conn: Any, include_dropped: bool = False) -> list[dict[str, Any]]:
        import json

        sql = "SELECT * FROM beliefs" + ("" if include_dropped else " WHERE status != 'dropped'")
        out = []
        for r in conn.execute(sql + " ORDER BY support DESC"):
            row = dict(r)
            row["params"] = json.loads(row.get("params") or "{}")
            row["confidence"] = confidence(row["support"], row["against"])
            out.append(row)
        return out

    def upsert_new(self, conn: Any, found: list[dict[str, Any]]) -> int:
        import json

        added = 0
        now = dt_util.utcnow().isoformat()
        for b in found:
            if conn.execute("SELECT 1 FROM beliefs WHERE id = ?", (b["id"],)).fetchone():
                continue
            conn.execute("INSERT INTO beliefs (id, kind, subject, claim, params, support, against, created, tested, status) "
                         "VALUES (?,?,?,?,?,?,0,?,?, 'open')",
                         (b["id"], b["kind"], b["subject"], b["claim"], json.dumps(b["params"]), b.get("support", 0), now,
                          b.get("formed_from")))
            added += 1
        return added

    def test_day(self, conn: Any, day_rows: list[dict[str, Any]], day: str) -> dict[str, int]:
        """Test every open or held 'by itself' belief against one day's unexplained changes."""
        out = {"held": 0, "missed": 0, "dropped": 0}
        for b in self.all(conn):
            if b["kind"] != "by_itself" or (b.get("tested") or "")[:10] >= day:
                continue
            happened = held_on(b, day_rows)
            support, against = b["support"] + int(happened), b["against"] + int(not happened)
            new_status = status(support, against, b["status"])
            conn.execute("UPDATE beliefs SET support = ?, against = ?, tested = ?, status = ? WHERE id = ?",
                         (support, against, day + "T23:59:59", new_status, b["id"]))
            out["held" if happened else "missed"] += 1
            out["dropped"] += int(new_status == "dropped")
        return out

    @staticmethod
    def mark_asked(conn: Any, belief_id: str) -> None:
        conn.execute("UPDATE beliefs SET asked = 1 WHERE id = ?", (belief_id,))

    @staticmethod
    def forget(conn: Any, belief_id: str) -> bool:
        return conn.execute("DELETE FROM beliefs WHERE id = ?", (belief_id,)).rowcount > 0


def day_bounds(day: datetime) -> tuple[float, float]:
    start = dt_util.start_of_local_day(day)
    return start.timestamp(), (start + timedelta(days=1)).timestamp()
