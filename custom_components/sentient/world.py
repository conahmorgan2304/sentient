"""The world outside, through the same pipeline: readings people choose, and parcels on their way.

* **Outside information** — any sensors chosen (a parcel count, today's energy price, a weather warning, the
  school's next inset day) are read as plain facts into the briefing and the evening look at tomorrow.
  Empty, zero and "off" readings are left out. Their text is quoted data, never instructions.
* **Expecting a parcel** — when a chosen parcel tracker says something is out for delivery, it's said once
  ("a parcel's out for delivery today"), the door is watched for it all day (even if parcel alerts are
  normally off), and if the tracker says delivered before anything was seen at the door, that's said too.
Plain rules, no AI.
"""

from __future__ import annotations

from datetime import datetime
import re
from typing import Any, Callable

from homeassistant.const import EVENT_STATE_CHANGED
from homeassistant.core import Event, HomeAssistant, State, callback
from homeassistant.helpers.storage import Store
from homeassistant.util import dt as dt_util

from .const import CONF_OUTSIDE_INFO, CONF_PARCEL_TRACKERS, DOMAIN
from .reasons import because

OUT_FOR_DELIVERY = re.compile(r"out for delivery|with (?:the |your )?(?:courier|driver)|arriving today|due today|on (?:its|the) way today", re.I)
DELIVERED = re.compile(r"\bdelivered\b|left (?:at|in|with|by)|handed to", re.I)
EMPTY = {"", "unknown", "unavailable", "none", "off", "0", "0.0", "false", "[]", "no"}
MAX_TEXT = 160


def _text(st: State) -> str:
    """What a tracker says: its state when that's words, else its short text attributes (a parcel list)."""
    try:
        float(st.state)
    except (TypeError, ValueError):
        return str(st.state)
    bits = []
    for value in st.attributes.values():
        if isinstance(value, str) and len(value) < 300:
            bits.append(value)
        elif isinstance(value, list):
            bits.extend(str(v) for v in value[:10] if isinstance(v, (str, dict)))
    return " ".join(bits)


def readings(hass: HomeAssistant, entity_ids: list[str]) -> list[str]:
    """'Parcels: 2', 'Electricity price: 0.21 GBP/kWh', 'Met Office warnings: Yellow warning of wind'."""
    out = []
    for entity_id in entity_ids or []:
        st = hass.states.get(entity_id)
        if st is None or str(st.state).strip().lower() in EMPTY:
            continue
        unit = st.attributes.get("unit_of_measurement") or ""
        value = str(st.state)[:MAX_TEXT]
        if st.domain == "binary_sensor":
            value = "yes"
        out.append(f"{st.name}: {value}{(' ' + unit) if unit else ''}")
    return out


class Parcels:
    def __init__(self, hass: HomeAssistant, entry_id: str, options: dict[str, Any], rt_getter: Callable[[], Any]) -> None:
        self.hass = hass
        self.options = options
        self._rt = rt_getter
        self._store: Store[dict[str, Any]] = Store(hass, 1, f"{DOMAIN}.{entry_id}.parcels")
        self.expected: dict[str, str] = {}  # tracker -> the day it said out for delivery
        self.delivered: dict[str, str] = {}  # tracker -> the day it said delivered
        self._unsub = None

    @property
    def trackers(self) -> list[str]:
        return list(self.options.get(CONF_PARCEL_TRACKERS) or [])

    def outside(self) -> list[str]:
        return readings(self.hass, list(self.options.get(CONF_OUTSIDE_INFO) or []))

    async def async_start(self) -> None:
        data = await self._store.async_load() or {}
        self.expected, self.delivered = dict(data.get("expected") or {}), dict(data.get("delivered") or {})
        self._unsub = self.hass.bus.async_listen(EVENT_STATE_CHANGED, self._changed)

    @callback
    def async_stop(self) -> None:
        if self._unsub:
            self._unsub()
            self._unsub = None

    def _save(self) -> None:
        self._store.async_delay_save(lambda: {"expected": self.expected, "delivered": self.delivered}, 2)

    @property
    def expecting_today(self) -> bool:
        today = dt_util.now().date().isoformat()
        return any(day == today for t, day in self.expected.items() if self.delivered.get(t) != today)

    @callback
    def _changed(self, event: Event) -> None:
        entity_id = event.data.get("entity_id")
        if entity_id not in self.trackers or (new := event.data.get("new_state")) is None:
            return
        self.hass.async_create_task(self.async_check(new))

    async def async_check(self, st: State, now: datetime | None = None) -> str | None:
        """Look at what a tracker says now: 'out for delivery', 'delivered' or None."""
        today = dt_util.as_local(now or dt_util.utcnow()).date().isoformat()
        text = _text(st)
        rt = self._rt()
        if OUT_FOR_DELIVERY.search(text):
            if self.expected.get(st.entity_id) == today:
                return None
            self.expected[st.entity_id] = today
            self._save()
            if rt is not None:
                rt.timeline.add("watch", f"{st.name}: out for delivery", "watching the door for it today")
                with because(f"{st.name} says it's out for delivery"):
                    await rt.speech.async_say("A parcel's out for delivery today — I'll keep an eye on the door.",
                                              "low", None, source="parcel")
            return "out for delivery"
        if DELIVERED.search(text):
            if self.delivered.get(st.entity_id) == today or self.expected.get(st.entity_id) != today:
                return None  # only a parcel it was expecting today (not last week's in the list)
            self.delivered[st.entity_id] = today
            self._save()
            if rt is not None:
                rt.timeline.add("visitor", f"{st.name}: delivered", "the parcel tracker", parcel=True)
                seen = rt.visitors.parcel_waiting
                if not seen:
                    with because(f"{st.name} says it's been delivered"):
                        await rt.speech.async_say("The tracker says a parcel's been delivered — worth a look at the door.",
                                                  "normal", None, source="parcel")
            return "delivered"
        return None
