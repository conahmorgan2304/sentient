"""The sidebar page's Settings tab: read, save and memories over websocket."""

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
