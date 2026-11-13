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
    PANEL_URL_PATH,
)
from .timeline import EVENT_TIMELINE

_LOGGER = logging.getLogger(__name__)
_REPEAT = timedelta(minutes=10)
ACTION = "SENTIENT"  # prefix of this integration's notification buttons
SNOOZE = timedelta(minutes=10)
PHONE_QUESTION_OPEN = timedelta(hours=2)  # a Yes tapped later than this does nothing


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
        if "holiday" in kinds and reason == "holiday":
            return "While you're away"
        if "held_quiet" in kinds and reason == "quiet_hours":
            return "Held back for quiet hours"
        if "unheard" in kinds and reason == "no_speaker":
            return "Nobody heard this"
    if kind == "visitor" and "visitors" in kinds:
        if item.get("detail") == "nobody" and not item.get("parcel"):
            return None  # the doorbell camera looked and saw no one ("empty street view"): nothing to tell a phone
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


def app_service_for(hass: HomeAssistant, entity_id: str) -> str | None:
    """The companion app's own notify action behind a phone's notify entity (it can carry a tap link and
    buttons; notify.send_message can't), or None for anything that isn't the app."""
    from homeassistant.helpers import device_registry as dr, entity_registry as er
    from homeassistant.util import slugify

    entry = er.async_get(hass).async_get(entity_id)
    if entry is None or entry.platform != "mobile_app":
        return None
    names = [entity_id.removeprefix("notify.")]
    if entry.device_id and (device := dr.async_get(hass).async_get(entry.device_id)) is not None:
        names = [slugify(device.name or ""), slugify(device.name_by_user or "")] + names
    for name in names:
        if name and hass.services.has_service("notify", f"mobile_app_{name}"):
            return f"mobile_app_{name}"
    return None


def tap_link(item: dict[str, Any]) -> str:
    """Where tapping the notification goes: that moment on Sentient's timeline."""
    from urllib.parse import quote

    return f"/{PANEL_URL_PATH}/timeline?item={quote(str(item.get('time') or ''))}"


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
        self._questions: dict[str, dict[str, Any]] = {}  # token -> {on_answer, until, tag}: yes/no asked on phones
        self.on_sent = None  # set by the runtime: (timeline item time, title) — marks it on the timeline as sent
        self.holiday = None  # set by the runtime: on holiday, what it would have said and visitors go to phones
        self._unsubs: list = []

    @callback
    def async_start(self) -> None:
        self._unsubs = [
            self.hass.bus.async_listen(EVENT_TIMELINE, self._item),
            self.hass.bus.async_listen("mobile_app_notification_action", self._button),
        ]

    @callback
    def async_stop(self) -> None:
        self.invalidate_questions()
        while getattr(self, "_unsubs", None):
            self._unsubs.pop()()

    def invalidate_questions(self) -> None:
        """Old notification buttons cannot authorise work after settings change or unload."""
        self._questions.clear()
        self._pending.clear()

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
                data["actions"] = [{"action": "URI", "title": "Show camera", "uri": f"entityId:{camera}"}]
            return apps, data
        token = uuid.uuid4().hex[:10]
        self._pending[token] = item.get("detail") or item.get("text", "")
        while len(self._pending) > 30:
            self._pending.pop(next(iter(self._pending)))
        return apps, {"tag": f"sentient-{token}", "actions": [
            {"action": f"{ACTION}_SNOOZE_{token}", "title": "Snooze 10 min"},
            {"action": f"{ACTION}_DONE_{token}", "title": "Done"},
        ]}

    # ----- questions answered from the phone ------------------------------------------------------
    def can_ask(self) -> bool:
        """Questions go to phones (with Yes / No) when nobody's near a speaker — if that's switched on."""
        return "questions" in (self.options.get(CONF_PHONE_BUTTONS) or []) and bool(phone_apps(self.hass, self.options))

    async def async_ask(self, question: str, on_answer: Any) -> bool:
        apps = phone_apps(self.hass, self.options)
        if not apps or "questions" not in (self.options.get(CONF_PHONE_BUTTONS) or []):
            return False
        token = uuid.uuid4().hex[:10]
        now = dt_util.utcnow()
        self._questions = {k: v for k, v in self._questions.items() if v["until"] > now}
        self._questions[token] = {"on_answer": on_answer, "until": now + PHONE_QUESTION_OPEN, "tag": f"sentient-q-{token}", "apps": set(apps)}
        persona = self.options.get(CONF_PERSONA) or DEFAULT_PERSONA
        await self._send_app(apps, f"{persona} · A question", question, {"tag": f"sentient-q-{token}", "actions": [
            {"action": f"{ACTION}_YES_{token}", "title": "Yes"},
            {"action": f"{ACTION}_NO_{token}", "title": "No"},
        ]})
        return True

    async def _async_answer(self, token: str, kind: str) -> None:
        question = self._questions.pop(token, None)
        if (question is None or question["until"] <= dt_util.utcnow() or kind not in ("yes", "no")
                or not self.can_ask() or question["apps"] != set(phone_apps(self.hass, self.options))):
            return
        try:
            reply = await question["on_answer"](kind)
        except Exception as err:  # noqa: BLE001
            _LOGGER.warning("Sentient couldn't act on a phone answer: %s", err)
            reply = "That didn't work."
        persona = self.options.get(CONF_PERSONA) or DEFAULT_PERSONA
        await self._send_app(phone_apps(self.hass, self.options), persona, reply or "Okay.", {"tag": question["tag"]})

    @callback
    def _button(self, event: Event) -> None:
        action = str(event.data.get("action") or "")
        for kind in ("YES", "NO"):
            if action.startswith(f"{ACTION}_{kind}_"):
                self.hass.async_create_task(self._async_answer(action.removeprefix(f"{ACTION}_{kind}_"), kind.lower()))
                return
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
        if self.holiday is not None and self.holiday.active:
            kinds |= {"holiday", "visitors"}
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
        link = tap_link(event.data)
        tap = {"url": link, "clickAction": link, "group": "sentient"}  # iOS opens url, Android clickAction
        if self.on_sent is not None:
            self.on_sent(event.data.get("time"), title)
        rich = self._with_buttons(kind, event.data) if kind else None
        if rich is not None:
            apps, data = rich
            self.hass.async_create_task(self._send_app(apps, f"{persona} · {title}", text, {**tap, **data}), eager_start=True)
            return
        # phones go through the app's own action (title, tap link); anything else through send_message
        apps = {t: app for t in targets if (app := app_service_for(self.hass, t)) is not None}
        if apps:
            self.hass.async_create_task(self._send_app(sorted(set(apps.values())), f"{persona} · {title}", text, tap), eager_start=True)
        if others := [t for t in targets if t not in apps]:
            self.hass.async_create_task(self._send(others, f"{persona} · {title}", text), eager_start=True)

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
