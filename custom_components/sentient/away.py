"""Away lights: when everyone's out after dark, the house looks lived in.

When every chosen person has been away for a while and the sun is down, Sentient replays how the
lights were really used at the same time a week ago (from Home Assistant's history), each light
shifted by a few minutes either way so it isn't the same every week. Only lights it switched on
itself are switched off again, and the moment anyone comes home it stops and puts back what it
turned on. Nothing is said out loud. Plain rules, no AI.
"""

from __future__ import annotations

from datetime import datetime, timedelta
import logging
import random
import re
from typing import Any, Callable

from homeassistant.const import EVENT_STATE_CHANGED, STATE_HOME
from homeassistant.core import Context, Event, HomeAssistant, callback
from homeassistant.helpers.event import async_track_time_interval
from homeassistant.helpers.storage import Store
from homeassistant.util import dt as dt_util

from .presence import area_of
from .const import CONF_AWAY_AFTER, CONF_AWAY_LIGHTS, CONF_AWAY_LIGHTS_ON, CONF_AWAY_PEOPLE, DEFAULT_AWAY_AFTER, DOMAIN

_LOGGER = logging.getLogger(__name__)
NOT_A_ROOM_LIGHT = re.compile(r"screen|led|ring|backlight|indicator|status|night ?light|all[_ ]lights|browser_mod|floodlight", re.I)

WEEK = timedelta(days=7)
JITTER_MINUTES = 12
MIN_ON = timedelta(minutes=2)  # a flick on and off isn't worth copying
MAX_ON = timedelta(hours=6)


def intervals(states: list[Any], start: datetime, end: datetime) -> list[tuple[datetime, datetime]]:
    """When a light was on between start and end, from its recorded states."""
    out: list[tuple[datetime, datetime]] = []
    on_since: datetime | None = None
    for st in sorted(states, key=lambda s: s.last_changed):
        when = max(st.last_changed, start)
        if st.state == "on" and on_since is None:
            on_since = when
        elif st.state != "on" and on_since is not None:
            out.append((on_since, when))
            on_since = None
    if on_since is not None:
        out.append((on_since, end))
    return [(a, min(b, a + MAX_ON)) for a, b in out if b - a >= MIN_ON]


class Away:
    def __init__(self, hass: HomeAssistant, entry_id: str, options: dict[str, Any], rt_getter: Callable[[], Any]) -> None:
        self.hass = hass
        self.options = options
        self._rt = rt_getter
        self.away_since: datetime | None = None
        self.active = False
        self.plan: dict[str, list[tuple[datetime, datetime]]] = {}  # light -> on-times, already moved to this week
        self._plan_for: datetime | None = None  # start of the window the plan covers
        self._applied: dict[str, bool] = {}  # what we last set each light to
        self.ours: set[str] = set()  # lights we switched on and haven't switched off
        self.context = Context()
        self.switched: dict[str, list[int]] = {}  # light -> when we switched it (epoch seconds), so learning skips them
        self._store = Store(hass, 1, f"{DOMAIN}.{entry_id}.away")
        self._unsubs: list = []

    # ----- settings ---------------------------------------------------------------------
    @property
    def enabled(self) -> bool:
        return bool(self.options.get(CONF_AWAY_LIGHTS_ON, False))

    def people(self) -> list[str]:
        return list(self.options.get(CONF_AWAY_PEOPLE) or [s.entity_id for s in self.hass.states.async_all("person")])

    def lights(self) -> list[str]:
        if chosen := self.options.get(CONF_AWAY_LIGHTS):
            return list(chosen)
        from homeassistant.helpers import entity_registry as er

        reg = er.async_get(self.hass)
        out = []
        for st in self.hass.states.async_all("light"):
            e = reg.async_get(st.entity_id)
            if e is None or e.hidden_by or e.disabled_by or e.entity_category:
                continue
            if st.attributes.get("entity_id") or NOT_A_ROOM_LIGHT.search(f"{st.entity_id} {st.name}"):
                continue  # groups ("all lights"), screens, LED rings, backlights
            if area_of(self.hass, st.entity_id) is None:
                continue
            out.append(st.entity_id)
        return out

    def movement_inside(self, now: datetime) -> str | None:
        """Someone moving about inside lately — people without a phone tracked, so not away after all."""
        rt = self._rt()
        sensors = rt.night_watch._inside_list() if rt is not None else []
        recent = timedelta(minutes=max(30.0, float(self.options.get(CONF_AWAY_AFTER, DEFAULT_AWAY_AFTER) or 0)))
        for entity_id in sensors:
            st = self.hass.states.get(entity_id)
            # on now, or changed within the window (so it was on at some point in it)
            if st is not None and st.state not in ("unavailable", "unknown") and (
                    st.state == "on" or st.last_changed >= now - recent):
                return st.name
        return None

    # ----- running ----------------------------------------------------------------------
    async def async_start(self) -> None:
        data = await self._store.async_load() or {}
        self.switched = data.get("switched", {})
        if left_on := [e for e in data.get("ours", []) if (st := self.hass.states.get(e)) and st.state == "on"]:
            self.ours = set(left_on)  # restarted mid-evening: still ours to switch off
            self.active = True
        self._unsubs += [
            self.hass.bus.async_listen(EVENT_STATE_CHANGED, self._changed),
            async_track_time_interval(self.hass, self._tick, timedelta(minutes=1)),
        ]
        self._check_people(dt_util.utcnow())

    @callback
    def async_stop(self) -> None:
        while self._unsubs:
            self._unsubs.pop()()

    def everyone_away(self) -> bool:
        people = self.people()
        states = [self.hass.states.get(p) for p in people]
        known = [s for s in states if s is not None and s.state not in ("unknown", "unavailable")]
        return bool(known) and all(s.state != STATE_HOME for s in known)

    @callback
    def _changed(self, event: Event) -> None:
        if str(event.data.get("entity_id", "")).startswith("person."):
            self._check_people(dt_util.utcnow())

    @callback
    def _check_people(self, now: datetime) -> None:
        if self.everyone_away():
            self.away_since = self.away_since or now
        else:
            self.away_since = None
            if self.active:
                self.hass.async_create_task(self.async_finish("someone's home"), eager_start=False)

    def dark(self) -> bool:
        sun = self.hass.states.get("sun.sun")
        return sun is None or sun.state == "below_horizon"

    def due(self, now: datetime) -> bool:
        minutes = float(self.options.get(CONF_AWAY_AFTER, DEFAULT_AWAY_AFTER) or 0)
        return (self.enabled and self.away_since is not None and now - self.away_since >= timedelta(minutes=minutes)
                and self.dark() and self.movement_inside(now) is None)

    @callback
    def _tick(self, now: datetime) -> None:
        self.hass.async_create_task(self.async_step(dt_util.utcnow()), eager_start=False)

    async def async_step(self, now: datetime) -> None:
        if not self.due(now):
            if self.active:
                why = ("someone's home" if self.away_since is None else "someone's moving about inside"
                       if self.movement_inside(now) else "it's light")
                await self.async_finish(why)
            return
        if not self.active:
            self.active = True
            self._timeline("Away lights on: replaying last week's evening so the house looks lived in")
        if self._plan_for is None or now - self._plan_for > timedelta(hours=12):
            await self.async_plan(now)
        for light, times in self.plan.items():
            want = any(a <= now < b for a, b in times)
            if self._applied.get(light) == want:
                continue
            self._applied[light] = want
            st = self.hass.states.get(light)
            if st is None or st.state in ("unavailable", "unknown"):
                continue
            if want and st.state == "off":
                await self._switch(light, True)
                self.ours.add(light)
            elif not want and light in self.ours:
                self.ours.discard(light)
                if st.state == "on":
                    await self._switch(light, False)

    async def async_plan(self, now: datetime) -> None:
        """Last week's on-times for the next 24 hours, each light nudged by a few minutes."""
        start, end = now - WEEK - timedelta(hours=1), now - WEEK + timedelta(hours=24)
        lights = self.lights()
        history = await self._history(start, end, lights)
        plan = {}
        for light in lights:
            shift = timedelta(minutes=random.uniform(-JITTER_MINUTES, JITTER_MINUTES))
            times = [(a + WEEK + shift, b + WEEK + shift) for a, b in intervals(history.get(light, []), start, end)]
            if times:
                plan[light] = times
        self.plan, self._plan_for = plan, now

    async def _history(self, start: datetime, end: datetime, lights: list[str]) -> dict[str, list[Any]]:
        if not lights or "recorder" not in self.hass.config.components:
            return {}
        try:
            from homeassistant.components.recorder import get_instance, history

            return await get_instance(self.hass).async_add_executor_job(
                lambda: history.get_significant_states(
                    self.hass, start, end, lights, significant_changes_only=False, no_attributes=True,
                    include_start_time_state=True,
                )
            )
        except Exception as err:  # noqa: BLE001
            _LOGGER.debug("Away lights: no history: %s", err)
            return {}

    async def async_finish(self, why: str) -> None:
        if not self.active:
            return
        self.active = False
        for light in list(self.ours):
            st = self.hass.states.get(light)
            if st is not None and st.state == "on":
                await self._switch(light, False)
        self.ours.clear()
        self._save()
        self._applied.clear()
        self.plan, self._plan_for = {}, None
        self._timeline(f"Away lights off — {why}")

    def times(self) -> dict[str, set[int]]:
        return {e: set(t) for e, t in self.switched.items()}

    def _save(self) -> None:
        cutoff = int(dt_util.utcnow().timestamp()) - 15 * 86400
        self.switched = {e: [t for t in ts if t > cutoff] for e, ts in self.switched.items() if any(t > cutoff for t in ts)}
        self._store.async_delay_save(lambda: {"switched": self.switched, "ours": sorted(self.ours)}, 5)

    async def _switch(self, light: str, on: bool) -> None:
        self.switched.setdefault(light, []).append(int(dt_util.utcnow().timestamp()))
        try:
            await self.hass.services.async_call(
                "light", "turn_on" if on else "turn_off", {"entity_id": light}, blocking=True,
                context=Context(parent_id=self.context.id),  # the house did this, not a person
            )
        except Exception as err:  # noqa: BLE001
            _LOGGER.debug("Away lights couldn't switch %s: %s", light, err)
        self.switched[light].append(int(dt_util.utcnow().timestamp()))
        self._save()

    def _timeline(self, text: str) -> None:
        rt = self._rt()
        if rt is not None:
            rt.timeline.add("mode", text)

    def describe(self) -> dict[str, Any]:
        return {"on": self.enabled, "running": self.active, "lights_on_now": sorted(self.ours),
                "planned_lights": len(self.plan)}
