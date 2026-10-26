"""Delivery: how loud to speak, and getting music out of the way while speaking.

- Announcement volume: a level for the speaker while Sentient talks (a quieter one late at
  night, e.g. a landing tablet at 2am for an urgent message), put back afterwards.
- Ducking: anything playing in the same room is turned down while Sentient talks, then
  restored.

- Room-aware volume: a speaker whose device also measures the room's loudness (a voice
  speaker's microphone, e.g. ESPHome's sound_level sensor) has a quick listen first; in a noisy
  room it speaks up, back to normal in a quiet one.

Volume controls are found on the speaker's own device: a media player, or a number entity
whose name says "volume". No configuration of entities needed.
"""

from __future__ import annotations

import asyncio
import contextlib
from datetime import timedelta
import logging
from typing import Any

from homeassistant.core import Context, HomeAssistant
from homeassistant.helpers import entity_registry as er
from homeassistant.util import dt as dt_util

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


def loudness_controls(hass: HomeAssistant, speaker: str) -> tuple[str, str | None] | None:
    """(loudness sensor, switch that makes it report or None) on the speaker's own device."""
    reg = er.async_get(hass)
    entry = reg.async_get(speaker)
    if entry is None or not entry.device_id:
        return None
    siblings = [e for e in er.async_entries_for_device(reg, entry.device_id) if not e.disabled_by]
    sensor = next((e.entity_id for e in siblings if e.domain == "sensor"
                   and ("loudness" in e.entity_id or "sound_level" in e.entity_id)), None)
    if sensor is None:
        return None
    switch = next((e.entity_id for e in siblings if e.domain == "switch"
                   and ("loudness" in e.entity_id or "sound_level" in e.entity_id)), None)
    return sensor, switch


LISTEN_SECONDS = 1.2
BOOST_PER_DB = 0.015  # +1.5% volume for every dB the room is above its usual level
MAX_BOOST = 0.3
BOOST_FROM_DB = 6  # a room this much above its usual level counts as noisy


def _db(hass: HomeAssistant, sensor: str) -> float | None:
    st = hass.states.get(sensor)
    try:
        return float(st.state) if st is not None else None
    except (TypeError, ValueError):
        return None


class RoomLevels:
    """Each room's usual loudness, learned from quick listens, and the boost a noisy room needs."""

    def __init__(self) -> None:
        self.usual: dict[str, float] = {}
        self.last: dict[str, dict[str, Any]] = {}

    def boost(self, sensor: str, level: float) -> float:
        usual = self.usual.get(sensor)
        if usual is None:
            self.usual[sensor] = level
            return 0.0
        if level < usual:
            self.usual[sensor] = usual * 0.7 + level * 0.3  # quieter: learn it quickly
        elif level < usual + BOOST_FROM_DB:
            self.usual[sensor] = usual * 0.9 + level * 0.1  # normal: drift slowly
        over = level - self.usual[sensor]
        if over < BOOST_FROM_DB:
            return 0.0
        return min(MAX_BOOST, over * BOOST_PER_DB)

    async def async_listen(self, hass: HomeAssistant, speaker: str) -> float | None:
        """A quick listen to the speaker's room: the loudest level heard, or None."""
        controls = loudness_controls(hass, speaker)
        if controls is None:
            return None
        sensor, switch = controls
        heard: list[float] = []

        def _seen(event) -> None:
            new = event.data.get("new_state")
            try:
                heard.append(float(new.state))
            except (AttributeError, TypeError, ValueError):
                pass

        from homeassistant.helpers.event import async_track_state_change_event

        unsub = async_track_state_change_event(hass, [sensor], _seen)
        turned_on = False
        try:
            sw = hass.states.get(switch) if switch else None
            if sw is not None and sw.state == "off":
                await hass.services.async_call("switch", "turn_on", {"entity_id": switch}, blocking=True,
                                               context=Context(parent_id=Context().id))
                turned_on = True
            await asyncio.sleep(LISTEN_SECONDS)
        except Exception as err:  # noqa: BLE001 - never stop the message for this
            _LOGGER.debug("Could not listen to %s: %s", sensor, err)
        finally:
            unsub()
            if turned_on:
                try:
                    await hass.services.async_call("switch", "turn_off", {"entity_id": switch}, blocking=False,
                                                   context=Context(parent_id=Context().id))
                except Exception:  # noqa: BLE001
                    pass
        if not heard:
            if switch:  # a sensor that only reports while its switch is on: nothing new means a still room
                st = hass.states.get(sensor)
                if st is None or dt_util.utcnow() - st.last_updated > timedelta(seconds=30):
                    return None
            level = _db(hass, sensor)
            if level is None:
                return None
            heard.append(level)
        level = max(heard)
        self.last[speaker] = {"level": round(level, 1), "time": dt_util.utcnow().isoformat()}
        return level

    async def async_volumes(self, hass: HomeAssistant, speakers: list[str], base: float | None) -> dict[str, float]:
        """Per-speaker volume: the usual level, raised where the room is noisy."""
        out: dict[str, float] = {}
        speakers = [s for s in speakers if loudness_controls(hass, s) is not None]
        if not speakers:
            return out
        results = await asyncio.gather(*(self.async_listen(hass, s) for s in speakers), return_exceptions=True)
        for speaker, level in zip(speakers, results):
            if not isinstance(level, float):
                continue
            controls = loudness_controls(hass, speaker)
            boost = self.boost(controls[0], level) if controls else 0.0
            if boost <= 0:
                continue
            start = base
            if start is None and (control := volume_control(hass, speaker)):
                start = _level(hass, control)
            if start is not None:
                out[speaker] = min(1.0, start + boost)
                self.last[speaker]["boost"] = round(boost, 2)
        return out


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
async def delivered(
    hass: HomeAssistant, speakers: list[str], volume: float | dict[str, float] | None, duck: float | None
):
    """Set speaker volume (one level, or one per speaker) and duck nearby media for the block."""
    restore: list[tuple[tuple[str, str], float]] = []
    try:
        for speaker in speakers:
            level = volume.get(speaker) if isinstance(volume, dict) else volume
            if level is not None:
                volume_for = level
                if (control := volume_control(hass, speaker)) and (before := _level(hass, control)) is not None:
                    if abs(before - volume_for) > 0.01:
                        restore.append((control, before))
                        await _set(hass, control, volume_for)
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
