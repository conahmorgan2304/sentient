"""Room profiles: private, gentle, own volume, own quiet time and no offers — rooms without one as before."""

from homeassistant.core import HomeAssistant
from homeassistant.helpers import area_registry as ar, device_registry as dr, entity_registry as er, llm
from pytest_homeassistant_custom_component.common import MockConfigEntry, async_mock_service

from custom_components.sentient.const import DOMAIN

OPTIONS = {"persona": "Nova", "speakers": ["assist_satellite.girls", "assist_satellite.kitchen"],
           "default_speaker": "assist_satellite.kitchen", "quiet_start": "00:00:00", "quiet_end": "00:00:00"}


async def _setup(hass: HomeAssistant):
    areas = ar.async_get(hass)
    girls, kitchen = areas.async_create("Girls' room"), areas.async_create("Kitchen")
    other = MockConfigEntry(domain="test")
    other.add_to_hass(hass)
    devices, ents = dr.async_get(hass), er.async_get(hass)
    for name, area in (("girls", girls), ("kitchen", kitchen)):
        device = devices.async_get_or_create(config_entry_id=other.entry_id, identifiers={("test", name)})
        devices.async_update_device(device.id, area_id=area.id)
        ents.async_get_or_create("assist_satellite", "test", name, suggested_object_id=name, device_id=device.id)
        hass.states.async_set(f"assist_satellite.{name}", "idle", {"friendly_name": name.title()})
    entry = MockConfigEntry(domain=DOMAIN, title="Nova", unique_id=DOMAIN, data=OPTIONS)
    entry.add_to_hass(hass)
    assert await hass.config_entries.async_setup(entry.entry_id)
    await hass.async_block_till_done()
    girls_device = devices.async_get_device(identifiers={("test", "girls")})
    return entry, girls.id, girls_device.id


async def test_room_quiet_volume_and_private(hass: HomeAssistant) -> None:
    entry, girls, girls_device = await _setup(hass)
    rt = entry.runtime_data
    rt.lists["rooms"].upsert({"name": "Girls", "areas": [girls], "private": True, "gentle": True, "volume": 30,
                              "quiet": "00:00-23:59", "offers": False})
    said = async_mock_service(hass, "assist_satellite", "announce")
    rt.memory.add("Sam's surprise party is on Saturday.")
    # quiet there: a normal remark goes elsewhere, never to that room; urgent still does
    out = await rt.speech.async_say("The washing's done.", "normal", ["assist_satellite.girls", "assist_satellite.kitchen"])
    assert out.speakers == ["assist_satellite.kitchen"]
    held = await rt.speech.async_say("The washing's done again.", "normal", ["assist_satellite.girls"])
    assert held.reason == "room_quiet"
    urgent = await rt.speech.async_say("The back door is open.", "urgent", ["assist_satellite.girls"])
    assert urgent.spoken
    assert rt.profiles.volume("assist_satellite.girls") == 0.3 and rt.profiles.volume("assist_satellite.kitchen") is None
    # questions aren't asked there
    assert rt.profiles.allowed(["assist_satellite.girls"], "normal", "offer:Brew") == []
    # the voice in that room: no personal memories, a gentler manner
    api = await llm.async_get_api(hass, DOMAIN, llm.LLMContext(platform="t", context=None, language="en",
                                                               assistant=None, device_id=girls_device))
    assert "surprise party" not in api.api_prompt and "This room is private" in api.api_prompt
    assert "speak gently" in api.api_prompt
    found = await api.async_call_tool(llm.ToolInput(tool_name="sentient_recall", tool_args={"query": "party"}))
    assert getattr(found, "data", found)["memories"] == []
    elsewhere = await llm.async_get_api(hass, DOMAIN, llm.LLMContext(platform="t", context=None, language="en",
                                                                     assistant=None, device_id=None))
    assert "surprise party" in elsewhere.api_prompt
    assert said
