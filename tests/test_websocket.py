"""The sidebar page's Settings tab: read, save and memories over websocket."""

from homeassistant.components.conversation.chat_log import UserContent, async_get_chat_log
from homeassistant.helpers import chat_session
from homeassistant.core import HomeAssistant
from pytest_homeassistant_custom_component.common import MockConfigEntry

from custom_components.sentient.const import DOMAIN


async def _setup(hass: HomeAssistant) -> MockConfigEntry:
    entry = MockConfigEntry(
        domain=DOMAIN, title="Nova", unique_id=DOMAIN,
        data={"persona": "Nova", "speakers": []}, options={"chattiness": "chatty", "default_speaker": "assist_satellite.k"},
    )
    entry.add_to_hass(hass)
    assert await hass.config_entries.async_setup(entry.entry_id)
    await hass.async_block_till_done()
    return entry


async def test_settings_roundtrip(hass: HomeAssistant, hass_ws_client) -> None:
    entry = await _setup(hass)
    ws = await hass_ws_client(hass)
    await ws.send_json({"id": 1, "type": "sentient/settings"})
    res = (await ws.receive_json())["result"]
    ids = [g["id"] for g in res["groups"]]
    assert ids[0] == "voice" and "briefing" in ids
    voice = res["groups"][0]["schema"]
    assert voice[0]["name"] == "persona" and any("selector" in f for f in voice)
    assert res["data"]["chattiness"] == "chatty" and res["data"]["persona"] == "Nova"
    assert res["labels"]["chattiness"] == "How chatty"
    assert res["selector"]["chattiness.options.quiet"] == "Quiet"

    # saving one group keeps the others, and an emptied field is cleared
    await ws.send_json({"id": 2, "type": "sentient/settings/save", "group": "voice",
                        "data": {"persona": "  Orbit ", "chattiness": "quiet", "default_speaker": ""}})
    out = await ws.receive_json()
    assert out["success"], out
    await hass.async_block_till_done()
    assert entry.options["persona"] == "Orbit"
    assert entry.options["chattiness"] == "quiet"
    assert "default_speaker" not in entry.options
    assert entry.runtime_data.options["persona"] == "Orbit"

    await ws.send_json({"id": 3, "type": "sentient/settings/save", "group": "voice", "data": {"chattiness": "loud"}})
    assert not (await ws.receive_json())["success"]


async def test_memories_list_and_edit(hass: HomeAssistant, hass_ws_client) -> None:
    entry = await _setup(hass)
    m = entry.runtime_data.memory.add("Bins go out on Thursday", "routine")
    ws = await hass_ws_client(hass)
    await ws.send_json({"id": 1, "type": "sentient/memories"})
    res = (await ws.receive_json())["result"]
    assert res["items"][0]["text"] == "Bins go out on Thursday" and res["admin"]
    await ws.send_json({"id": 2, "type": "sentient/memory/update", "memory_id": m.id, "text": "Bins go out Wednesday", "category": "fact"})
    assert (await ws.receive_json())["result"]["text"] == "Bins go out Wednesday"
    assert entry.runtime_data.memory.get(m.id).category == "fact"
    await ws.send_json({"id": 3, "type": "sentient/memory/update", "memory_id": "nope", "text": "x"})
    assert (await ws.receive_json())["error"]["code"] == "not_found"


async def test_spots_over_websocket(hass: HomeAssistant, hass_ws_client) -> None:
    entry = await _setup(hass)
    ws = await hass_ws_client(hass)
    await ws.send_json({"id": 1, "type": "sentient/spots/set", "camera": "camera.garden", "name": "The rabbit pen", "box": [0.6, 0.5, 0.2, 0.9]})
    res = await ws.receive_json()
    assert res["success"], res
    assert res["result"]["spots"][0]["box"] == [0.2, 0.5, 0.6, 0.9]
    assert entry.runtime_data.spots.find_in("camera.garden", "is the rabbit pen shut") == "rabbit pen"
    await ws.send_json({"id": 2, "type": "sentient/spots/set", "camera": "camera.garden", "name": "dot", "box": [0.1, 0.1, 0.105, 0.105]})
    assert (await ws.receive_json())["error"]["code"] == "failed"
    await ws.send_json({"id": 3, "type": "sentient/spots/forget", "name": "rabbit pen"})
    res = await ws.receive_json()
    assert res["result"]["result"] is True and res["result"]["spots"] == []


async def test_non_admins_cannot_change_settings_through_say_it_differently(
    hass: HomeAssistant, hass_ws_client, hass_read_only_access_token
) -> None:
    entry = await _setup(hass)
    ws = await hass_ws_client(hass, hass_read_only_access_token)
    await ws.send_json({"id": 1, "type": "sentient/feedback/style", "text": "Hello", "note": "Shorter"})
    out = await ws.receive_json()
    assert not out["success"] and out["error"]["code"] == "unauthorized"
    assert "style_notes" not in entry.options
    await ws.send_json({"id": 2, "type": "sentient/mutes", "clear": True})
    assert (await ws.receive_json())["error"]["code"] == "unauthorized"
    await ws.send_json({"id": 3, "type": "sentient/mutes"})
    assert (await ws.receive_json())["success"]  # reading is fine


def test_wizard_leaves_voice_pipelines_alone_unless_ticked() -> None:
    from pathlib import Path

    js = (Path(__file__).parent.parent / "custom_components/sentient/frontend/sentient-panel.js").read_text()
    assert "backupPipeline: false" in js


async def test_alarms_speak_and_automated_routines_just_happen(hass: HomeAssistant, hass_ws_client) -> None:
    """One store, two pages: silent ones are Automated routines, the rest are Alarms; voice can make either."""
    from homeassistant.helpers import llm

    entry = await _setup(hass)
    hass.states.async_set("switch.heated_blanket", "off", {"friendly_name": "Heated blanket"})
    rt = entry.runtime_data
    rt.alarms.set("Bins", "19:00", ["thu"], description="bin night")
    ws = await hass_ws_client(hass)
    await ws.send_json({"id": 1, "type": "sentient/items/save", "kind": "schedules", "item": {
        "name": "Blanket off", "time": "23:00:00", "days": ["mon"],
        "actions": [{"action": "switch.turn_off", "target": {"entity_id": "switch.heated_blanket"}}]}})
    sched = (await ws.receive_json())["result"]
    assert [i["name"] for i in sched["items"]] == ["Blanket off"] and sched["meta"]["title"] == "Automated routines"
    await ws.send_json({"id": 2, "type": "sentient/items", "kind": "alarms"})
    assert [i["name"] for i in (await ws.receive_json())["result"]["items"]] == ["Bins"]
    api = await llm.async_get_api(hass, "sentient", llm.LLMContext(platform="t", context=None, language="en", assistant=None, device_id=None))
    from homeassistant.components.homeassistant.exposed_entities import async_expose_entity
    from homeassistant.setup import async_setup_component

    assert await async_setup_component(hass, "homeassistant", {})
    async_expose_entity(hass, "conversation", "switch.heated_blanket", True)
    args = {"action": "set", "name": "Blanket on", "time": "21:00", "days": "every day", "turn_on": "heated blanket"}
    with chat_session.async_get_chat_session(hass) as session, async_get_chat_log(hass, session) as chat:
        chat.async_add_user_content(UserContent("Schedule the blanket"))
        out = await api.async_call_tool(llm.ToolInput(tool_name="sentient_alarm", tool_args=args))
        out = getattr(out, "data", out)
        assert out["read_back"] == "'Blanket on': at 21:00 every day, switch on the Heated blanket."
        assert rt.lists["alarms"].find("Blanket on") is None  # nothing saved until they say yes
        chat.async_add_user_content(UserContent("Yes please"))
        out = await api.async_call_tool(llm.ToolInput(tool_name="sentient_alarm", tool_args={**args, "confirmed": True}))
        out = getattr(out, "data", out)
        assert out["kind"] == "automated routine"
        made = rt.lists["alarms"].find("Blanket on")
        assert made["silent"] and made["actions"][0]["action"] == "switch.turn_on"


async def test_saving_part_of_a_group_keeps_the_rest_and_an_empty_value_clears(hass, hass_ws_client) -> None:
    """A save that sends one setting must not reset the others in its group (it did: security incidents and the
    left-open settings went back to defaults when only one switch was sent)."""
    from pytest_homeassistant_custom_component.common import MockConfigEntry

    from custom_components.sentient.const import DOMAIN

    entry = MockConfigEntry(domain=DOMAIN, title="Nova", unique_id=DOMAIN, data={"persona": "Nova"},
                            options={"security_incidents": True, "left_open_minutes": 15,
                                     "left_open_ignore": ["binary_sensor.window"]})
    entry.add_to_hass(hass)
    assert await hass.config_entries.async_setup(entry.entry_id)
    await hass.async_block_till_done()
    ws = await hass_ws_client(hass)
    await ws.send_json({"id": 1, "type": "sentient/settings/save", "group": "safety", "data": {"cognitive_shadow": True}})
    assert (await ws.receive_json())["success"]
    opts = entry.options
    assert opts["cognitive_shadow"] is True and opts["security_incidents"] is True
    assert opts["left_open_minutes"] == 15 and opts["left_open_ignore"] == ["binary_sensor.window"]
    await ws.send_json({"id": 2, "type": "sentient/settings/save", "group": "safety", "data": {"left_open_ignore": ""}})
    assert (await ws.receive_json())["success"]
    assert not entry.options.get("left_open_ignore") and entry.options["left_open_minutes"] == 15
