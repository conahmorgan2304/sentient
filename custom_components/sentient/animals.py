"""Animals that live outside: a word when their home gets too cold or too hot.

For each chosen temperature sensor (a hutch, a kennel, a coop, a tank): one remark when it drops below
the "too cold" line or rises above the "too hot" one, and not again until it has come back a degree.
In quiet hours a cold remark is kept for the morning rather than waking anyone. The evening look at
tomorrow knows about them too, so a frost brings a warning the night before. Plain rules, no AI.
"""

from __future__ import annotations

import logging
from typing import Any, Callable

from homeassistant.const import EVENT_STATE_CHANGED
from homeassistant.core import Event, HomeAssistant, State, callback
from homeassistant.util import dt as dt_util

from .const import (
    CONF_ANIMAL_HIGH,
    CONF_ANIMAL_LOW,
    CONF_ANIMAL_SENSORS,
    CONF_QUIET_END,
    DEFAULT_ANIMAL_HIGH,
    DEFAULT_ANIMAL_LOW,
    DEFAULT_QUIET_END,
)

from .followyou import next_quiet_end

_LOGGER = logging.getLogger(__name__)
HYSTERESIS = 1.0


def place(st: State) -> str:
    """'Rabbit hutch Temperature' -> 'the rabbit hutch'."""
    name = st.name
    for word in (" temperature", " temp"):
        if name.lower().endswith(word):
            name = name[: -len(word)]
    name = name.strip()
    if len(name) > 1 and name[0].isupper() and not name[1].isupper():
        name = name[0].lower() + name[1:]
    return name if name.lower().startswith("the ") else f"the {name}"


def _temp(st: State | None) -> float | None:
    try:
        return float(st.state) if st is not None else None
    except ValueError:
        return None


class Animals:
    def __init__(self, hass: HomeAssistant, options: dict[str, Any], rt_getter: Callable[[], Any]) -> None:
        self.hass = hass
        self.options = options
        self._rt = rt_getter
        self._state: dict[str, str] = {}  # sensor -> "cold" / "hot" while it's out of range
        self._unsub = None

    async def async_start(self) -> None:
        self._unsub = self.hass.bus.async_listen(EVENT_STATE_CHANGED, self._changed)
        for entity_id in self.sensors:  # already out of range at start-up: don't say it again
            if (kind := self._kind(_temp(self.hass.states.get(entity_id)), None)) is not None:
                self._state[entity_id] = kind

    @callback
    def async_stop(self) -> None:
        if self._unsub:
            self._unsub()
            self._unsub = None

    @property
    def sensors(self) -> list[str]:
        return list(self.options.get(CONF_ANIMAL_SENSORS) or [])

    @property
    def low(self) -> float:
        return float(self.options.get(CONF_ANIMAL_LOW, DEFAULT_ANIMAL_LOW))

    @property
    def high(self) -> float:
        return float(self.options.get(CONF_ANIMAL_HIGH, DEFAULT_ANIMAL_HIGH))

    def _kind(self, value: float | None, now: str | None) -> str | None:
        """cold / hot / None, with a degree's grace before it counts as back to normal."""
        if value is None:
            return now
        if value < self.low or (now == "cold" and value < self.low + HYSTERESIS):
            return "cold"
        if value > self.high or (now == "hot" and value > self.high - HYSTERESIS):
            return "hot"
        return None

    @callback
    def _changed(self, event: Event) -> None:
        new = event.data.get("new_state")
        if new is None or new.entity_id not in self.sensors:
            return
        value = _temp(new)
        was = self._state.get(new.entity_id)
        kind = self._kind(value, was)
        if kind == was:
            return
        if kind is None:
            self._state.pop(new.entity_id, None)
            return
        self._state[new.entity_id] = kind
        self.hass.async_create_task(self._async_say(new, value, kind), eager_start=False)

    async def _async_say(self, st: State, value: float, kind: str) -> None:
        rt = self._rt()
        if rt is None:
            return
        where = place(st)
        if kind == "cold":
            text = (f"It's down to {round(value)} degrees in {where}"
                    + (" and freezing — check the water bottles and bedding." if value <= 0 else " — worth checking the bedding."))
        else:
            text = f"It's up to {round(value)} degrees in {where} — they'll want shade and fresh water."
        rt.timeline.add("animal", text, f"{'below' if kind == 'cold' else 'above'} {self.low if kind == 'cold' else self.high:g}°")
        if rt.speech.quiet_hours_now():
            morning = next_quiet_end(dt_util.now(), self.options.get(CONF_QUIET_END) or DEFAULT_QUIET_END)
            rt.followyou.keep(f"Overnight, {text[0].lower()}{text[1:]}", source="animals", not_before=morning, hours=4)
            return
        try:
            said = await rt.speech.async_say(text, "normal", None, source="animals")
            if not said.spoken and said.reason not in ("duplicate",):
                rt.followyou.keep(text, source="animals", hours=3)
        except Exception as err:  # noqa: BLE001
            _LOGGER.debug("Animal remark not said: %s", err)

    # ----- for the briefing and the look at tomorrow ---------------------------------------
    def readings(self) -> list[str]:
        out = []
        for entity_id in self.sensors:
            st = self.hass.states.get(entity_id)
            if (value := _temp(st)) is not None:
                out.append(f"{place(st)} {round(value)}°")
        return out

    def out_of_range(self) -> list[str]:
        out = []
        for entity_id in self.sensors:
            st = self.hass.states.get(entity_id)
            if self._state.get(entity_id) and (value := _temp(st)) is not None:
                out.append(f"{place(st)} {round(value)}° ({'too cold' if self._state[entity_id] == 'cold' else 'too hot'})")
        return out
