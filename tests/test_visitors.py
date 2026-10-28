"""The quiet visitor log and parcel check."""

from datetime import timedelta
from unittest.mock import patch

from homeassistant.core import HomeAssistant, ServiceCall, SupportsResponse
from homeassistant.helpers import llm
from pytest_homeassistant_custom_component.common import (
    MockConfigEntry,
    async_fire_time_changed,
    async_mock_service,
)

from custom_components.sentient.const import DOMAIN

OPTIONS = {
    "persona": "Nova",
    "speakers": ["assist_satellite.kitchen"],
    "default_speaker": "assist_satellite.kitchen",
    "quiet_start": "00:00:00",
    "quiet_end": "00:00:00",
    "door_camera": "camera.doorbell",
    "door_triggers": ["binary_sensor.doorbell", "event.doorbell_ring"],
}


async def _setup(hass: HomeAssistant, **extra) -> MockConfigEntry:
    entry = MockConfigEntry(domain=DOMAIN, title="Nova", data={**OPTIONS, **extra}, unique_id=DOMAIN)
    entry.add_to_hass(hass)
    assert await hass.config_entries.async_setup(entry.entry_id)
    await hass.async_block_till_done()
    hass.states.async_set("assist_satellite.kitchen", "idle")
    hass.states.async_set("camera.doorbell", "idle")
    hass.states.async_set("binary_sensor.doorbell", "off")
    hass.states.async_set("event.doorbell_ring", "2026-10-21T10:00:00+00:00")
    await hass.async_block_till_done()
    return entry


def _ai(hass: HomeAssistant, replies: list[dict]):
    calls: list[ServiceCall] = []

    async def generate(call: ServiceCall):
        calls.append(call)
        return {"conversation_id": "x", "data": replies[min(len(calls), len(replies)) - 1]}

    hass.services.async_register("ai_task", "generate_data", generate, supports_response=SupportsResponse.ONLY)
    return calls


async def test_logs_quietly_then_says_parcel_left(hass: HomeAssistant, freezer) -> None:
    await _setup(hass)
    said = async_mock_service(hass, "assist_satellite", "announce")
    ai = _ai(hass, [
        {"kind": "delivery", "description": "A delivery driver holding a small parcel.", "parcel": True},
        {"parcel_left": True},
    ])
    with patch("custom_components.sentient.visitors.asyncio.sleep"):
        hass.states.async_set("binary_sensor.doorbell", "on")
        await hass.async_block_till_done()
    assert len(ai) == 1 and ai[0].data["attachments"][0]["media_content_id"] == "media-source://camera/camera.doorbell"
    assert not said  # who it was is never announced by default
    sensor = hass.states.get("sensor.nova_visitors")
    assert sensor.state == "1" and sensor.attributes["recent"][0]["description"] == "A delivery driver holding a small parcel"
    freezer.tick(timedelta(seconds=181))
    async_fire_time_changed(hass)
    await hass.async_block_till_done()
    assert [c.data["message"] for c in said] == ["A parcel's been left at the front door."]
    ctx = llm.LLMContext(platform="test", context=None, language="en", assistant="conversation", device_id=None)
    api = await llm.async_get_api(hass, "sentient", ctx)
    assert "Visitors to the door today: 1" in api.api_prompt
    out = await api.async_call_tool(llm.ToolInput(tool_name="sentient_visitors", tool_args={}))
    assert out["visitors"][0].endswith("A delivery driver holding a small parcel (left a parcel)")
    facts = await hass.services.async_call(DOMAIN, "briefing", {"speak": False}, blocking=True, return_response=True)
    assert "left a parcel" in facts["visitors_today"][0]


async def test_event_entity_trigger_cooldown_and_announce_option(hass: HomeAssistant) -> None:
    await _setup(hass, announce_visitors=True, parcel_alerts=False)
    said = async_mock_service(hass, "assist_satellite", "announce")
    ai = _ai(hass, [{"kind": "visitor", "description": "A man in a red coat"}])
    with patch("custom_components.sentient.visitors.asyncio.sleep"):
        hass.states.async_set("event.doorbell_ring", "2026-10-21T19:00:00+00:00", {"event_type": "ring"})
        await hass.async_block_till_done()
        hass.states.async_set("event.doorbell_ring", "2026-10-21T19:00:20+00:00", {"event_type": "ring"})
        await hass.async_block_till_done()
    assert len(ai) == 1  # second ring inside a minute isn't looked at again
    assert said[-1].data["message"] == "Someone's at the door: A man in a red coat."


async def test_recognised_name_is_used_when_fresh(hass: HomeAssistant) -> None:
    await _setup(hass, announce_visitors=True, parcel_alerts=False, face_sensor="sensor.doorbell_face")
    said = async_mock_service(hass, "assist_satellite", "announce")
    _ai(hass, [{"kind": "visitor", "description": "A man in a red coat"}])
    hass.states.async_set("sensor.doorbell_face", "conah")
    with patch("custom_components.sentient.visitors.asyncio.sleep"):
        hass.states.async_set("binary_sensor.doorbell", "on")
        await hass.async_block_till_done()
    assert said[-1].data["message"] == "Conah's at the door."
    log = hass.states.get("sensor.nova_visitors").attributes["recent"]
    assert log[0]["name"] == "Conah"


async def test_doorstep_visit_without_ring_checks_for_parcel_once(hass: HomeAssistant, freezer) -> None:
    await _setup(hass, doorstep_sensors=["binary_sensor.front_garden_person"])
    hass.states.async_set("binary_sensor.front_garden_person", "off")
    await hass.async_block_till_done()
    said = async_mock_service(hass, "assist_satellite", "announce")
    ai = _ai(hass, [
        {"parcel_left": True, "description": "A small brown box."},
        {"parcel_left": True, "description": "A small brown box."},
        {"parcel_left": False},
        {"parcel_left": True, "description": "Some post"},
    ])

    async def visit(minutes: int) -> None:
        hass.states.async_set("binary_sensor.front_garden_person", "on")
        await hass.async_block_till_done()
        hass.states.async_set("binary_sensor.front_garden_person", "off")
        await hass.async_block_till_done()
        freezer.tick(timedelta(seconds=11))
        async_fire_time_changed(hass)
        await hass.async_block_till_done()
        freezer.tick(timedelta(minutes=minutes))

    await visit(3)
    assert len(ai) == 1 and "without" in ai[0].data["instructions"]
    assert [c.data["message"] for c in said] == ["A parcel's been left at the front door."]
    log = hass.states.get("sensor.nova_visitors").attributes["recent"]
    assert log[0]["description"] == "someone left A small brown box without ringing"
    await visit(3)  # same parcel still there: not said again
    await visit(3)  # taken in
    await visit(3)  # new post
    # only the doorstep checks: other parts of the house may make their own calls while the clock jumps
    assert len([c for c in ai if "doorstep" in c.data["task_name"]]) == 4
    assert len(said) == 2


async def test_doorstep_check_skipped_after_a_ring_and_when_parcels_off(hass: HomeAssistant, freezer) -> None:
    await _setup(hass, doorstep_sensors=["binary_sensor.front_garden_person"])
    hass.states.async_set("binary_sensor.front_garden_person", "off")
    await hass.async_block_till_done()
    ai = _ai(hass, [{"kind": "visitor", "description": "A man in a red coat"}])
    with patch("custom_components.sentient.visitors.asyncio.sleep"):
        hass.states.async_set("binary_sensor.doorbell", "on")
        await hass.async_block_till_done()
    hass.states.async_set("binary_sensor.front_garden_person", "on")
    await hass.async_block_till_done()
    hass.states.async_set("binary_sensor.front_garden_person", "off")
    await hass.async_block_till_done()
    freezer.tick(timedelta(seconds=11))
    async_fire_time_changed(hass)
    await hass.async_block_till_done()
    assert len(ai) == 1  # only the doorbell look
