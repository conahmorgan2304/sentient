"""Camera security: people on the cameras, followed from one to the next.

From the cameras' own person sensors (Frigate's "person occupancy", for example) — no AI unless
you ask for a description:

* journeys: detections close in time form a possible route, not proof of one person's journey;
* boundary: a person is reported inside after a known quiet period, with no recent entry opening recorded;
* at night: a person reported in quiet hours with no recent indoor activity detected;
* loitering: someone staying on a camera for minutes on end in quiet hours.

Those three are alerts: on the timeline, to phones, and — in quiet hours, if night watch says
alerts out loud — on the night-watch speakers. Optionally one AI look describes who it is.
"""

from __future__ import annotations

import asyncio
from datetime import datetime, timedelta
import logging
import re
from typing import Any, Callable

from homeassistant.const import EVENT_STATE_CHANGED
from homeassistant.exceptions import HomeAssistantError
from homeassistant.core import Event, HomeAssistant, callback
from homeassistant.helpers import entity_registry as er
from homeassistant.helpers.event import async_call_later
from homeassistant.util import dt as dt_util

from .ai import async_generate
from .const import (
    CONF_AI_TASK_ENTITY,
    CONF_SMART_AI,
    CONF_CAMSEC,
    CONF_CAMSEC_DESCRIBE,
    CONF_CAMSEC_ENTRIES,
    CONF_CAMSEC_INSIDE,
    CONF_CAMSEC_SENSORS,
    CONF_NIGHT_WATCH_ANNOUNCE,
    CONF_NIGHT_WATCH_SPEAKERS,
)

_LOGGER = logging.getLogger(__name__)

JOURNEY_GAP = timedelta(minutes=3)  # detections closer than this are one journey
EMPTY_BEFORE = timedelta(minutes=10)  # the garden must have been empty this long…
ENTRY_WINDOW = timedelta(minutes=5)  # …and no way in used in this long, for "over the fence"
LOITER = timedelta(minutes=4)
REPEAT = timedelta(minutes=10)


def camera_for(hass: HomeAssistant, sensor: str) -> str | None:
    """Use a unique device/name match; an ambiguous view cannot check this sensor."""
    def normal(value: str) -> str:
        return " ".join(re.sub(r"[_\W]+", " ", value.casefold()).split())

    source = sensor.split(".", 1)[-1]
    source = re.sub(r"_(?:person_occupancy|occupancy|person)$", "", source)
    names = {normal(source), normal(place(hass, sensor))} - {""}

    def unique(candidates: list[str]) -> str | None:
        matched = [eid for eid in candidates if normal(eid.split(".", 1)[-1]) in names
                   or ((st := hass.states.get(eid)) is not None and normal(st.name) in names)]
        return matched[0] if len(matched) == 1 else None

    reg = er.async_get(hass)
    entry = reg.async_get(sensor)
    if entry and entry.device_id:
        candidates = [e.entity_id for e in er.async_entries_for_device(reg, entry.device_id)
                      if e.domain == "camera" and not e.disabled_by]
        if candidates:
            return candidates[0] if len(candidates) == 1 else unique(candidates)
    return unique([cam.entity_id for cam in hass.states.async_all("camera")])


def place(hass: HomeAssistant, sensor: str) -> str:
    st = hass.states.get(sensor)
    name = (st.name if st else sensor) or sensor
    for tail in (" person occupancy", " occupancy", " person"):
        if name.lower().endswith(tail):
            name = name[: -len(tail)]
    return name.strip() or sensor


class CameraSecurity:
    def __init__(self, hass: HomeAssistant, options: dict[str, Any], runtime: Callable[[], Any]) -> None:
        self.hass = hass
        self.options = options
        self._rt = runtime
        self._journey: list[tuple[datetime, str]] = []
        self._journey_timer = None
        self._loiter_timers: dict[str, Any] = {}
        self._last_alert: dict[str, datetime] = {}
        self._pending_alert_keys: dict[str, tuple] = {}
        self._journey_stamp: tuple | None = None
        self.journeys: list[dict[str, Any]] = []
        self._last_route: tuple[str, datetime] | None = None
        self._unsub = None
        self._stopped = False
        self._generation = 0
        self._tasks: set[asyncio.Task] = set()

    def _stamp(self) -> tuple:
        rt = self._rt()
        speech = getattr(rt, "speech", None)
        return (self._generation, id(self.options), getattr(speech, "_privacy_generation", 0),
                getattr(speech, "_guest_generation", 0), getattr(speech, "guest_mode", False),
                getattr(getattr(rt, "ai", None), "_generation", 0))

    def _valid(self, stamp: tuple, sensor: str | None = None) -> bool:
        return (not self._stopped and bool(self.options.get(CONF_CAMSEC)) and stamp == self._stamp()
                and (sensor is None or sensor in self.sensors))

    @callback
    def clear(self) -> None:
        """Invalidate queued work and transient observations without publishing them."""
        self._generation += 1
        for task in list(self._tasks):
            task.cancel()
        for timer in self._loiter_timers.values():
            timer()
        self._loiter_timers.clear()
        if self._journey_timer:
            self._journey_timer()
            self._journey_timer = None
        self._journey.clear()
        self.journeys.clear()
        self._last_route = None
        self._last_alert.clear()
        self._pending_alert_keys.clear()

    @property
    def sensors(self) -> list[str]:
        return list(self.options.get(CONF_CAMSEC_SENSORS) or [])

    @callback
    def async_start(self) -> None:
        self._stopped = False
        self._unsub = self.hass.bus.async_listen(EVENT_STATE_CHANGED, self._changed)

    @callback
    def async_stop(self) -> None:
        self._stopped = True
        self.clear()
        if self._unsub:
            self._unsub()
            self._unsub = None

    @callback
    def _changed(self, event: Event) -> None:
        if not self._valid(self._stamp()):
            return
        entity_id = event.data.get("entity_id")
        if entity_id not in self.sensors:
            return
        new, old = event.data.get("new_state"), event.data.get("old_state")
        if new is None or old is None or old.state in ("unavailable", "unknown"):
            return
        if new.state == "on" and old.state != "on":
            inside = self.options.get(CONF_CAMSEC_INSIDE) or []
            readings = [old if s == entity_id else self.hass.states.get(s) for s in inside]
            empty = bool(readings) and all(st is not None and st.state == "off"
                and new.last_changed - st.last_changed >= EMPTY_BEFORE for st in readings)
            self._seen(entity_id, new.last_changed, empty)
        elif new.state != "on" and (t := self._loiter_timers.pop(entity_id, None)):
            t()

    # ----- someone seen ----------------------------------------------------------------
    @callback
    def _seen(self, sensor: str, when: datetime, was_empty: bool = False) -> None:
        stamp = self._stamp()
        if not self._valid(stamp, sensor):
            return
        rt = self._rt()
        inside = set(self.options.get(CONF_CAMSEC_INSIDE) or [])
        if self._journey_stamp != stamp:
            self._journey.clear()
            self._journey_stamp = stamp
        # journey
        if self._journey and when - self._journey[-1][0] > JOURNEY_GAP:
            self._end_journey()
        if not self._journey or self._journey[-1][1] != sensor:
            self._journey.append((when, sensor))
        if self._journey_timer:
            self._journey_timer()
        @callback
        def _done(_now: datetime) -> None:
            self._journey_timer = None
            if self._valid(stamp):
                self._end_journey()
            else:
                self._journey.clear()

        self._journey_timer = async_call_later(self.hass, JOURNEY_GAP.total_seconds(), _done)
        # loitering, in quiet hours
        if rt.speech.quiet_hours_now() and sensor not in self._loiter_timers:
            @callback
            def _still(_now: datetime, s: str = sensor) -> None:
                self._loiter_timers.pop(s, None)
                st = self.hass.states.get(s)
                if (self._valid(stamp, s) and rt.speech.quiet_hours_now() and st is not None
                        and st.state == "on" and rt.night_watch.someone_inside(dt_util.utcnow()) is None):
                    self._alert(s, f"A person has been reported by the {place(self.hass, s).lower()} for a few minutes", "loitering")

            self._loiter_timers[sensor] = async_call_later(self.hass, LOITER.total_seconds(), _still)
        # over the fence
        if sensor in inside:
            entries = [self.hass.states.get(e) for e in self.options.get(CONF_CAMSEC_ENTRIES) or []]
            no_entry = bool(entries) and all(st is not None and st.state == "off"
                and when - st.last_changed > ENTRY_WINDOW for st in entries)
            if was_empty and no_entry:
                message = f"A person was reported in the {place(self.hass, sensor).lower()}, with no recent entry opening recorded"
                self._alert(sensor, message, "fence")
                return
        # anyone at night with the house asleep
        if rt.speech.quiet_hours_now() and rt.night_watch.someone_inside(when) is None:
            self._alert(sensor, f"A person was reported by the {place(self.hass, sensor).lower()}, with no recent indoor activity detected", "night")

    @callback
    def _end_journey(self) -> None:
        if self._journey_timer:
            self._journey_timer()
            self._journey_timer = None
        journey, self._journey = self._journey, []
        if not self._valid(self._stamp()):
            return
        stops: list[str] = []  # each place once, in the order first reached
        for _, s in journey:
            if s not in stops:
                stops.append(s)
        if len(stops) < 2:
            return
        inside = set(self.options.get(CONF_CAMSEC_INSIDE) or [])
        rt = self._rt()
        if not rt.speech.quiet_hours_now():
            # in the day, people passing outside or pottering in the garden aren't news: only someone
            # coming in from outside to inside the boundary is worth a line
            if not inside or stops[0] in inside or not any(s in inside for s in stops):
                return
        names = [place(self.hass, s) for s in stops]
        route = " → ".join(names[:6]) + (" → …" if len(names) > 6 else "")
        now = dt_util.utcnow()
        if (last := self._last_route) and last[0] == route and now - last[1] < timedelta(minutes=20):
            return  # the same trip again: once is enough
        self._last_route = (route, now)
        self.journeys = [{"time": journey[0][0].isoformat(), "route": route}, *self.journeys][:30]
        rt.timeline.add("noticed", f"Possible route: {route}", "Detections close in time, not proof of one person", evidence="inferred")

    # ----- alerting ------------------------------------------------------------------------
    @callback
    def _alert(self, sensor: str, text: str, why: str) -> None:
        stamp = self._stamp()
        if not self._valid(stamp, sensor):
            return
        incidents = getattr(self._rt(), "incidents", None)
        incident_id = None
        if incidents is not None and incidents.enabled:
            should_alert, incident_id = incidents.alert(sensor, text, why)
            if not should_alert:
                return
        else:
            now = dt_util.utcnow()
            key = f"{why}:{sensor}"
            if key in self._pending_alert_keys or ((last := self._last_alert.get(key)) and now - last < REPEAT):
                return
            self._pending_alert_keys[key] = stamp
        started = False

        async def deliver() -> None:
            nonlocal started
            started = True
            await self._async_alert(sensor, text, incident_id, why, stamp)

        def done(task: asyncio.Task) -> None:
            self._tasks.discard(task)
            if not started:
                # A task cancelled before its first turn cannot run its own finally block.
                if incident_id is not None:
                    from .incidents import ALERT_LEVELS
                    incidents.finish_alert(incident_id, ALERT_LEVELS.get(why, 1), False)
                else:
                    key = f"{why}:{sensor}"
                    if self._pending_alert_keys.get(key) == stamp:
                        self._pending_alert_keys.pop(key, None)

        task = self.hass.async_create_task(deliver(), eager_start=False)
        self._tasks.add(task)
        task.add_done_callback(done)

    async def _async_alert(self, sensor: str, text: str, incident_id: str | None = None, reason: str = "night",
                           stamp: tuple | None = None) -> None:
        stamp = self._stamp() if stamp is None else stamp
        rt = self._rt()
        from .incidents import ALERT_LEVELS
        incidents = getattr(rt, "incidents", None)
        accepted = False
        try:
            if not self._valid(stamp, sensor):
                return
            local = dt_util.as_local(dt_util.utcnow())
            detail = None
            camera = camera_for(self.hass, sensor)
            if self.options.get(CONF_CAMSEC_DESCRIBE) and camera and rt.watches.spend("security"):
                seen, detail = await self._describe(camera, lambda: self._valid(stamp, sensor))
                if seen is False:
                    detail = "No person was visible in the later snapshot; the earlier sensor detection is unverified."
            if not self._valid(stamp, sensor):
                return
            if camera and getattr(rt, "clips", None) is not None and rt.clips.enabled:
                self.hass.async_create_background_task(rt.clips.async_after_alert(camera, text, rt.clips._stamp()), "sentient_clip")
            rt.timeline.add("security", f"{text} ({local:%H:%M})." + (f" {detail}" if detail else ""), "camera security", alert=True, incident_id=incident_id, evidence="inferred")
            accepted = True
            if rt.speech.quiet_hours_now() and self.options.get(CONF_NIGHT_WATCH_ANNOUNCE):
                speakers = [s for s in self.options.get(CONF_NIGHT_WATCH_SPEAKERS) or [] if self.hass.states.get(s)] or None
                await rt.speech.async_say(f"Heads up: {text[0].lower()}{text[1:]}." + (f" {detail}" if detail else ""),
                                          "urgent", speakers, source="camera security")
        finally:
            if incident_id is not None and incidents is not None:
                incidents.finish_alert(incident_id, ALERT_LEVELS.get(reason, 1), accepted and self._valid(stamp, sensor))
            elif incident_id is None:
                key = f"{reason}:{sensor}"
                if self._pending_alert_keys.get(key) == stamp:
                    self._pending_alert_keys.pop(key, None)
                if accepted and self._valid(stamp, sensor):
                    self._last_alert[key] = dt_util.utcnow()

    async def _describe(self, camera: str, current: Callable[[], bool] | None = None) -> tuple[bool | None, str | None]:
        """(is anyone actually there, one sentence on them). (None, None) if the AI couldn't look."""
        from .camwake import async_wake

        try:
            await async_wake(self.hass, self.options, camera)
        except HomeAssistantError:
            return None, None  # retain the sensor alert; an unchecked image cannot dismiss it
        if current is not None and not current():
            return None, None
        data: dict[str, Any] = {
            "task_name": "Sentient: who's there",
            "instructions": "A home security sensor reported a person earlier. This is a later snapshot, which cannot disprove that detection. Is a person visible in this "
                            "picture? If so, in one short sentence describe them and what they're doing (e.g. 'A man "
                            "in a dark hoodie is trying the shed door'). No guesses at names.",
            "structure": {
                "person": {"description": "Is a person actually visible?", "required": True,
                           "selector": {"boolean": {}}},
                "description": {"description": "One short sentence on them; empty if nobody", "required": False,
                                "selector": {"text": {}}},
            },
            "attachments": [{"media_content_id": f"media-source://camera/{camera}", "media_content_type": "image/jpeg"}],
        }
        if ai := self.options.get(CONF_SMART_AI) or self.options.get(CONF_AI_TASK_ENTITY):
            data["entity_id"] = ai
        try:
            response = await async_generate(self.hass, data)
        except Exception as err:  # noqa: BLE001
            _LOGGER.debug("No description: %s", err)
            return None, None
        result = (response or {}).get("data")
        if not isinstance(result, dict):
            return None, None
        person = result.get("person")
        seen = person if type(person) is bool else None
        description = result.get("description")
        text = " ".join(description.split())[:600] if isinstance(description, str) else ""
        return seen, (text if seen is True and text else None)

    def describe(self) -> dict[str, Any]:
        return {
            "on": bool(self.options.get(CONF_CAMSEC)),
            "cameras": [place(self.hass, s) for s in self.sensors],
            "inside_the_boundary": [place(self.hass, s) for s in self.options.get(CONF_CAMSEC_INSIDE) or []],
            "recent_journeys": self.journeys[:8],
        }
