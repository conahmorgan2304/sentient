"""Watches: state and camera, by service and by voice tool."""

from datetime import timedelta
from unittest.mock import patch

from homeassistant.core import HomeAssistant, ServiceCall, SupportsResponse
from homeassistant.helpers import device_registry as dr
from homeassistant.helpers import entity_registry as er
from homeassistant.helpers import llm
from homeassistant.util import dt as dt_util
from pytest_homeassistant_custom_component.common import MockConfigEntry, async_mock_service

from custom_components.sentient.const import DOMAIN
from custom_components.sentient.watches import parse_until

OPTIONS = {
    "persona": "Nova",
    "speakers": ["assist_satellite.kitchen"],
    "default_speaker": "assist_satellite.kitchen",
    "quiet_start": "00:00:00",
    "quiet_end": "00:00:00",
    "banned_words": "sir",
    "camera_cooldown": 60,
}


async def _setup(hass: HomeAssistant) -> MockConfigEntry:
    entry = MockConfigEntry(domain=DOMAIN, title="Nova", data=OPTIONS, unique_id=DOMAIN)
    entry.add_to_hass(hass)
    assert await hass.config_entries.async_setup(entry.entry_id)
    await hass.async_block_till_done()
    hass.states.async_set("assist_satellite.kitchen", "idle")
    return entry


def _fake_ai(hass: HomeAssistant, answer: bool, description: str = ""):
    calls: list[ServiceCall] = []

    async def generate(call: ServiceCall):
        calls.append(call)
        return {"conversation_id": "x", "data": {"answer": answer, "description": description}}

    hass.services.async_register("ai_task", "generate_data", generate, supports_response=SupportsResponse.ONLY)
    return calls


def _garden_camera(hass: HomeAssistant) -> None:
    other = MockConfigEntry(domain="frigate")
    other.add_to_hass(hass)
    device = dr.async_get(hass).async_get_or_create(config_entry_id=other.entry_id, identifiers={("frigate", "garden")})
    ents = er.async_get(hass)
    for domain, obj in (("camera", "garden"), ("binary_sensor", "garden_motion"), ("binary_sensor", "garden_cat_occupancy")):
        ents.async_get_or_create(domain, "frigate", obj, suggested_object_id=obj, device_id=device.id)
    hass.states.async_set("camera.garden", "idle", {"friendly_name": "Garden"})
    hass.states.async_set("binary_sensor.garden_motion", "off", {"device_class": "motion"})
    hass.states.async_set("binary_sensor.garden_cat_occupancy", "off", {"device_class": "occupancy"})


async def test_state_watch_fires_once(hass: HomeAssistant) -> None:
    entry = await _setup(hass)
    said = async_mock_service(hass, "assist_satellite", "announce")
    hass.states.async_set("sensor.washer", "running", {"friendly_name": "Washing machine"})
    res = await hass.services.async_call(
        DOMAIN, "watch", {"what": "washing machine finishing", "entity": "washing machine", "to_state": "finished"},
        blocking=True, return_response=True,
    )
    assert res["kind"] == "state"
    assert hass.states.get("sensor.nova_watches").state == "1"
    hass.states.async_set("sensor.washer", "finished", {"friendly_name": "Washing machine"})
    await hass.async_block_till_done()
    assert said[-1].data["message"] == "Washing machine is now finished."
    assert not entry.runtime_data.watches.watches
    assert hass.states.get("sensor.nova_watches").state == "0"


async def test_numeric_watch_only_on_crossing(hass: HomeAssistant) -> None:
    await _setup(hass)
    said = async_mock_service(hass, "assist_satellite", "announce")
    hass.states.async_set("sensor.bedroom_temp", "25", {"friendly_name": "Bedroom temperature", "unit_of_measurement": "°C"})
    await hass.services.async_call(
        DOMAIN, "watch", {"what": "bedroom too warm", "entity": "sensor.bedroom_temp", "above": 24, "repeat": True,
                          "message": "The bedroom's getting warm."},
        blocking=True,
    )
    hass.states.async_set("sensor.bedroom_temp", "25.5", {"unit_of_measurement": "°C"})  # already above: no
    await hass.async_block_till_done()
    assert not said
    hass.states.async_set("sensor.bedroom_temp", "23", {"unit_of_measurement": "°C"})
    hass.states.async_set("sensor.bedroom_temp", "24.5", {"unit_of_measurement": "°C"})
    await hass.async_block_till_done()
    assert [c.data["message"] for c in said] == ["The bedroom's getting warm."]


async def test_camera_watch_asks_ai_on_motion_with_cooldown(hass: HomeAssistant) -> None:
    entry = await _setup(hass)
    said = async_mock_service(hass, "assist_satellite", "announce")
    ai = _fake_ai(hass, True, "Two cats are by the rabbit pen.")
    _garden_camera(hass)
    res = await hass.services.async_call(
        DOMAIN, "watch",
        {"what": "cats near the rabbit pen", "entity": "garden", "question": "Are any cats near the rabbit pen?",
         "look_for": "cats", "until": "23:59", "repeat": True},
        blocking=True, return_response=True,
    )
    assert res["kind"] == "camera"
    watch = entry.runtime_data.watches.watches[0]
    assert watch.entity_id == "camera.garden"
    assert watch.triggers == ["binary_sensor.garden_cat_occupancy"]

    with patch("custom_components.sentient.watches.asyncio.sleep"):
        hass.states.async_set("binary_sensor.garden_motion", "on", {"device_class": "motion"})  # not a trigger
        await hass.async_block_till_done()
        assert not ai
        hass.states.async_set("binary_sensor.garden_cat_occupancy", "on", {"device_class": "occupancy"})
        await hass.async_block_till_done()
        assert len(ai) == 1
        assert ai[0].data["attachments"][0]["media_content_id"] == "media-source://camera/camera.garden"
        assert said[-1].data["message"] == "Two cats are by the rabbit pen."
        # off and on again inside the cooldown: no second look
        hass.states.async_set("binary_sensor.garden_cat_occupancy", "off", {"device_class": "occupancy"})
        hass.states.async_set("binary_sensor.garden_cat_occupancy", "on", {"device_class": "occupancy"})
        await hass.async_block_till_done()
        assert len(ai) == 1
    assert watch.checks == 1 and watch.fired == 1


async def test_camera_watch_no_means_quiet(hass: HomeAssistant) -> None:
    entry = await _setup(hass)
    said = async_mock_service(hass, "assist_satellite", "announce")
    _fake_ai(hass, False)
    _garden_camera(hass)
    await hass.services.async_call(
        DOMAIN, "watch", {"what": "cats", "entity": "camera.garden", "question": "Any cats?"}, blocking=True
    )
    with patch("custom_components.sentient.watches.asyncio.sleep"):
        hass.states.async_set("binary_sensor.garden_motion", "on", {"device_class": "motion"})
        await hass.async_block_till_done()
    assert not said
    assert entry.runtime_data.watches.watches[0].last_result == "no"


async def test_expiry_and_cancel(hass: HomeAssistant, freezer) -> None:
    entry = await _setup(hass)
    hass.states.async_set("sensor.washer", "running", {"friendly_name": "Washing machine"})
    hass.states.async_set("lock.back_door", "unlocked", {"friendly_name": "Back door"})
    await hass.services.async_call(
        DOMAIN, "watch", {"what": "washer done", "entity": "sensor.washer", "to_state": "off", "minutes": 30}, blocking=True
    )
    await hass.services.async_call(
        DOMAIN, "watch", {"what": "back door locked", "entity": "back door", "to_state": "locked"}, blocking=True
    )
    res = await hass.services.async_call(DOMAIN, "cancel_watch", {"what": "the back door"}, blocking=True, return_response=True)
    assert res == {"cancelled": "back door locked"}
    freezer.tick(timedelta(minutes=31))
    entry.runtime_data.watches._expire()
    assert not entry.runtime_data.watches.watches


async def test_voice_tools(hass: HomeAssistant) -> None:
    await _setup(hass)
    hass.states.async_set("sensor.washer", "running", {"friendly_name": "Washing machine"})
    ctx = llm.LLMContext(platform="test", context=None, language="en", assistant="conversation", device_id=None)
    api = await llm.async_get_api(hass, "sentient", ctx)
    out = await api.async_call_tool(llm.ToolInput(
        tool_name="sentient_watch",
        tool_args={"what": "washing machine finishing", "entity": "washing machine", "to_state": "off", "until": "6pm"},
    ))
    assert out["watching"]["until"] in ("18:00 today", "18:00 tomorrow")
    out = await api.async_call_tool(llm.ToolInput(tool_name="sentient_watch", tool_args={"what": "x", "entity": "nothing here", "to_state": "on"}))
    assert "can't find" in out["error"]
    api = await llm.async_get_api(hass, "sentient", ctx)
    assert "washing machine finishing" in api.api_prompt
    out = await api.async_call_tool(llm.ToolInput(tool_name="sentient_watches", tool_args={}))
    assert len(out["watches"]) == 1
    out = await api.async_call_tool(llm.ToolInput(tool_name="sentient_cancel_watch", tool_args={"what": "washing"}))
    assert out["cancelled"] == "washing machine finishing"


def test_parse_until() -> None:
    now = dt_util.now()
    t = dt_util.as_local(parse_until("6:30pm", None, 2))
    assert (t.hour, t.minute) == (18, 30) and t > now
    assert parse_until(None, 15, 2) - dt_util.utcnow() < timedelta(minutes=15, seconds=1)
    assert parse_until("nonsense", None, 2) - dt_util.utcnow() > timedelta(hours=1, minutes=59)


async def test_look_tool(hass: HomeAssistant) -> None:
    await _setup(hass)
    _garden_camera(hass)
    asked: list[ServiceCall] = []

    async def generate(call: ServiceCall):
        asked.append(call)
        return {"conversation_id": "x", "data": "Two cats are sunbathing on the lawn."}

    hass.services.async_register("ai_task", "generate_data", generate, supports_response=SupportsResponse.ONLY)
    ctx = llm.LLMContext(platform="test", context=None, language="en", assistant="conversation", device_id=None)
    api = await llm.async_get_api(hass, "sentient", ctx)
    out = await api.async_call_tool(llm.ToolInput(tool_name="sentient_look", tool_args={"camera": "garden", "question": "what's going on?"}))
    assert out == {"camera": "Garden", "answer": "Two cats are sunbathing on the lawn."}
    assert "structure" not in asked[0].data
    out = await api.async_call_tool(llm.ToolInput(tool_name="sentient_look", tool_args={"camera": "attic", "question": "?"}))
    assert "error" in out
