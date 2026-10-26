"""Curiosity: ask about things it can't explain, and remember the answer.

Questions come from patterns Sentient can see but not explain:
* someone unnamed coming to the door on the same weekday, week after week;
* the overnight reflection, which may come up with one thing it's wondering about.

At most one question a day, in the daytime, out of quiet hours, only when someone's about and
nothing else has been said for a while, never with guests in. Each question is asked once. The
voice agent is told to save what it learns with sentient_remember, so every voice knows it.
"""

from __future__ import annotations

from collections import Counter
from datetime import datetime, time, timedelta
import hashlib
import logging
from typing import Any, Callable

from homeassistant.core import HomeAssistant, callback
from homeassistant.helpers.event import async_track_time_change
from homeassistant.helpers.storage import Store
from homeassistant.util import dt as dt_util

from .const import CONF_CURIOSITY, CONF_QUIET_START, DEFAULT_QUIET_START, DOMAIN

_LOGGER = logging.getLogger(__name__)

EARLIEST = time(10, 0)
QUIET_FOR = timedelta(minutes=10)  # nothing said for this long before asking
DAYS = ["Monday", "Tuesday", "Wednesday", "Thursday", "Friday", "Saturday", "Sunday"]


def _key(text: str) -> str:
    return hashlib.sha1(text.lower().encode()).hexdigest()[:8]


class Curiosity:
    def __init__(self, hass: HomeAssistant, entry_id: str, options: dict[str, Any], rt_getter: Callable[[], Any]) -> None:
        self.hass = hass
        self.options = options
        self._rt = rt_getter
        self._store: Store[dict[str, Any]] = Store(hass, 1, f"{DOMAIN}.{entry_id}.curiosity")
        self.queue: list[dict[str, Any]] = []
        self.asked: dict[str, dict[str, Any]] = {}  # key -> {question, time}
        self.last_day: str | None = None
        self._unsub = None

    async def async_start(self) -> None:
        data = await self._store.async_load() or {}
        self.queue = data.get("queue", [])
        self.asked = data.get("asked", {})
        self.last_day = data.get("last_day")
        self._unsub = async_track_time_change(self.hass, self._tick, minute=list(range(0, 60, 15)), second=30)

    @callback
    def async_stop(self) -> None:
        if self._unsub:
            self._unsub()
            self._unsub = None

    @callback
    def _save(self) -> None:
        self._store.async_delay_save(
            lambda: {"queue": self.queue, "asked": self.asked, "last_day": self.last_day}, 5
        )

    @property
    def enabled(self) -> bool:
        return bool(self.options.get(CONF_CURIOSITY, True))

    def wonder(self, question: str, why: str = "", source: str = "") -> bool:
        """Add something to ask about (once ever)."""
        question = " ".join((question or "").split())
        if len(question) < 8:
            return False
        key = _key(question)
        if key in self.asked or any(q["key"] == key for q in self.queue):
            return False
        self.queue.append({"key": key, "question": question, "why": why, "source": source,
                           "added": dt_util.utcnow().isoformat()})
        del self.queue[:-5]  # only the freshest few
        self._save()
        return True

    def listing(self) -> dict[str, Any]:
        recent = sorted(self.asked.values(), key=lambda a: a.get("time", ""), reverse=True)[:5]
        return {"wondering": [q["question"] for q in self.queue], "asked": [a["question"] for a in recent]}

    def dismiss(self, words: str) -> bool:
        needle = words.lower()
        before = len(self.queue)
        self.queue = [q for q in self.queue if needle not in q["question"].lower()]
        if len(self.queue) != before:
            self._save()
            return True
        return False

    # ----- finding things to wonder about -----------------------------------------------
    def find_regular_callers(self) -> None:
        """Unnamed callers at the door on the same weekday, three of the last four weeks."""
        rt = self._rt()
        if rt is None:
            return
        cutoff = dt_util.utcnow() - timedelta(days=28)
        seen: Counter[tuple[int, int]] = Counter()
        weeks: dict[tuple[int, int], set[int]] = {}
        for v in rt.visitors.log:
            when = dt_util.parse_datetime(v.get("time") or "")
            if when is None or when < cutoff or v.get("name") or v.get("kind") == "delivery":
                continue
            local = dt_util.as_local(when)
            slot = (local.weekday(), local.hour // 3)  # same weekday, same part of the day
            weeks.setdefault(slot, set()).add(local.isocalendar().week)
            seen[slot] += 1
        for slot, wks in weeks.items():
            if len(wks) >= 3:
                day, part = slot
                start = part * 3
                when = f"{start % 12 or 12}{'am' if start < 12 else 'pm'}"
                self.wonder(
                    f"Someone's been coming to the door on {DAYS[day]}s, around {when}, for a few weeks now. "
                    "Do you know who that is?",
                    why="regular unnamed caller", source="visitors",
                )

    # ----- asking ----------------------------------------------------------------------------
    @callback
    def _tick(self, now: datetime) -> None:
        if not self.enabled:
            return
        local = dt_util.as_local(now)
        if local.minute == 0 and local.hour == 9:
            self.find_regular_callers()
        if not self.queue or self.last_day == local.date().isoformat() or local.time() < EARLIEST:
            return
        try:
            quiet = time.fromisoformat(self.options.get(CONF_QUIET_START) or DEFAULT_QUIET_START)
        except ValueError:
            quiet = time(21, 0)
        if (datetime.combine(local.date(), quiet) - datetime.combine(local.date(), local.time())) < timedelta(hours=1):
            return
        self.hass.async_create_task(self.async_ask(), eager_start=False)

    async def async_ask(self, force: bool = False) -> dict[str, Any]:
        rt = self._rt()
        if rt is None or not self.queue:
            return {"asked": None, "why": "nothing to ask"}
        speech = rt.speech
        if not force:
            if speech.guest_mode or speech.quiet_now():
                return {"asked": None, "why": "not a good time"}
            if rt.tracker.best_area() is None:
                return {"asked": None, "why": "nobody about"}
            said = speech._said[0][0] if speech._said else None
            if said and dt_util.utcnow() - said < QUIET_FOR:
                return {"asked": None, "why": "just spoke"}
        item = self.queue[0]
        result = await speech.async_ask(
            item["question"], "low" if not force else "normal", source="curious",
            extra_prompt=(
                "You asked because you were curious. When they answer, save what you learned with "
                "sentient_remember as one plain, durable fact (category fact) — e.g. 'The Tuesday morning "
                "caller is the window cleaner.' Then thank them in a few words. If they don't know or would "
                "rather not say, just say no problem and save nothing."
            ),
        )
        if not result.spoken:
            return {"asked": None, "why": result.reason}
        self.queue.pop(0)
        self.asked[item["key"]] = {"question": item["question"], "time": dt_util.utcnow().isoformat()}
        self.last_day = dt_util.now().date().isoformat()
        self._save()
        rt.timeline.add("mind", item["question"], "asked out of curiosity")
        return {"asked": item["question"]}
