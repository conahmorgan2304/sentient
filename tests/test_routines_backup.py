"""Named routines and backup."""

from homeassistant.core import HomeAssistant
from homeassistant.helpers import llm
from pytest_homeassistant_custom_component.common import MockConfigEntry, async_mock_service

from custom_components.sentient.const import DOMAIN

OPTIONS = {"persona": "Nova", "speakers": ["assist_satellite.kitchen"], "default_speaker": "assist_satellite.kitchen",
           "quiet_start": "21:00:00", "quiet_end": "08:00:00"}


async def _setup(hass):
    entry = MockConfigEntry(domain=DOMAIN, title="Nova", unique_id=DOMAIN, data=OPTIONS)
    entry.add_to_hass(hass)
    assert await hass.config_entries.async_setup(entry.entry_id)
    await hass.async_block_till_done()
    hass.states.async_set("assist_satellite.kitchen", "idle")
    return entry


async def test_goodnight(hass: HomeAssistant, hass_ws_client) -> None:
    entry = await _setup(hass)
    off = async_mock_service(hass, "light", "turn_off")
    said = async_mock_service(hass, "assist_satellite", "announce")
    hass.states.async_set("binary_sensor.back_door", "on", {"device_class": "door", "friendly_name": "Back door"})
    hass.states.async_set("light.landing", "on", {"friendly_name": "Landing"})
    ws = await hass_ws_client(hass)
    await ws.send_json({"id": 1, "type": "sentient/shortcuts/save", "item": {
        "name": "Goodnight", "aliases": "bedtime, night night", "check_open": True, "check_lights": True,
        "say": "Goodnight.", "actions": [{"action": "light.turn_off", "target": {"entity_id": "light.landing"}}]}})
    res = await ws.receive_json()
    assert res["success"], res
    await ws.send_json({"id": 2, "type": "sentient/shortcuts/save", "item": {"name": "bad", "actions": [{"nonsense": 1}]}})
    assert not (await ws.receive_json())["success"]

    out = await hass.services.async_call(DOMAIN, "run_routine", {"name": "bedtime"}, blocking=True, return_response=True)
    assert len(off) == 1
    assert out["said"] == "Goodnight. Back door is still open. Landing is still on."
    assert said[-1].data["message"] == out["said"]  # spoken even in quiet hours: someone asked

    api = await llm.async_get_api(hass, "sentient", llm.LLMContext(platform="t", context=None, language="en", assistant=None, device_id=None))
    assert "Goodnight" in api.api_prompt
    res = await api.async_call_tool(llm.ToolInput(tool_name="sentient_run_routine", tool_args={"name": "goodnight routine"}))
    assert getattr(res, "data", res)["routine"] == "Goodnight" and len(off) == 2


async def test_backup_round_trip(hass: HomeAssistant, hass_ws_client) -> None:
    entry = await _setup(hass)
    rt = entry.runtime_data
    rt.memory.add("The cats are called Cookie and Daisy", "fact")
    rt.spots.set("camera.garden", "rabbit pen", [0.1, 0.1, 0.5, 0.5])
    rt.motion_lights.upsert({"light": "light.landing", "motion": ["binary_sensor.landing_motion"]})
    rt.shortcuts.upsert({"name": "Goodnight", "say": "Night."})
    rt.mutes.add("washing machine", None)
    ws = await hass_ws_client(hass)
    await ws.send_json({"id": 1, "type": "sentient/export"})
    backup = (await ws.receive_json())["result"]
    assert backup["memories"][0]["text"].startswith("The cats")

    # wipe, then restore
    rt.memory._items.clear(); rt.spots.spots.clear(); rt.motion_lights.rules.clear(); rt.shortcuts.items.clear(); rt.mutes.items.clear()
    await ws.send_json({"id": 2, "type": "sentient/import", "data": backup})
    counts = (await ws.receive_json())["result"]
    assert counts == {"memories": 1, "spots": 1, "motion_lights": 1, "routines": 1, "shushed": 1, "learned_choices": 0}
    await ws.send_json({"id": 3, "type": "sentient/import", "data": backup})
    assert (await ws.receive_json())["result"]["memories"] == 0  # adding twice doesn't duplicate
    await ws.send_json({"id": 4, "type": "sentient/import", "data": {"nope": 1}})
    assert (await ws.receive_json())["error"]["code"] == "invalid"
