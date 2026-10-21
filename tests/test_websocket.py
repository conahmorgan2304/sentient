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
