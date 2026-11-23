"""Self-grading reads the real recorder: the state at a moment, and a little after it."""
import asyncio
from datetime import timedelta
from unittest.mock import patch

from homeassistant.core import HomeAssistant
from homeassistant.util import dt as dt_util
from pytest_homeassistant_custom_component.components.recorder.common import async_wait_recording_done

from custom_components.sentient.grading import _states_at


async def test_reads_the_recorder(recorder_mock, hass: HomeAssistant) -> None:
    hass.states.async_set("light.kitchen", "on")
    await async_wait_recording_done(hass)
    await asyncio.sleep(0.05)
    when = dt_util.utcnow()
    await asyncio.sleep(0.3)
    hass.states.async_set("light.kitchen", "off")
    await async_wait_recording_done(hass)
    with patch("custom_components.sentient.grading.SETTLE", timedelta(seconds=0.1)):
        assert await _states_at(hass, ["light.kitchen"], when) == {"light.kitchen": ("on", "on")}
    with patch("custom_components.sentient.grading.SETTLE", timedelta(seconds=0.5)):
        assert await _states_at(hass, ["light.kitchen"], when) == {"light.kitchen": ("on", "off")}
