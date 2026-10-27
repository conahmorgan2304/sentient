"""Camera security: people on the cameras, followed from one to the next.

From the cameras' own person sensors (Frigate's "person occupancy", for example) — no AI unless
you ask for a description:

* journeys: detections on different cameras a few minutes apart are one person's route
  ("gate → garden → shed"), logged on the timeline;
* over the fence: someone appears inside the boundary (the garden) when it was empty and none
  of the ways in (the gate, the back door) opened — they didn't come through them;
* at night: anyone on a camera in quiet hours with nobody up inside;
* loitering: someone staying on a camera for minutes on end in quiet hours.

Those three are alerts: on the timeline, to phones, and — in quiet hours, if night watch says
alerts out loud — on the night-watch speakers. Optionally one AI look describes who it is.
"""

from __future__ import annotations

from datetime import datetime, timedelta
import logging
from typing import Any, Callable

from homeassistant.const import EVENT_STATE_CHANGED
from homeassistant.core import Event, HomeAssistant, callback
from homeassistant.helpers import entity_registry as er
from homeassistant.helpers.event import async_call_later
from homeassistant.util import dt as dt_util

from .ai import async_generate
from .const import (
    CONF_AI_TASK_ENTITY,
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
    """The camera a person sensor belongs to: same device, else the same name."""
    reg = er.async_get(hass)
    entry = reg.async_get(sensor)
    if entry and entry.device_id:
        for e in er.async_entries_for_device(reg, entry.device_id):
            if e.domain == "camera":
                return e.entity_id
    st = hass.states.get(sensor)
    first = ((st.name if st else sensor.split(".", 1)[1]) or "").lower().split(" ")[0]
    for cam in hass.states.async_all("camera"):
        if cam.entity_id.split(".", 1)[1].startswith(first) or (cam.name or "").lower().startswith(first):
            return cam.entity_id
    return None


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
        self._last_seen: dict[str, datetime] = {}  # sensor -> last time it saw someone
        self._loiter_timers: dict[str, Any] = {}
        self._last_alert: dict[str, datetime] = {}
        self.journeys: list[dict[str, Any]] = []
        self._last_route: tuple[str, datetime] | None = None
        self._unsub = None

    @property
    def sensors(self) -> list[str]:
        return list(self.options.get(CONF_CAMSEC_SENSORS) or [])

    @callback
    def async_start(self) -> None:
        self._unsub = self.hass.bus.async_listen(EVENT_STATE_CHANGED, self._changed)

    @callback
    def async_stop(self) -> None:
        if self._unsub:
            self._unsub()
            self._unsub = None
        for t in list(self._loiter_timers.values()):
            t()
        self._loiter_timers.clear()
        if self._journey_timer:
            self._journey_timer()
            self._journey_timer = None

    @callback
    def _changed(self, event: Event) -> None:
        if not self.options.get(CONF_CAMSEC):
            return
        entity_id = event.data.get("entity_id")
        if entity_id not in self.sensors:
            return
        new, old = event.data.get("new_state"), event.data.get("old_state")
        if new is None or old is None or old.state in ("unavailable", "unknown"):
            return
        if new.state == "on" and old.state != "on":
            self._seen(entity_id, new.last_changed)
        elif new.state != "on" and (t := self._loiter_timers.pop(entity_id, None)):
            t()

    # ----- someone seen ----------------------------------------------------------------
    @callback
    def _seen(self, sensor: str, when: datetime) -> None:
        rt = self._rt()
        inside = set(self.options.get(CONF_CAMSEC_INSIDE) or [])
        previous_inside = max((self._last_seen.get(s) for s in inside if s in self._last_seen), default=None)
        self._last_seen[sensor] = when
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
            self._end_journey()

        self._journey_timer = async_call_later(self.hass, JOURNEY_GAP.total_seconds(), _done)
        # loitering, in quiet hours
        if rt.speech.quiet_hours_now() and sensor not in self._loiter_timers:
            @callback
            def _still(_now: datetime, s: str = sensor) -> None:
                self._loiter_timers.pop(s, None)
                st = self.hass.states.get(s)
                if st is not None and st.state == "on" and rt.night_watch.someone_inside(dt_util.utcnow()) is None:
                    self._alert(s, f"Someone has been by the {place(self.hass, s).lower()} for a few minutes", "loitering")

            self._loiter_timers[sensor] = async_call_later(self.hass, LOITER.total_seconds(), _still)
        # over the fence
        if sensor in inside:
            empty = previous_inside is None or when - previous_inside >= EMPTY_BEFORE
            entries = [e for e in self.options.get(CONF_CAMSEC_ENTRIES) or [] if (st := self.hass.states.get(e))
                       and (st.state == "on" or when - st.last_changed <= ENTRY_WINDOW)]
            if empty and not entries:
                self._alert(sensor, f"Someone is in the {place(self.hass, sensor).lower()}, but the gate and doors didn't open", "fence")
                return
        # anyone at night with the house asleep
        if rt.speech.quiet_hours_now() and rt.night_watch.someone_inside(when) is None:
            self._alert(sensor, f"Someone is by the {place(self.hass, sensor).lower()} and nobody's up", "night")

    @callback
    def _end_journey(self) -> None:
        if self._journey_timer:
            self._journey_timer()
            self._journey_timer = None
        journey, self._journey = self._journey, []
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
        mins = max(1, round((journey[-1][0] - journey[0][0]).total_seconds() / 60))
        start = dt_util.as_local(journey[0][0])
        self.journeys = [{"time": journey[0][0].isoformat(), "route": route}, *self.journeys][:30]
        rt.timeline.add("noticed", f"Someone walked {route}", f"{start:%H:%M}, over {mins} minute{'s' if mins != 1 else ''}")

    # ----- alerting ------------------------------------------------------------------------
    @callback
    def _alert(self, sensor: str, text: str, why: str) -> None:
        now = dt_util.utcnow()
        key = f"{why}:{sensor}"
        if (last := self._last_alert.get(key)) and now - last < REPEAT:
            return
        self._last_alert[key] = now
        self.hass.async_create_task(self._async_alert(sensor, text))

    async def _async_alert(self, sensor: str, text: str) -> None:
        rt = self._rt()
        local = dt_util.as_local(dt_util.utcnow())
        detail = None
        camera = camera_for(self.hass, sensor)
        if self.options.get(CONF_CAMSEC_DESCRIBE) and camera and rt.watches.spend("security"):
            seen, detail = await self._describe(camera)
            if seen is False:
                # the AI looked and there's nobody there: a false alarm (a shadow, a cat, a coat on the line)
                rt.timeline.add("noticed", f"{text} — but nobody was in the picture ({local:%H:%M})",
                                "camera security: probably a false alarm")
                return
        rt.timeline.add("security", f"{text} ({local:%H:%M})." + (f" {detail}" if detail else ""), "camera security", alert=True)
        if rt.speech.quiet_hours_now() and self.options.get(CONF_NIGHT_WATCH_ANNOUNCE):
            speakers = [s for s in self.options.get(CONF_NIGHT_WATCH_SPEAKERS) or [] if self.hass.states.get(s)] or None
            await rt.speech.async_say(f"Heads up: {text[0].lower()}{text[1:]}." + (f" {detail}" if detail else ""),
                                      "urgent", speakers, source="camera security")

    async def _describe(self, camera: str) -> tuple[bool | None, str | None]:
        """(is anyone actually there, one sentence on them). (None, None) if the AI couldn't look."""
        data: dict[str, Any] = {
            "task_name": "Sentient: who's there",
            "instructions": "A home security camera thinks it just saw a person. Is there really a person in this "
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
        if ai := self.options.get(CONF_AI_TASK_ENTITY):
            data["entity_id"] = ai
        try:
            response = await async_generate(self.hass, data)
        except Exception as err:  # noqa: BLE001
            _LOGGER.debug("No description: %s", err)
            return None, None
        result = (response or {}).get("data")
        if not isinstance(result, dict):
            text = str(result or "").strip()
            return None, text or None
        person = result.get("person")
        seen = None if person is None else person in (True, "true", "yes", "True")
        text = " ".join(str(result.get("description") or "").split()).strip()
        return seen, (text if seen is not False and text else None)

    def describe(self) -> dict[str, Any]:
        return {
            "on": bool(self.options.get(CONF_CAMSEC)),
            "cameras": [place(self.hass, s) for s in self.sensors],
            "inside_the_boundary": [place(self.hass, s) for s in self.options.get(CONF_CAMSEC_INSIDE) or []],
            "recent_journeys": self.journeys[:8],
        }
