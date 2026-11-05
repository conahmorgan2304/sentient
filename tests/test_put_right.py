"""'No, the other one': the voice switches back what it just switched, then does the corrected request."""

from datetime import timedelta

from homeassistant.core import HomeAssistant
from homeassistant.helpers import llm
from pytest_homeassistant_custom_component.common import MockConfigEntry, async_mock_service

from custom_components.sentient.const import DOMAIN

OPTIONS = {"persona": "Nova", "speakers": ["assist_satellite.kitchen"], "default_speaker": "assist_satellite.kitchen",
           "quiet_start": "00:00:00", "quiet_end": "00:00:00"}


async def _setup(hass: HomeAssistant, **extra) -> MockConfigEntry:
    entry = MockConfigEntry(domain=DOMAIN, title="Nova", unique_id=DOMAIN, data={**OPTIONS, **extra})
    entry.add_to_hass(hass)
    assert await hass.config_entries.async_setup(entry.entry_id)
    await hass.async_block_till_done()
    return entry


async def _api(hass: HomeAssistant, device_id: str | None = "sat1"):
    return await llm.async_get_api(hass, DOMAIN, llm.LLMContext(platform="t", context=None, language="en",
                                                               assistant=None, device_id=device_id))


async def test_undo_what_the_voice_just_switched(hass: HomeAssistant, freezer) -> None:
    hass.states.async_set("light.ceiling", "off", {"friendly_name": "Ceiling light"})
    entry = await _setup(hass)
    guard = entry.runtime_data.conversations.guard
    hass.states.async_set("light.ceiling", "on", {"friendly_name": "Ceiling light"})  # the voice got it wrong
    await hass.async_block_till_done()
    guard.note(["conv1", "sat1"], [{"type": "entity", "id": "light.ceiling", "name": "Ceiling light"}])
    off = async_mock_service(hass, "homeassistant", "turn_off")
    api = await _api(hass)
    assert "sentient_undo_last" in [t.name for t in api.tools]
    out = await api.async_call_tool(llm.ToolInput(tool_name="sentient_undo_last", tool_args={}))
    assert out == {"undone": ["Ceiling light"]} and off[0].data["entity_id"] == "light.ceiling"
    out = await api.async_call_tool(llm.ToolInput(tool_name="sentient_undo_last", tool_args={}))
    assert out["undone"] == []  # only once


async def test_not_after_a_few_minutes_or_when_off(hass: HomeAssistant, freezer) -> None:
    hass.states.async_set("light.ceiling", "off", {"friendly_name": "Ceiling light"})
    entry = await _setup(hass)
    guard = entry.runtime_data.conversations.guard
    hass.states.async_set("light.ceiling", "on", {"friendly_name": "Ceiling light"})
    await hass.async_block_till_done()
    guard.note(["sat1"], [{"type": "entity", "id": "light.ceiling"}])
    freezer.tick(timedelta(minutes=4))
    assert await guard.async_undo(["sat1"]) == []
    hass.config_entries.async_update_entry(entry, options={**OPTIONS, "put_right": False})
    await hass.async_block_till_done()
    assert "sentient_undo_last" not in [t.name for t in (await _api(hass)).tools]
