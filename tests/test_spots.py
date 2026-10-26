"""Spots: named areas in a camera picture, and cropped checks."""

import io
from unittest.mock import patch

from homeassistant.components.camera import Image as CameraImage
from homeassistant.core import HomeAssistant, ServiceCall, SupportsResponse
from homeassistant.helpers import llm
from PIL import Image
from pytest_homeassistant_custom_component.common import MockConfigEntry, async_mock_service

from custom_components.sentient.const import DOMAIN

from .test_watches import _garden_camera

OPTIONS = {
    "persona": "Nova",
    "speakers": ["assist_satellite.kitchen"],
    "default_speaker": "assist_satellite.kitchen",
    "quiet_start": "00:00:00",
    "quiet_end": "00:00:00",
}


def _jpeg(w=400, h=200) -> bytes:
    buf = io.BytesIO()
    Image.new("RGB", (w, h), (20, 30, 40)).save(buf, "JPEG")
    return buf.getvalue()


async def test_teach_spot_then_crop(hass: HomeAssistant, tmp_path) -> None:
    hass.config.media_dirs = {"local": str(tmp_path)}
    entry = MockConfigEntry(domain=DOMAIN, title="Nova", data=OPTIONS, unique_id=DOMAIN)
    entry.add_to_hass(hass)
    assert await hass.config_entries.async_setup(entry.entry_id)
    await hass.async_block_till_done()
    hass.states.async_set("assist_satellite.kitchen", "idle")
    async_mock_service(hass, "assist_satellite", "announce")
    _garden_camera(hass)
    calls: list[ServiceCall] = []
    replies = [
        {"found": True, "left": 50, "top": 50, "right": 75, "bottom": 100},
        "Two cats are sitting by the pen.",
    ]

    async def generate(call: ServiceCall):
        calls.append(call)
        return {"conversation_id": "x", "data": replies[len(calls) - 1]}

    hass.services.async_register("ai_task", "generate_data", generate, supports_response=SupportsResponse.ONLY)
    ctx = llm.LLMContext(platform="test", context=None, language="en", assistant="conversation", device_id=None)
    api = await llm.async_get_api(hass, "sentient", ctx)
    out = await api.async_call_tool(llm.ToolInput(tool_name="sentient_spot", tool_args={"camera": "garden", "name": "the rabbit pen"}))
    assert out == {"learned": "the rabbit pen", "camera": "Garden"}
    out = await api.async_call_tool(llm.ToolInput(tool_name="sentient_spot", tool_args={}))
    assert out["spots"] == [{"name": "the rabbit pen", "camera": "camera.garden", "also_called": ""}]

    with patch("homeassistant.components.camera.async_get_image", return_value=CameraImage("image/jpeg", _jpeg())):
        out = await api.async_call_tool(llm.ToolInput(
            tool_name="sentient_look", tool_args={"camera": "garden", "question": "what are the cats doing near the rabbit pen?"}))
    assert out["answer"] == "Two cats are sitting by the pen."
    media_id = calls[-1].data["attachments"][0]["media_content_id"]
    assert media_id == "media-source://media_source/local/sentient/rabbit_pen_garden.jpg"
    cropped = Image.open(tmp_path / "sentient" / "rabbit_pen_garden.jpg")
    assert cropped.size == (124, 112)  # 25% x 50% of 400x200, plus a 12% margin (clipped at the bottom edge)

    # a watch whose question names the spot uses it automatically
    await hass.services.async_call(
        DOMAIN, "watch", {"what": "cats near the rabbit pen", "entity": "garden", "question": "Any cats by the rabbit pen?"},
        blocking=True,
    )
    assert entry.runtime_data.watches.watches[0].spot == "rabbit pen"
    assert hass.states.get("sensor.nova_watches").attributes["watches"][0]["spot"] == "rabbit pen"


async def test_spot_by_another_name_picks_its_camera_and_masks_the_shape(hass: HomeAssistant, tmp_path) -> None:
    hass.config.media_dirs = {"local": str(tmp_path)}
    entry = MockConfigEntry(domain=DOMAIN, title="Nova", data=OPTIONS, unique_id=DOMAIN)
    entry.add_to_hass(hass)
    assert await hass.config_entries.async_setup(entry.entry_id)
    await hass.async_block_till_done()
    hass.states.async_set("camera.front", "idle", {"friendly_name": "Front"})
    hass.states.async_set("camera.gate", "idle", {"friendly_name": "Gate"})
    spots = entry.runtime_data.spots
    spots.set("camera.gate", "the drive", [0, 0, 1, 1], points=[[0.5, 0.5], [1, 0.5], [1, 1], [0.5, 1]],
              aliases="driveway, parking space")
    calls: list[ServiceCall] = []

    async def generate(call: ServiceCall):
        calls.append(call)
        return {"conversation_id": "x", "data": "Yes, a black car is parked there."}

    hass.services.async_register("ai_task", "generate_data", generate, supports_response=SupportsResponse.ONLY)
    white = io.BytesIO()
    Image.new("RGB", (400, 200), (255, 255, 255)).save(white, "JPEG")
    with patch("homeassistant.components.camera.async_get_image", return_value=CameraImage("image/jpeg", white.getvalue())):
        out = await entry.runtime_data.watches.async_look("front", "is there a car on the driveway?")
    assert out["camera"] == "Gate" and out["spot"] == "the drive"
    assert calls[0].data["attachments"][0]["media_content_id"].startswith("media-source://media_source/local/sentient/")
    saved = Image.open(tmp_path / "sentient" / "drive_gate.jpg")
    assert saved.getpixel((2, 2))[0] < 40  # outside the shape: blacked out
    w, h = saved.size
    assert saved.getpixel((w - 3, h - 3))[0] > 200  # inside: kept
    ctx = llm.LLMContext(platform="test", context=None, language="en", assistant="conversation", device_id=None)
    api = await llm.async_get_api(hass, "sentient", ctx)
    assert "the drive (on the Gate camera; also called driveway, parking space)" in api.api_prompt
