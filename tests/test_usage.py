"""How it's used: features counted from voice tools and what's said; on-but-unused listed."""

from homeassistant.core import HomeAssistant
from homeassistant.helpers import llm
from pytest_homeassistant_custom_component.common import MockConfigEntry, async_mock_service

from custom_components.sentient.const import DOMAIN

OPTIONS = {"persona": "Nova", "speakers": ["assist_satellite.kitchen"], "default_speaker": "assist_satellite.kitchen",
           "quiet_start": "00:00:00", "quiet_end": "00:00:00", "briefings": True, "curiosity": True}


async def test_usage(hass: HomeAssistant, hass_ws_client) -> None:
    hass.states.async_set("assist_satellite.kitchen", "idle", {"friendly_name": "Kitchen"})
    entry = MockConfigEntry(domain=DOMAIN, title="Nova", unique_id=DOMAIN, data=OPTIONS)
    entry.add_to_hass(hass)
    assert await hass.config_entries.async_setup(entry.entry_id)
    await hass.async_block_till_done()
    rt = entry.runtime_data
    async_mock_service(hass, "assist_satellite", "announce")
    api = await llm.async_get_api(hass, DOMAIN, llm.LLMContext(platform="t", context=None, language="en",
                                                               assistant=None, device_id=None))
    await api.async_call_tool(llm.ToolInput(tool_name="sentient_watches", tool_args={}))  # not a counted feature
    await api.async_call_tool(llm.ToolInput(tool_name="sentient_remind", tool_args={"message": "Ring Mum", "minutes": 30}))
    rt.lists["alarms"].upsert({"name": "Bins", "time": "19:00:00"})
    await rt.speech.async_say("The bins go out tonight.", "normal", None, source="alarm:Bins")
    await hass.async_block_till_done()
    client = await hass_ws_client(hass)
    await client.send_json_auto_id({"type": "sentient/usage"})
    out = (await client.receive_json())["result"]
    used = {r["id"]: r["used_this_month"] for r in out["used"]}
    assert used.get("watches") == 1 and used.get("alarms") == 1
    never = {r["id"] for r in out["never"]}
    assert {"briefings", "curiosity"} <= never  # switched on, never used
    assert "books" in {r["id"] for r in out["off"]}
