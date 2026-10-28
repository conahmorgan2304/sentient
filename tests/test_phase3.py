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


async def test_relevant_memories_when_there_are_many(hass: HomeAssistant) -> None:
    from homeassistant.components.conversation.chat_log import current_chat_log
    from types import SimpleNamespace

    entry = await _setup(hass, prompt_memories=4)
    rt = entry.runtime_data
    rt.memory.add("The rabbits are called Bun and Hop", "household")
    for i in range(8):
        rt.memory.add(f"Note number {i} about the kitchen", "household")
    log = SimpleNamespace(content=[SimpleNamespace(role="user", content="what are the rabbits called?")], conversation_id="c")
    token = current_chat_log.set(log)
    try:
        prompt = (await _api(hass)).api_prompt
    finally:
        current_chat_log.reset(token)
    assert "Bun and Hop" in prompt  # matched what was asked, though it's the oldest
    api = await _api(hass)
    old = [m for m in rt.memory.recent(24) if m.category != "correction"][:4]
    assert api.api._memories(rt, 4, False) == old  # off: exactly as before


async def test_known_cars_named_from_the_same_look(hass: HomeAssistant) -> None:
    from homeassistant.core import ServiceCall, SupportsResponse

    hass.states.async_set("camera.gate", "idle")
    entry = await _setup(hass, known_cars="Corey: dark grey hatchback\nNan: red Fiat")
    rt = entry.runtime_data
    rt.spots.set("camera.gate", "drive", [0.1, 0.4, 0.6, 0.9])
    replies = iter([{"cars": 0}, {"cars": 1, "description": "dark grey hatchback", "whose": "Corey"}])
    calls = []

    async def generate(call: ServiceCall):
        calls.append(call)
        return {"conversation_id": "x", "data": next(replies)}

    hass.services.async_register("ai_task", "generate_data", generate, supports_response=SupportsResponse.ONLY)
    await rt.drive.async_check(force=True)
    await rt.drive.async_check(force=True)
    assert "Corey — dark grey hatchback" in calls[-1].data["instructions"]
    assert rt.timeline.items[0]["text"] == "Corey's car arrived on the drive — now dark grey hatchback"
    assert "(Corey's)" in rt.drive.prompt_line()


async def test_no_known_cars_no_names(hass: HomeAssistant) -> None:
    from homeassistant.core import ServiceCall, SupportsResponse

    hass.states.async_set("camera.gate", "idle")
    entry = await _setup(hass)
    rt = entry.runtime_data
    rt.spots.set("camera.gate", "drive", [0.1, 0.4, 0.6, 0.9])
    calls = []

    async def generate(call: ServiceCall):
        calls.append(call)
        return {"conversation_id": "x", "data": {"cars": 1, "description": "dark grey hatchback"}}

    hass.services.async_register("ai_task", "generate_data", generate, supports_response=SupportsResponse.ONLY)
    await rt.drive.async_check(force=True)
    assert "whose" not in calls[-1].data["structure"] and "Known household" not in calls[-1].data["instructions"]


async def test_event_clip_watched(hass: HomeAssistant, aioclient_mock, tmp_path) -> None:
    from homeassistant.core import ServiceCall, SupportsResponse

    hass.config.media_dirs = {"local": str(tmp_path)}
    MockConfigEntry(domain="frigate", data={"url": "http://frigate:5000"}).add_to_hass(hass)
    hass.states.async_set("camera.front_door", "idle", {"friendly_name": "Front door"})
    entry = await _setup(hass, event_clips=True)
    aioclient_mock.get("http://frigate:5000/api/events", json=[{"id": "abc.123", "start_time": 1793190000, "label": "person"}])
    aioclient_mock.get("http://frigate:5000/api/events/abc.123/clip.mp4", content=b"\x00\x00\x00 ftypmp4 fake clip")
    sent = []

    async def generate(call: ServiceCall):
        sent.append(call)
        return {"conversation_id": "x", "data": "A courier left a parcel by the door and went."}

    hass.services.async_register("ai_task", "generate_data", generate, supports_response=SupportsResponse.ONLY)
    api = await _api(hass)
    out = await api.async_call_tool(llm.ToolInput(tool_name="sentient_what_happened", tool_args={"camera": "front door"}))
    assert out["what_happened"] == "A courier left a parcel by the door and went."
    assert sent[0].data["attachments"][0]["media_content_type"] == "video/mp4"
    assert (tmp_path / "sentient" / "clip_front_door.mp4").exists()


async def test_event_clips_off(hass: HomeAssistant) -> None:
    entry = await _setup(hass)
    api = await _api(hass)
    assert "sentient_what_happened" not in [t.name for t in api.tools]
    assert "skipped" in await entry.runtime_data.clips.async_what_happened("camera.front_door")
