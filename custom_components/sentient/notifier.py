"""Phone notifications: the important things also go to people's phones.

Driven by the timeline, so anything the house says, holds back, sees at the door or
finds wrong can be mirrored — each kind switched on separately. Uses notify entities
(e.g. the companion app), so it works with whatever the house already has.
"""

from __future__ import annotations

from datetime import datetime, timedelta
import logging
import re
import uuid
from typing import Any

from homeassistant.core import Event, HomeAssistant, callback
from homeassistant.util import dt as dt_util

from .const import (
    CONF_DOOR_CAMERA,
    CONF_NOTIFY_TARGETS,
    CONF_NOTIFY_WHEN,
    CONF_PERSONA,
    CONF_PHONE_APPS,
    CONF_PHONE_BUTTONS,
    DEFAULT_NOTIFY_WHEN,
    DEFAULT_PERSONA,
)
from .timeline import EVENT_TIMELINE

_LOGGER = logging.getLogger(__name__)
_REPEAT = timedelta(minutes=10)
ACTION = "SENTIENT"  # prefix of this integration's notification buttons
SNOOZE = timedelta(minutes=10)


def wanted(item: dict[str, Any], kinds: set[str]) -> str | None:
    """The notification title for a timeline item, or None if it shouldn't be sent."""
    kind = item.get("kind")
    if kind == "critical" and "critical" in kinds:
        return "ALERT"
    if kind == "security" and "security" in kinds:
        return "Night watch" if item.get("alert") else "Left open"
    if kind == "said" and "urgent" in kinds and item.get("priority") == "urgent" and item.get("source") != "critical":
        return "Announcement"
    if kind == "held":
        reason = str(item.get("reason") or "")
        if "held_quiet" in kinds and reason == "quiet_hours":
            return "Held back for quiet hours"
        if "unheard" in kinds and reason == "no_speaker":
            return "Nobody heard this"
    if kind == "visitor" and "visitors" in kinds:
        return "Parcel left" if item.get("parcel") else "At the door"
    if kind == "watch" and "watches" in kinds:
        return "Watch"
    if kind == "digest" and "digest" in kinds:
        return "Daily digest"
    if kind == "learned" and "digest" in kinds:
        return "This week"
    if kind == "health" and "health" in kinds:
        return "House health"
    return None


def phone_apps(hass: HomeAssistant, options: dict[str, Any]) -> list[str]:
    """The companion-app notify services for the phones: the ones chosen, else the apps behind the phones
    already picked for notifications — never the tablets or a watch."""
    apps = [a.removeprefix("notify.") for a in options.get(CONF_PHONE_APPS) or []]
    if not apps:
        phones = {re.sub(r"_\d+$", "", t.removeprefix("notify.")) for t in options.get(CONF_NOTIFY_TARGETS) or []}
        apps = [s for s in hass.services.async_services().get("notify", {})
                if s.startswith("mobile_app_") and s.removeprefix("mobile_app_") in phones]
    return [a for a in apps if hass.services.has_service("notify", a)]


class Notifier:
    """Sends chosen timeline items to notify entities."""

    def __init__(self, hass: HomeAssistant, options: dict[str, Any]) -> None:
        self.hass = hass
        self.options = options
        self._sent: dict[str, datetime] = {}
        self._pending: dict[str, str] = {}  # snooze token -> what to remind about
        self.on_snooze = None  # set by the runtime: (what, delay) -> remind again
        self._unsubs: list = []

    @callback
    def async_start(self) -> None:
        self._unsubs = [
            self.hass.bus.async_listen(EVENT_TIMELINE, self._item),
            self.hass.bus.async_listen("mobile_app_notification_action", self._button),
        ]

    @callback
    def async_stop(self) -> None:
        while getattr(self, "_unsubs", None):
            self._unsubs.pop()()

    # ----- buttons on phone notifications (the companion app) -------------------------------------
    def buttons(self, kind: str) -> list[str]:
        """The companion-app notify actions to send this kind with buttons, or [] for a plain notification."""
        if kind not in (self.options.get(CONF_PHONE_BUTTONS) or []):
            return []
        return phone_apps(self.hass, self.options)

    def _with_buttons(self, kind: str, item: dict[str, Any]) -> tuple[list[str], dict[str, Any]] | None:
        apps = self.buttons(kind)
        if not apps:
            return None
        if kind == "door":
            camera = self.options.get(CONF_DOOR_CAMERA)
            data: dict[str, Any] = {"tag": "sentient-door"}
            if camera:
                data["image"] = f"/api/camera_proxy/{camera}"
                data["actions"] = [{"action": "URI", "title": "Voir la caméra", "uri": f"entityId:{camera}"}]
            return apps, data
        token = uuid.uuid4().hex[:10]
        self._pending[token] = item.get("detail") or item.get("text", "")
        while len(self._pending) > 30:
            self._pending.pop(next(iter(self._pending)))
        return apps, {"tag": f"sentient-{token}", "actions": [
            {"action": f"{ACTION}_SNOOZE_{token}", "title": "Reporter 10 min"},
            {"action": f"{ACTION}_DONE_{token}", "title": "Done"},
        ]}

    @callback
    def _button(self, event: Event) -> None:
        action = str(event.data.get("action") or "")
        if not action.startswith(f"{ACTION}_SNOOZE_"):
            return
        what = self._pending.pop(action.removeprefix(f"{ACTION}_SNOOZE_"), None)
        if what and self.on_snooze is not None:
            self.on_snooze(what, SNOOZE)

    @callback
    def _item(self, event: Event) -> None:
        targets = [t for t in self.options.get(CONF_NOTIFY_TARGETS) or [] if self.hass.states.get(t)]
        if not targets:
            return
        kinds = set(self.options.get(CONF_NOTIFY_WHEN, DEFAULT_NOTIFY_WHEN) or [])
        title = wanted(event.data, kinds)
        if title is None:
            return
        text = event.data.get("text", "")
        now = dt_util.utcnow()
        self._sent = {k: v for k, v in self._sent.items() if now - v < _REPEAT}
        if text.lower() in self._sent:
            return
        self._sent[text.lower()] = now
        persona = self.options.get(CONF_PERSONA) or DEFAULT_PERSONA
        kind = "door" if event.data.get("kind") == "visitor" else "reminders" if event.data.get("reminder") else None
        rich = self._with_buttons(kind, event.data) if kind else None
        if rich is not None:
            apps, data = rich
            self.hass.async_create_task(self._send_app(apps, f"{persona} · {title}", text, data), eager_start=True)
            return
        self.hass.async_create_task(self._send(targets, f"{persona} · {title}", text), eager_start=True)

    async def _send(self, targets: list[str], title: str, message: str) -> None:
        try:
            await self.hass.services.async_call(
                "notify", "send_message", {"entity_id": targets, "title": title, "message": message}, blocking=True
            )
        except Exception as err:  # noqa: BLE001 - a failed notification must never break anything else
            _LOGGER.warning("Sentient couldn't send a notification: %s", err)

    async def _send_app(self, apps: list[str], title: str, message: str, data: dict[str, Any]) -> None:
        for app in apps:
            try:
                await self.hass.services.async_call(
                    "notify", app, {"title": title, "message": message, "data": data}, blocking=True
                )
            except Exception as err:  # noqa: BLE001
                _LOGGER.warning("Sentient couldn't send a notification with buttons to %s: %s", app, err)
