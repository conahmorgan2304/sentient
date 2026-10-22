"""Phone notifications: the important things also go to people's phones.

Driven by the timeline, so anything the house says, holds back, sees at the door or
finds wrong can be mirrored — each kind switched on separately. Uses notify entities
(e.g. the companion app), so it works with whatever the house already has.
"""

from __future__ import annotations

from datetime import datetime, timedelta
import logging
from typing import Any

from homeassistant.core import Event, HomeAssistant, callback
from homeassistant.util import dt as dt_util

from .const import CONF_NOTIFY_TARGETS, CONF_NOTIFY_WHEN, CONF_PERSONA, DEFAULT_NOTIFY_WHEN, DEFAULT_PERSONA
from .timeline import EVENT_TIMELINE

_LOGGER = logging.getLogger(__name__)
_REPEAT = timedelta(minutes=10)


def wanted(item: dict[str, Any], kinds: set[str]) -> str | None:
    """The notification title for a timeline item, or None if it shouldn't be sent."""
    kind = item.get("kind")
    if kind == "said" and "urgent" in kinds and item.get("priority") == "urgent":
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
    if kind == "health" and "health" in kinds:
        return "House health"
    return None


class Notifier:
    """Sends chosen timeline items to notify entities."""

    def __init__(self, hass: HomeAssistant, options: dict[str, Any]) -> None:
        self.hass = hass
        self.options = options
        self._sent: dict[str, datetime] = {}
        self._unsub = None

    @callback
    def async_start(self) -> None:
        self._unsub = self.hass.bus.async_listen(EVENT_TIMELINE, self._item)

    @callback
    def async_stop(self) -> None:
        if self._unsub:
            self._unsub()
            self._unsub = None

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
        self.hass.async_create_task(self._send(targets, f"{persona} · {title}", text), eager_start=True)

    async def _send(self, targets: list[str], title: str, message: str) -> None:
        try:
            await self.hass.services.async_call(
                "notify", "send_message", {"entity_id": targets, "title": title, "message": message}, blocking=True
            )
        except Exception as err:  # noqa: BLE001 - a failed notification must never break anything else
            _LOGGER.warning("Sentient couldn't send a notification: %s", err)
