"""Goals: something the household wants to change over days or weeks ("use less heating this month").

Set by voice (sentient_goal) or kept from the page. A goal can follow a sensor: when it's set, the
usual daily value is taken from the last week of history; every night yesterday's value is compared
with it and noted, and the voice is told how it's going so it can mention progress or suggest
something when it fits. When the time is up it's closed with a summary on the timeline. No AI of
its own; nothing is changed in the house. Off until switched on.
"""

from __future__ import annotations

from datetime import datetime, timedelta
from typing import Any, Callable
import uuid

from homeassistant.core import HomeAssistant, callback
from homeassistant.helpers.event import async_track_time_change
from homeassistant.helpers.storage import Store
from homeassistant.util import dt as dt_util

from .const import CONF_GOALS, DOMAIN

STORAGE_VERSION = 1
MAX_OPEN = 8


def _num(v: Any) -> float | None:
    try:
        return float(v)
    except (TypeError, ValueError):
        return None


async def daily_values(hass: HomeAssistant, entity_id: str, days: int, end: datetime | None = None) -> list[tuple[str, float]]:
    """One value per local day: the day's rise for running totals (energy), else the day's average."""
    if "recorder" not in hass.config.components:
        return []
    from homeassistant.components.recorder import get_instance, history

    end_local = dt_util.as_local(end or dt_util.utcnow()).replace(hour=0, minute=0, second=0, microsecond=0)
    start = end_local - timedelta(days=days)
    st = hass.states.get(entity_id)
    total = bool(st and st.attributes.get("state_class") in ("total", "total_increasing"))
    rows = await get_instance(hass).async_add_executor_job(
        lambda: history.state_changes_during_period(hass, dt_util.as_utc(start), dt_util.as_utc(end_local), entity_id,
                                                    include_start_time_state=True)
    )
    by_day: dict[str, list[float]] = {}
    for s in rows.get(entity_id, []):
        v = _num(s.state)
        if v is None:
            continue
        day = dt_util.as_local(s.last_changed).date().isoformat()
        by_day.setdefault(day, []).append(v)
    out = []
    for day in sorted(by_day):
        vals = by_day[day]
        out.append((day, round(max(vals) - min(vals), 2) if total else round(sum(vals) / len(vals), 2)))
    return out


class Goals:
    def __init__(self, hass: HomeAssistant, entry_id: str, options: dict[str, Any], rt_getter: Callable[[], Any]) -> None:
        self.hass = hass
        self.options = options
        self._rt = rt_getter
        self._store: Store[dict[str, Any]] = Store(hass, STORAGE_VERSION, f"{DOMAIN}.{entry_id}.goals")
        self.items: list[dict[str, Any]] = []
        self._unsub = None

    @property
    def enabled(self) -> bool:
        return bool(self.options.get(CONF_GOALS))

    async def async_start(self) -> None:
        self.items = list((await self._store.async_load() or {}).get("items", []))
        self._unsub = async_track_time_change(self.hass, self._nightly, hour=4, minute=50, second=0)

    @callback
    def async_stop(self) -> None:
        if self._unsub:
            self._unsub()
            self._unsub = None

    @callback
    def _save(self) -> None:
        self._store.async_delay_save(lambda: {"items": self.items[-40:]}, 5)

    def open(self) -> list[dict[str, Any]]:
        return [g for g in self.items if not g.get("closed")]

    async def async_set(self, title: str, entity_id: str | None = None, aim: str = "lower", days: int = 30) -> dict[str, Any]:
        if len(self.open()) >= MAX_OPEN:
            return {"error": f"there are already {MAX_OPEN} goals; close one first"}
        days = max(1, min(int(days), 120))
        goal = {"id": uuid.uuid4().hex[:8], "title": title.strip()[:120], "entity_id": entity_id, "aim": aim if aim in ("lower", "higher") else "lower",
                "created": dt_util.utcnow().isoformat(), "ends": (dt_util.utcnow() + timedelta(days=days)).isoformat(),
                "baseline": None, "checks": [], "closed": None}
        if entity_id:
            if self.hass.states.get(entity_id) is None:
                return {"error": f"no sensor called {entity_id}"}
            week = await daily_values(self.hass, entity_id, 7)
            if week:
                goal["baseline"] = round(sum(v for _, v in week) / len(week), 2)
        self.items.append(goal)
        self._save()
        if (rt := self._rt()) is not None:
            rt.timeline.add("mind", f"New goal: {goal['title']}", f"until {dt_util.as_local(dt_util.parse_datetime(goal['ends'])):%d %b}")
        return {"goal": goal["title"], "usual_per_day": goal["baseline"], "ends": goal["ends"]}

    def close(self, words: str) -> dict[str, Any] | None:
        low = words.lower().strip()
        for g in self.open():
            if g["id"] == low or low in g["title"].lower():
                g["closed"] = dt_util.utcnow().isoformat()
                self._save()
                return g
        return None

    @staticmethod
    def progress(goal: dict[str, Any]) -> str | None:
        if not goal.get("checks") or goal.get("baseline") in (None, 0):
            return None
        recent = goal["checks"][-7:]
        avg = sum(c["value"] for c in recent) / len(recent)
        change = (avg - goal["baseline"]) / abs(goal["baseline"]) * 100
        better = change < 0 if goal["aim"] == "lower" else change > 0
        return (f"{abs(round(change))}% {'lower' if change < 0 else 'higher'} than usual over the last {len(recent)} day"
                f"{'s' if len(recent) > 1 else ''} — {'going the right way' if better and abs(change) >= 3 else 'about the same' if abs(change) < 3 else 'going the wrong way'}")

    @callback
    def _nightly(self, _now) -> None:
        if self.enabled and self.open():
            self.hass.async_create_task(self.async_check(), eager_start=False)

    async def async_check(self) -> None:
        rt = self._rt()
        now = dt_util.utcnow()
        for g in self.open():
            if g.get("entity_id"):
                vals = await daily_values(self.hass, g["entity_id"], 1)
                if vals and not any(c["day"] == vals[-1][0] for c in g["checks"]):
                    g["checks"] = (g["checks"] + [{"day": vals[-1][0], "value": vals[-1][1]}])[-120:]
            if (end := dt_util.parse_datetime(g["ends"])) and now >= end:
                g["closed"] = now.isoformat()
                if rt is not None:
                    rt.timeline.add("mind", f"Goal finished: {g['title']}", self.progress(g) or "no readings to compare")
        self._save()

    def listing(self) -> list[dict[str, Any]]:
        out = []
        for g in self.open():
            st = self.hass.states.get(g["entity_id"]) if g.get("entity_id") else None
            out.append({"id": g["id"], "title": g["title"], "follows": st.name if st else None,
                        "usual": g.get("baseline"), "unit": st.attributes.get("unit_of_measurement") if st else None,
                        "progress": self.progress(g), "ends": g["ends"], "days_checked": len(g["checks"])})
        return out

    def prompt_line(self) -> str | None:
        goals = self.listing()
        if not goals:
            return None
        parts = [f"{g['title']}" + (f" ({g['progress']})" if g["progress"] else "") for g in goals]
        return ("The household's goals (mention progress or suggest something that helps only when it fits naturally): "
                + "; ".join(parts) + ".")
