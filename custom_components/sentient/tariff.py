"""Cheap-electricity suggestions — only with a time-of-use tariff and a price entity chosen.

The price comes from the entity chosen in Settings. If it lists the coming rates (as the Octopus Energy and
Nordpool integrations do), those are used; otherwise the usual price at each hour is learned from the last
week of its history, which suits fixed time-of-use tariffs (Economy 7, Octopus Go…).

Once per appliance (washing machine, dishwasher, dryer), in the evening when someone's about and tonight's
cheapest two hours are well under the price now, it asks: "Electricity's about half price from 1am — want a
nudge in the evenings to set the dishwasher's delay start for then?" A yes gives that nudge on evenings it
hasn't already run; a no means it's never asked again. Plain rules, no AI.
"""

from __future__ import annotations

from datetime import datetime, timedelta
import logging
import re
from statistics import median
from typing import Any, Callable

from homeassistant.core import HomeAssistant, callback
from homeassistant.helpers.event import async_track_time_change
from homeassistant.helpers.storage import Store
from homeassistant.util import dt as dt_util

from .const import CONF_PRICE_ENTITY, DOMAIN
from .reasons import because

_LOGGER = logging.getLogger(__name__)
BIG = re.compile(r"wash|dish|dryer|drier|tumble", re.I)
CHEAPER_BY = 0.3  # the cheap window must be at least this much under the price now
WINDOW_HOURS = 2
LOOK_AHEAD = timedelta(hours=18)
ASK_AT = (19, 30)
NUDGE_AT = (20, 30)
_VALUE_KEYS = ("value_inc_vat", "value", "price", "rate", "total")


def _rates(attributes: dict[str, Any]) -> list[tuple[datetime, float]]:
    """(start, price) pairs from any list attribute shaped like [{start, end, value…}]."""
    out = []
    for value in attributes.values():
        if not isinstance(value, list):
            continue
        for item in value:
            if not isinstance(item, dict):
                continue
            start = item.get("start") or item.get("valid_from") or item.get("from")
            start = start if isinstance(start, datetime) else dt_util.parse_datetime(str(start or ""))
            price = next((item[k] for k in _VALUE_KEYS if isinstance(item.get(k), (int, float))), None)
            if start is not None and price is not None:
                out.append((dt_util.as_utc(start), float(price)))
    return sorted(set(out))


def cheapest(prices: list[tuple[datetime, float]], now: datetime, hours: int = WINDOW_HOURS) -> tuple[datetime, float] | None:
    """The start of the cheapest run of *hours* in the next 18 hours, and its average price."""
    soon = [(t, p) for t, p in prices if now - timedelta(minutes=30) <= t <= now + LOOK_AHEAD]
    if not soon:
        return None
    step = (soon[1][0] - soon[0][0]) if len(soon) > 1 else timedelta(hours=1)
    per = max(1, round(timedelta(hours=hours) / step)) if step.total_seconds() > 0 else 1
    best = None
    for i in range(0, len(soon) - per + 1):
        block = soon[i:i + per]
        if block[-1][0] - block[0][0] > timedelta(hours=hours):
            continue  # a gap in the rates
        avg = sum(p for _, p in block) / len(block)
        if best is None or avg < best[1]:
            best = (block[0][0], avg)
    return best


class CheapPower:
    def __init__(self, hass: HomeAssistant, entry_id: str, options: dict[str, Any], rt_getter: Callable[[], Any]) -> None:
        self.hass = hass
        self.options = options
        self._rt = rt_getter
        self._store: Store[dict[str, Any]] = Store(hass, 1, f"{DOMAIN}.{entry_id}.tariff")
        self.answers: dict[str, str] = {}  # appliance -> "yes" / "no"
        self.usual_by_hour: dict[int, float] = {}
        self._unsubs: list = []

    @property
    def entity(self) -> str | None:
        return self.options.get(CONF_PRICE_ENTITY) or None

    async def async_start(self) -> None:
        data = await self._store.async_load() or {}
        self.answers = dict(data.get("answers") or {})
        self.usual_by_hour = {int(k): float(v) for k, v in (data.get("usual_by_hour") or {}).items()}
        self._unsubs = [
            async_track_time_change(self.hass, self._ask_time, hour=ASK_AT[0], minute=ASK_AT[1], second=0),
            async_track_time_change(self.hass, self._nudge_time, hour=NUDGE_AT[0], minute=NUDGE_AT[1], second=0),
            async_track_time_change(self.hass, self._learn_time, hour=4, minute=10, second=0),
        ]

    @callback
    def async_stop(self) -> None:
        while self._unsubs:
            self._unsubs.pop()()

    def _save(self) -> None:
        self._store.async_delay_save(lambda: {"answers": self.answers, "usual_by_hour": self.usual_by_hour}, 2)

    # ----- prices ---------------------------------------------------------------------------
    def price_now(self) -> float | None:
        st = self.hass.states.get(self.entity) if self.entity else None
        try:
            return float(st.state) if st is not None else None
        except (TypeError, ValueError):
            return None

    def prices(self, now: datetime) -> list[tuple[datetime, float]]:
        st = self.hass.states.get(self.entity) if self.entity else None
        if st is not None and (listed := _rates(dict(st.attributes))):
            return listed
        if not self.usual_by_hour:
            return []
        start = dt_util.as_local(now).replace(minute=0, second=0, microsecond=0)
        return [(dt_util.as_utc(start + timedelta(hours=h)), self.usual_by_hour[(start + timedelta(hours=h)).hour])
                for h in range(0, 19) if (start + timedelta(hours=h)).hour in self.usual_by_hour]

    def tonight(self, now: datetime | None = None) -> dict[str, Any] | None:
        """Tonight's cheap window, if it's well under the price now: {from, until, saving}."""
        now = now or dt_util.utcnow()
        price, best = self.price_now(), cheapest(self.prices(now), now)
        if price is None or best is None or price <= 0 or best[1] > price * (1 - CHEAPER_BY):
            return None
        start = dt_util.as_local(best[0])
        return {"from": start, "until": start + timedelta(hours=WINDOW_HOURS), "saving": 1 - best[1] / price}

    async def async_learn(self) -> None:
        """The usual price at each hour, from the last week of history (for tariffs that don't list rates)."""
        if not self.entity:
            return
        try:
            from homeassistant.components.recorder import get_instance, history

            end = dt_util.utcnow()
            states = await get_instance(self.hass).async_add_executor_job(
                history.state_changes_during_period, self.hass, end - timedelta(days=7), end, self.entity)
        except Exception as err:  # noqa: BLE001 - no recorder: rates from the entity only
            _LOGGER.debug("Couldn't read price history: %s", err)
            return
        by_hour: dict[int, list[float]] = {}
        rows = states.get(self.entity) or []
        for i, st in enumerate(rows):
            try:
                value = float(st.state)
            except (TypeError, ValueError):
                continue
            until = rows[i + 1].last_changed if i + 1 < len(rows) else end
            t = st.last_changed.replace(minute=0, second=0, microsecond=0)
            while t < until:
                by_hour.setdefault(dt_util.as_local(t).hour, []).append(value)
                t += timedelta(hours=1)
        if len(by_hour) >= 20:
            self.usual_by_hour = {h: round(median(v), 4) for h, v in by_hour.items()}
            self._save()

    # ----- asking and nudging ---------------------------------------------------------------
    def appliances(self) -> list[dict[str, Any]]:
        rt = self._rt()
        if rt is None or getattr(rt, "appliances", None) is None:
            return []
        return [a for a in rt.appliances.listing() if BIG.search(a["name"])]

    @staticmethod
    def _when(window: dict[str, Any]) -> str:
        t = window["from"]
        return f"{t.hour % 12 or 12}{'' if t.minute == 0 else f':{t.minute:02d}'}{'am' if t.hour < 12 else 'pm'}"

    @staticmethod
    def _how_much(saving: float) -> str:
        return "about half price" if 0.4 <= saving <= 0.6 else f"about {round(saving * 100)}% cheaper"

    @callback
    def _learn_time(self, _now: datetime) -> None:
        self.hass.async_create_task(self.async_learn())

    @callback
    def _ask_time(self, _now: datetime) -> None:
        self.hass.async_create_task(self.async_ask())

    @callback
    def _nudge_time(self, _now: datetime) -> None:
        self.hass.async_create_task(self.async_nudge())

    async def async_ask(self) -> dict[str, Any] | None:
        rt = self._rt()
        if rt is None or not self.entity or (window := self.tonight()) is None:
            return None
        if rt.tracker is not None and rt.tracker.best_area() is None:
            return None
        appliance = next((a for a in self.appliances() if a["entity_id"] not in self.answers), None)
        if appliance is None:
            return None
        name, eid = appliance["name"].lower(), appliance["entity_id"]

        async def answered(kind: str) -> str:
            self.answers[eid] = "yes" if kind == "yes" else "no"
            self._save()
            return "Okay, I'll mention it in the evenings." if kind == "yes" else "Okay, I won't."

        question = (f"Electricity's {self._how_much(window['saving'])} from {self._when(window)} — want a nudge in the "
                    f"evenings to set the {name}'s delay start for then?")
        with because(f"tonight's cheapest two hours start at {self._when(window)}, "
                     f"{round(window['saving'] * 100)}% under the price now"):
            result = await rt.speech.async_ask(question, source="offer:cheap electricity", on_answer=answered,
                                               extra_prompt="If they say yes or no, just acknowledge it in a few words.")
        if result.spoken:
            self.answers.setdefault(eid, "asked")
            self._save()
        return {"asked": question}

    async def async_nudge(self) -> list[str]:
        rt = self._rt()
        if rt is None or not self.entity or (window := self.tonight()) is None:
            return []
        evening = dt_util.start_of_local_day() + timedelta(hours=17)
        said = []
        for a in self.appliances():
            if self.answers.get(a["entity_id"]) != "yes" or a.get("running"):
                continue
            last = (rt.appliances.last.get(a["entity_id"]) or {}).get("ended")
            if last and (ended := dt_util.parse_datetime(last)) and ended >= evening:
                continue  # already run this evening
            text = f"Electricity's cheapest from {self._when(window)} tonight — a good time for the {a['name'].lower()}'s delay start."
            with because("you asked for a nudge when electricity's cheap overnight"):
                await rt.speech.async_say(text, "low", None, source="cheap electricity")
            said.append(text)
            break  # one nudge an evening
        return said
