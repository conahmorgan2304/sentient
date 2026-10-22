"""Delivery: how loud to speak, and getting music out of the way while speaking.

- Announcement volume: a level for the speaker while Sentient talks (a quieter one late at
  night, e.g. a landing tablet at 2am for an urgent message), put back afterwards.
- Ducking: anything playing in the same room is turned down while Sentient talks, then
  restored.

Volume controls are found on the speaker's own device: a media player, or a number entity
whose name says "volume". No configuration of entities needed.
"""

from __future__ import annotations

import asyncio
import contextlib
import logging
from typing import Any

from homeassistant.core import HomeAssistant
from homeassistant.helpers import entity_registry as er

from .presence import area_of

_LOGGER = logging.getLogger(__name__)


def volume_control(hass: HomeAssistant, speaker: str) -> tuple[str, str] | None:
    """('media_player'|'number', entity_id) controlling the speaker's own volume."""
    reg = er.async_get(hass)
    entry = reg.async_get(speaker)
    if entry is None or not entry.device_id:
        return None
    siblings = [e for e in er.async_entries_for_device(reg, entry.device_id) if not e.disabled_by]
    for e in siblings:
        if e.domain == "media_player":
            return ("media_player", e.entity_id)
    numbers = [e for e in siblings if e.domain == "number" and "volume" in e.entity_id]
    # prefer the general/media volume over an assistant- or alarm-only one
    numbers.sort(key=lambda e: (any(w in e.entity_id for w in ("assistant", "alarm", "ring", "notification")), len(e.entity_id)))
    return ("number", numbers[0].entity_id) if numbers else None


def _level(hass: HomeAssistant, control: tuple[str, str]) -> float | None:
    """Current level as 0..1."""
    kind, entity_id = control
    st = hass.states.get(entity_id)
    if st is None:
        return None
    try:
        if kind == "media_player":
            return float(st.attributes.get("volume_level"))
        top = float(st.attributes.get("max", 100) or 100)
        return float(st.state) / top
    except (TypeError, ValueError):
        return None


async def _set(hass: HomeAssistant, control: tuple[str, str], level: float) -> None:
    kind, entity_id = control
    level = max(0.0, min(1.0, level))
    if kind == "media_player":
        await hass.services.async_call(
            "media_player", "volume_set", {"entity_id": entity_id, "volume_level": round(level, 2)}, blocking=True
        )
    else:
        st = hass.states.get(entity_id)
        top = float((st.attributes.get("max", 100) if st else 100) or 100)
        await hass.services.async_call(
            "number", "set_value", {"entity_id": entity_id, "value": round(level * top)}, blocking=True
        )


def playing_nearby(hass: HomeAssistant, speakers: list[str]) -> list[str]:
    """Media players playing in the speakers' rooms (not the speakers' own players)."""
    areas = {a for s in speakers if (a := area_of(hass, s))}
    own = {c[1] for s in speakers if (c := volume_control(hass, s))}
    out = []
    for st in hass.states.async_all("media_player"):
        if st.state == "playing" and st.entity_id not in own and area_of(hass, st.entity_id) in areas:
            out.append(st.entity_id)
    return out


@contextlib.asynccontextmanager
async def delivered(hass: HomeAssistant, speakers: list[str], volume: float | None, duck: float | None):
    """Set speaker volume and duck nearby media for the duration of the block."""
    restore: list[tuple[tuple[str, str], float]] = []
    try:
        if volume is not None:
            for speaker in speakers:
                if (control := volume_control(hass, speaker)) and (before := _level(hass, control)) is not None:
                    if abs(before - volume) > 0.01:
                        restore.append((control, before))
                        await _set(hass, control, volume)
        if duck is not None:
            for player in playing_nearby(hass, speakers):
                control = ("media_player", player)
                if (before := _level(hass, control)) is not None and before > duck:
                    restore.append((control, before))
                    await _set(hass, control, duck)
        if restore:
            await asyncio.sleep(0.4)  # let the new level land before speaking
    except Exception as err:  # noqa: BLE001 - never stop the message for this
        _LOGGER.debug("Could not adjust volume: %s", err)
    try:
        yield
    finally:
        for control, level in reversed(restore):
            try:
                await _set(hass, control, level)
            except Exception as err:  # noqa: BLE001
                _LOGGER.debug("Could not restore %s: %s", control[1], err)
