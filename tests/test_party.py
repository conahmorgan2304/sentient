"""Disco mode: lights put on a show, then go back exactly as they were."""

from datetime import datetime, timedelta

from homeassistant.core import HomeAssistant
from homeassistant.helpers import area_registry as ar, entity_registry as er, llm
from homeassistant.util import dt as dt_util
from pytest_homeassistant_custom_component.common import (
    MockConfigEntry,
    async_fire_time_changed,
    async_mock_service,
)

from custom_components.sentient.const import DOMAIN


async def _setup(hass: HomeAssistant, freezer, **extra) -> MockConfigEntry:
    freezer.move_to(dt_util.as_utc(datetime(2026, 10, 24, 20, 0, tzinfo=dt_util.get_default_time_zone())))
    hass.states.async_set("light.lamp", "on", {
        "supported_color_modes": ["color_temp", "hs"], "color_mode": "color_temp",
        "brightness": 120, "color_temp_kelvin": 2700, "friendly_name": "Lamp",
    })
    hass.states.async_set("light.ceiling", "off", {"supported_color_modes": ["brightness"], "friendly_name": "Ceiling"})
    entry = MockConfigEntry(domain=DOMAIN, title="Nova", unique_id=DOMAIN, data={
        "persona": "Nova", "party_lights": ["light.lamp", "light.ceiling"], "party_minutes": 30, **extra,
    })
    entry.add_to_hass(hass)
    assert await hass.config_entries.async_setup(entry.entry_id)
    await hass.async_block_till_done()
    return entry


async def test_show_then_lights_go_back_as_they_were(hass: HomeAssistant, freezer) -> None:
    entry = await _setup(hass, freezer)
    on = async_mock_service(hass, "light", "turn_on")
    off = async_mock_service(hass, "light", "turn_off")
    party = entry.runtime_data.party
    result = await party.async_party("disco")
    await hass.async_block_till_done()
    assert result["on"] and result["style"] == "disco" and result["until"]
    assert hass.states.get("switch.nova_disco_mode").state == "on"
    lamp = [c for c in on if c.data["entity_id"] == "light.lamp"]
    ceiling = [c for c in on if c.data["entity_id"] == "light.ceiling"]
    assert "hs_color" in lamp[0].data  # colour light changes colour
    assert "hs_color" not in ceiling[0].data and ceiling[0].data["brightness_pct"] == 100  # white light pulses
    # the show keeps going
    before = len(on)
    freezer.tick(timedelta(seconds=2))
    async_fire_time_changed(hass)
    await hass.async_block_till_done()
    assert len(on) > before
    # the house's own changes don't count as someone at a switch
    assert all(c.context.parent_id for c in on)
    # time's up: the lamp goes back to warm white at its old brightness, the ceiling light off
    on.clear()
    freezer.tick(timedelta(minutes=31))
    async_fire_time_changed(hass)
    await hass.async_block_till_done()
    assert not party.active
    back = [c for c in on if c.data["entity_id"] == "light.lamp"][-1].data
    assert back["brightness"] == 120 and back["color_temp_kelvin"] == 2700
    assert any(c.data["entity_id"] == "light.ceiling" for c in off)
    assert any(i["text"] == "Disco mode off" for i in entry.runtime_data.timeline.items)


async def test_motion_lights_leave_party_lights_alone(hass: HomeAssistant, freezer) -> None:
    entry = await _setup(hass, freezer)
    hass.states.async_set("sun.sun", "below_horizon")
    hass.states.async_set("binary_sensor.motion", "off")
    ml = entry.runtime_data.motion_lights
    ml.upsert({"light": "light.ceiling", "motion": ["binary_sensor.motion"], "off_after": 1})
    async_mock_service(hass, "light", "turn_on")
    async_mock_service(hass, "light", "turn_off")
    await entry.runtime_data.party.async_party("chill")
    assert "light.ceiling" in ml.paused
    hass.states.async_set("binary_sensor.motion", "on")
    await hass.async_block_till_done()
    assert ml.listing()["rules"][0]["managing"] is False
    await entry.runtime_data.party.async_end()
    assert "light.ceiling" not in ml.paused


async def test_a_restart_mid_party_still_puts_lights_back(hass: HomeAssistant, freezer) -> None:
    entry = await _setup(hass, freezer)
    async_mock_service(hass, "light", "turn_on")
    await entry.runtime_data.party.async_party("rainbow")
    await hass.async_block_till_done()
    await hass.config_entries.async_unload(entry.entry_id)
    await hass.async_block_till_done()
    off = async_mock_service(hass, "light", "turn_off")
    assert await hass.config_entries.async_setup(entry.entry_id)
    await hass.async_block_till_done()
    assert any(c.data["entity_id"] == "light.ceiling" for c in off)
    assert not entry.runtime_data.party.active


async def test_by_voice_in_a_named_room(hass: HomeAssistant, freezer) -> None:
    entry = await _setup(hass, freezer, party_lights=[])
    room = ar.async_get(hass).async_create("Living Room")
    ents = er.async_get(hass)
    ents.async_get_or_create("light", "test", "lr", suggested_object_id="living_strip")
    ents.async_update_entity("light.living_strip", area_id=room.id)
    hass.states.async_set("light.living_strip", "off", {"supported_color_modes": ["rgb"]})
    async_mock_service(hass, "light", "turn_on")
    api = await llm.async_get_api(hass, DOMAIN, llm.LLMContext(
        platform="test", context=None, language="en", assistant="conversation", device_id=None,
    ))
    out = await api.async_call_tool(llm.ToolInput(tool_name="sentient_party", tool_args={"action": "start", "area": "living room"}))
    assert out["on"] and entry.runtime_data.party.lights == ["light.living_strip"]
    out = await api.async_call_tool(llm.ToolInput(tool_name="sentient_party", tool_args={"action": "start"}))
    assert "error" in out  # no disco lights set and no room to go on
    out = await api.async_call_tool(llm.ToolInput(tool_name="sentient_party", tool_args={"action": "stop"}))
    assert out["on"] is False
