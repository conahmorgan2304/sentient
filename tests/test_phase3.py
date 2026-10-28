"""Phase 3: rules made by voice (read back, made only after a yes), names from face recognition, and the
optional stronger AI — each working when on and doing nothing when off."""

from unittest.mock import patch

from homeassistant.core import HomeAssistant
from homeassistant.helpers import llm
from pytest_homeassistant_custom_component.common import MockConfigEntry, async_mock_service

from custom_components.sentient.const import DOMAIN

OPTIONS = {"persona": "Nova", "speakers": ["assist_satellite.kitchen"], "default_speaker": "assist_satellite.kitchen",
           "quiet_start": "00:00:00", "quiet_end": "00:00:00"}


async def _setup(hass: HomeAssistant, **extra) -> MockConfigEntry:
    hass.states.async_set("assist_satellite.kitchen", "idle", {"supported_features": 3})
    entry = MockConfigEntry(domain=DOMAIN, title="Nova", unique_id=DOMAIN, data={**OPTIONS, **extra})
    entry.add_to_hass(hass)
    assert await hass.config_entries.async_setup(entry.entry_id)
    await hass.async_block_till_done()
    return entry


async def _api(hass: HomeAssistant):
    return await llm.async_get_api(hass, DOMAIN, llm.LLMContext(platform="t", context=None, language="en",
                                                                assistant=None, device_id=None))


RULE = {"name": "Back door at night", "when": "back door", "becomes": "opens", "after_dark": True,
        "do": [{"action": "light.turn_on", "target": "kitchen light", "brightness_pct": 30}]}


async def test_rule_by_voice_read_back_then_made_then_runs(hass: HomeAssistant) -> None:
    hass.states.async_set("binary_sensor.back_door", "off", {"friendly_name": "Back door", "device_class": "door"})
    hass.states.async_set("light.kitchen", "off", {"friendly_name": "Kitchen light"})
    hass.states.async_set("sun.sun", "below_horizon")
    entry = await _setup(hass, rules=True)
    rt = entry.runtime_data
    api = await _api(hass)
    out = await api.async_call_tool(llm.ToolInput(tool_name="sentient_make_rule", tool_args=RULE))
    assert out["read_back"] == "When Back door turns on or opens, after dark: turn on Kitchen light at 30%."
    assert rt.lists["rules"].listing() == []  # nothing made until a yes
    out = await api.async_call_tool(llm.ToolInput(tool_name="sentient_make_rule", tool_args={**RULE, "confirmed": True}))
    assert "made" in out and rt.lists["rules"].listing()[0]["made_by"] == "voice"
    on = async_mock_service(hass, "light", "turn_on")
    hass.states.async_set("binary_sensor.back_door", "on", {"friendly_name": "Back door", "device_class": "door"})
    await hass.async_block_till_done()
    await hass.async_block_till_done()
    assert on and on[-1].data["brightness_pct"] == 30
    # in daylight it doesn't run
    hass.states.async_set("binary_sensor.back_door", "off", {"friendly_name": "Back door"})
    hass.states.async_set("sun.sun", "above_horizon")
    rt.lists["rules"].update(rt.lists["rules"].listing()[0]["id"], last=None)
    hass.states.async_set("binary_sensor.back_door", "on", {"friendly_name": "Back door"})
    await hass.async_block_till_done()
    assert len(on) == 1


async def test_rules_never_touch_the_alarm(hass: HomeAssistant) -> None:
    hass.states.async_set("binary_sensor.back_door", "off", {"friendly_name": "Back door"})
    hass.states.async_set("alarm_control_panel.house", "armed_away", {"friendly_name": "House alarm"})
    await _setup(hass, rules=True)
    api = await _api(hass)
    out = await api.async_call_tool(llm.ToolInput(tool_name="sentient_make_rule", tool_args={
        "name": "x", "when": "back door", "do": [{"action": "alarm_control_panel.alarm_disarm", "target": "house alarm"}]}))
    assert "can't" in out["error"]


async def test_rules_off(hass: HomeAssistant) -> None:
    hass.states.async_set("binary_sensor.back_door", "off", {"friendly_name": "Back door"})
    hass.states.async_set("light.kitchen", "off", {"friendly_name": "Kitchen light"})
    hass.states.async_set("sun.sun", "below_horizon")
    entry = await _setup(hass)
    api = await _api(hass)
    assert "sentient_make_rule" not in [t.name for t in api.tools]  # the voice can't make them
    entry.runtime_data.lists["rules"].upsert({"name": "r", "when_entity": "binary_sensor.back_door", "when_to": "on",
                                              "actions": [{"action": "light.turn_on", "target": {"entity_id": "light.kitchen"}}]})
    on = async_mock_service(hass, "light", "turn_on")
    hass.states.async_set("binary_sensor.back_door", "on", {"friendly_name": "Back door"})
    await hass.async_block_till_done()
    assert not on  # and none run


async def test_faces_named_privately(hass: HomeAssistant) -> None:
    hass.states.async_set("sensor.frigate_corey_last_camera", "None", {"friendly_name": "Frigate Corey Last Camera"})
    hass.states.async_set("sensor.frigate_nan_last_camera", "None", {"friendly_name": "Frigate Nan Last Camera"})
    entry = await _setup(hass, faces=True, face_people=["Corey"])
    said = async_mock_service(hass, "assist_satellite", "announce")
    hass.states.async_set("sensor.frigate_corey_last_camera", "gate", {"friendly_name": "Frigate Corey Last Camera"})
    hass.states.async_set("sensor.frigate_nan_last_camera", "gate", {"friendly_name": "Frigate Nan Last Camera"})
    await hass.async_block_till_done()
    rt = entry.runtime_data
    assert [s["name"] for s in rt.faces.recent()] == ["Corey"]  # only the people chosen
    assert any(i["text"] == "Corey seen at the gate" for i in rt.timeline.items)
    assert not said  # never announced
    api = await _api(hass)
    assert "Corey at the gate" in api.api_prompt and "never announce it" in api.api_prompt


async def test_faces_off(hass: HomeAssistant) -> None:
    hass.states.async_set("sensor.frigate_corey_last_camera", "None", {"friendly_name": "Frigate Corey Last Camera"})
    entry = await _setup(hass)
    hass.states.async_set("sensor.frigate_corey_last_camera", "gate", {"friendly_name": "Frigate Corey Last Camera"})
    await hass.async_block_till_done()
    assert entry.runtime_data.faces.recent() == []
    assert "Recognised by the cameras" not in (await _api(hass)).api_prompt


async def test_stronger_ai_for_security_only_when_chosen(hass: HomeAssistant) -> None:
    entry = await _setup(hass, ai_task_entity="ai_task.usual")
    sent = []

    async def fake(_hass, data):
        sent.append(data.get("entity_id"))
        return {"data": {"person": True, "description": "Someone at the shed."}}

    sec = entry.runtime_data.camsec
    with patch("custom_components.sentient.camsecurity.async_generate", fake):
        await sec._describe("camera.shed")
        sec.options = {**sec.options, "smart_ai_task": "ai_task.smart"}
        await sec._describe("camera.shed")
    assert sent == ["ai_task.usual", "ai_task.smart"]


async def test_rules_page_lists_and_saves(hass: HomeAssistant, hass_ws_client) -> None:
    hass.states.async_set("binary_sensor.back_door", "off", {"friendly_name": "Back door"})
    await _setup(hass, rules=True)
    ws = await hass_ws_client(hass)
    await ws.send_json({"id": 1, "type": "sentient/items", "kind": "rules"})
    reply = await ws.receive_json()
    assert reply["success"] and reply["result"]["meta"]["title"] == "Rules"
    await ws.send_json({"id": 2, "type": "sentient/items/save", "kind": "rules",
                        "item": {"name": "Door", "when_entity": "binary_sensor.back_door", "when_to": "on"}})
    assert (await ws.receive_json())["success"]
