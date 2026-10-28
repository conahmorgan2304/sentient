"""The screen: what a wall tablet shows, decided by the house. No AI.

`custom:sentient-screen-card` draws it; this works out what's on it:

* **Alerts and notices** — whatever the house says out loud (the doorbell, the gate, a door left open,
  the washing finished, the hutch getting cold…) also appears as a card, with the right camera when
  there is one. Safety alerts stay until dismissed; everything else for ten minutes.
* **Morning** — when the house wakes up (or someone's first about after quiet hours), a morning card:
  the weather, today's reminders and calendar, anything open, anything wrong, an overnight thought.
  Up until late morning or until dismissed.
* **Idle** — the rest of the time: the house at a glance.

Optionally, "screen actions" run when something new appears (wake the tablet, switch it to the
page with this card) and "clear actions" when it's all gone (back to normal).
"""

from __future__ import annotations

from datetime import datetime, timedelta
import logging
from typing import Any, Callable
import uuid

from homeassistant.core import Event, HomeAssistant, callback
from homeassistant.helpers.event import async_track_time_interval
from homeassistant.util import dt as dt_util

from .const import (
    CONF_DOOR_CAMERA,
    CONF_SCREEN,
    CONF_SCREEN_ACTIONS,
    CONF_SCREEN_CLEAR_ACTIONS,
    DOMAIN,
    EVENT_SPOKE,
)

_LOGGER = logging.getLogger(__name__)

EVENT_SCREEN = f"{DOMAIN}_screen"
NOTICE_FOR = timedelta(minutes=10)
MORNING_UNTIL_HOUR = 11
# what's said that's worth showing; chatter (remarks, thoughts, briefings) stays spoken only
SHOWN = ("visitors", "announce:", "critical", "camera security", "night_watch", "left_open", "appliance",
         "animals", "check:", "watch:", "reminder:", "alarm:", "followthrough")
ALERTS = ("critical", "camera security", "night_watch")


class Screen:
    def __init__(self, hass: HomeAssistant, options: dict[str, Any], rt_getter: Callable[[], Any]) -> None:
        self.hass = hass
        self.options = options
        self._rt = rt_getter
        self.cards: list[dict[str, Any]] = []
        self.morning: dict[str, Any] | None = None
        self._morning_day: str | None = None
        self._unsubs: list = []

    async def async_start(self) -> None:
        from .sleep import EVENT_SLEEP

        self._unsubs += [
            self.hass.bus.async_listen(EVENT_SPOKE, self._spoke),
            self.hass.bus.async_listen(EVENT_SLEEP, self._sleep),
            async_track_time_interval(self.hass, self._tick, timedelta(minutes=1)),
        ]

    @callback
    def async_stop(self) -> None:
        while self._unsubs:
            self._unsubs.pop()()

    @property
    def enabled(self) -> bool:
        return bool(self.options.get(CONF_SCREEN, True))

    # ----- what goes up ------------------------------------------------------------------
    def _camera(self, source: str) -> str | None:
        rt = self._rt()
        if source == "visitors":
            return self.options.get(CONF_DOOR_CAMERA)
        kind, _, name = source.partition(":")
        if rt is None or not name:
            return None
        lists = {"announce": "announce", "check": "checks"}
        if kind in lists and (item := rt.lists[lists[kind]].find(name)):
            if kind == "check":
                return item.get("camera")
            from .camsecurity import camera_for

            return camera_for(self.hass, item["entity"]) if item.get("entity") else None
        if kind == "watch":
            for w in rt.watches.watches:
                if w.what == name and str(w.entity_id or "").startswith("camera."):
                    return w.entity_id
        return None

    @callback
    def _spoke(self, event: Event) -> None:
        source = str(event.data.get("source") or "")
        if not self.enabled or not source.startswith(SHOWN):
            return
        self.show(str(event.data.get("message") or ""), source, alert=source.startswith(ALERTS)
                  or event.data.get("priority") == "urgent")

    @callback
    def show(self, text: str, source: str = "", alert: bool = False, camera: str | None = None,
             minutes: float | None = None) -> dict[str, Any]:
        if not self.enabled:
            return {"shown": False, "reason": "the tablet screen is turned off in Sentient's settings"}
        now = dt_util.utcnow()
        camera = camera or self._camera(source)
        if camera and self.hass.states.get(camera) is None:
            camera = None
        card = {
            "id": uuid.uuid4().hex[:8], "kind": "alert" if alert else "notice", "text": text, "source": source,
            "camera": camera, "time": now.isoformat(),
            "until": None if source == "critical" else (now + (timedelta(minutes=minutes) if minutes else NOTICE_FOR)).isoformat(),
        }
        # the same thing said again replaces the old card
        self.cards = [c for c in self.cards if c["text"] != text][:5]
        self.cards.insert(0, card)
        self._changed(new=True)
        return card

    @callback
    def dismiss(self, card_id: str | None = None) -> None:
        if card_id in (None, "", "all"):
            self.cards, self.morning = [], None
        elif card_id == "morning":
            self.morning = None
        else:
            self.cards = [c for c in self.cards if c["id"] != card_id]
        self._changed()

    # ----- morning -----------------------------------------------------------------------
    @callback
    def _sleep(self, event: Event) -> None:
        if not event.data.get("asleep") and self.enabled:
            self.hass.async_create_task(self.async_morning(), eager_start=False)

    async def async_morning(self) -> dict[str, Any] | None:
        rt = self._rt()
        if rt is None or not self.enabled:
            return None
        local = dt_util.now()
        day = local.date().isoformat()
        if self._morning_day == day and self.morning is not None:
            return self.morning
        from .upkeep import gather_briefing

        try:
            facts = await gather_briefing(self.hass, rt)
        except Exception as err:  # noqa: BLE001
            _LOGGER.debug("Morning card skipped: %s", err)
            return None
        keep = ("weather", "reminders_today", "calendar_next_24h", "to_do", "open_now", "overnight_thoughts", "visitors_today",
                "unusual_overnight", "animals_outside", "security")
        self.morning = {k: v for k, v in facts.items() if k in keep and v}
        problems = (facts.get("system") or {}).get("problems")
        if problems:
            self.morning["problems"] = problems[:4]
        self.morning["day"] = local.strftime("%A %d %B")
        self._morning_day = day
        self._changed(new=True)
        return self.morning

    # ----- housekeeping ------------------------------------------------------------------
    @callback
    def _tick(self, now: datetime) -> None:
        utc = dt_util.utcnow()
        before = len(self.cards)
        self.cards = [c for c in self.cards if not c["until"] or dt_util.parse_datetime(c["until"]) > utc]
        gone = len(self.cards) != before
        if self.morning is not None and dt_util.now().hour >= MORNING_UNTIL_HOUR:
            self.morning, gone = None, True
        if gone:
            self._changed()

    def mode(self) -> str:
        if not self.enabled:
            return "idle"
        if any(c["kind"] == "alert" for c in self.cards):
            return "alert"
        if self.cards:
            return "notice"
        if self.morning:
            return "morning"
        return "idle"

    def coming_up(self, limit: int = 3) -> list[dict[str, Any]]:
        """The next few things in the next 24 hours: alarms (bin night and the like) and reminders."""
        rt = self._rt()
        if rt is None:
            return []
        now = dt_util.utcnow()
        soon = now + timedelta(hours=24)
        out = []
        for a in rt.alarms.listing():
            at = dt_util.parse_datetime(a["next"] or "")
            if a["on"] and not a["silent"] and at and now < at <= soon:
                out.append({"what": a["name"], "at": dt_util.as_utc(at).isoformat(), "kind": "alarm"})
        for w in rt.watches.watches:
            if w.kind == "reminder" and now < w.until_dt <= soon:
                out.append({"what": w.what, "at": w.until_dt.isoformat(), "kind": "reminder"})
        return sorted(out, key=lambda i: i["at"])[:limit]

    def describe(self) -> dict[str, Any]:
        return {"mode": self.mode(), "cards": self.cards, "morning": self.morning, "coming_up": self.coming_up()}

    @callback
    def _changed(self, new: bool = False) -> None:
        self.hass.bus.async_fire(EVENT_SCREEN, {"mode": self.mode(), "new": new})
        actions = self.options.get(CONF_SCREEN_ACTIONS if new else CONF_SCREEN_CLEAR_ACTIONS) or []
        if actions and self.enabled and (new or self.mode() == "idle"):
            self.hass.async_create_task(self._async_actions(actions, new), eager_start=False)

    async def _async_actions(self, actions: list, new: bool) -> None:
        from .shortcuts import async_run_actions

        try:
            await async_run_actions(self.hass, actions, "screen" if new else "screen cleared")
        except Exception as err:  # noqa: BLE001
            _LOGGER.warning("Sentient screen actions failed: %s", err)
