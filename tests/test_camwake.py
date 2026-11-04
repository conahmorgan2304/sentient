"""Sleepy cameras are woken before the AI looks at them."""

from unittest.mock import AsyncMock, patch

from homeassistant.core import HomeAssistant
from pytest_homeassistant_custom_component.common import MockConfigEntry, async_mock_service

from custom_components.sentient import camwake
from custom_components.sentient.const import DOMAIN


async def test_wakes_the_camera_once_before_looking(hass: HomeAssistant) -> None:
    camwake._woken.clear()
    entry = MockConfigEntry(domain=DOMAIN, title="Nova", unique_id=DOMAIN, data={
        "persona": "Nova", "speakers": [], "camera_wake_seconds": 3,
        "camera_wake": [{"action": "button.press", "target": {"entity_id": "button.{{ camera.split('.')[1] }}_wake"}}]})
    entry.add_to_hass(hass)
    assert await hass.config_entries.async_setup(entry.entry_id)
    await hass.async_block_till_done()
    pressed = async_mock_service(hass, "button", "press")
    with patch("custom_components.sentient.camwake.asyncio.sleep", AsyncMock()) as wait:
        att = await entry.runtime_data.spots.async_attachment("camera.drive", None)
        assert att["media_content_id"] == "media-source://camera/camera.drive"
        assert pressed[-1].data["entity_id"] == ["button.drive_wake"] and wait.await_args.args[0] == 3
        await entry.runtime_data.spots.async_attachment("camera.drive", None)
    assert len(pressed) == 1  # still awake a moment later: not woken again


async def test_off_without_actions_and_only_chosen_cameras() -> None:
    assert not camwake.wanted({}, "camera.drive")
    opts = {"camera_wake": [{"action": "script.turn_on"}], "camera_wake_cameras": ["camera.garden"]}
    assert camwake.wanted(opts, "camera.garden") and not camwake.wanted(opts, "camera.drive")
