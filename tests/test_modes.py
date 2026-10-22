"""Hush and guest mode."""

from datetime import timedelta

from homeassistant.core import HomeAssistant
from homeassistant.helpers import llm
from homeassistant.util import dt as dt_util
from pytest_homeassistant_custom_component.common import MockConfigEntry, async_fire_time_changed, async_mock_service

from custom_components.sentient.const import DOMAIN

OPTIONS = {"persona": "Nova", "speakers": ["assist_satellite.kitchen"], "default_speaker": "assist_satellite.kitchen",
           "quiet_start": "00:00:00", "quiet_end": "00:00:00"}


async def test_hush_and_guest_mode(hass: HomeAssistant, freezer) -> None:
    entry = MockConfigEntry(domain=DOMAIN, title="Nova", unique_id=DOMAIN, data=OPTIONS)
    entry.add_to_hass(hass)
    assert await hass.config_entries.async_setup(entry.entry_id)
    await hass.async_block_till_done()
    hass.states.async_set("assist_satellite.kitchen", "idle")
    said = async_mock_service(hass, "assist_satellite", "announce")
    speech = entry.runtime_data.speech

    await hass.services.async_call(DOMAIN, "hush", {"minutes": 30}, blocking=True)
    await hass.async_block_till_done()
    assert hass.states.get("switch.nova_hush").state == "on"
    res = await hass.services.async_call(DOMAIN, "say", {"message": "Hello"}, blocking=True, return_response=True)
    assert res["reason"] == "hushed" and not said
    res = await hass.services.async_call(DOMAIN, "say", {"message": "Fire!", "priority": "urgent"}, blocking=True, return_response=True)
    assert res["spoken"]
    assert not speech.quiet_hours_now()  # motion lights aren't affected by a hush

    freezer.tick(timedelta(minutes=31))
    async_fire_time_changed(hass)
    await hass.async_block_till_done()
    assert hass.states.get("switch.nova_hush").state == "off"

    api = await llm.async_get_api(hass, "sentient", llm.LLMContext(platform="t", context=None, language="en", assistant=None, device_id=None))
    await api.async_call_tool(llm.ToolInput(tool_name="sentient_quiet", tool_args={"guests": True}))
    await hass.async_block_till_done()
    assert hass.states.get("switch.nova_guest_mode").state == "on"
    assert "Guests are visiting" in api.api_prompt or speech.guest_mode
    assert entry.runtime_data.noticer._free_rules() == "guest_mode"
    await hass.services.async_call("switch", "turn_off", {"entity_id": "switch.nova_guest_mode"}, blocking=True)
    assert not speech.guest_mode
    texts = [i["text"] for i in entry.runtime_data.timeline.items if i["kind"] == "mode"]
    assert texts[0] == "Guest mode off" and "Guest mode on" in texts and "Talking again" in texts


async def test_chime_setting_and_switch(hass: HomeAssistant) -> None:
    entry = MockConfigEntry(domain=DOMAIN, title="Nova", unique_id=DOMAIN, data=OPTIONS)
    entry.add_to_hass(hass)
    assert await hass.config_entries.async_setup(entry.entry_id)
    await hass.async_block_till_done()
    hass.states.async_set("assist_satellite.kitchen", "idle")
    said = async_mock_service(hass, "assist_satellite", "announce")
    await hass.services.async_call(DOMAIN, "say", {"message": "One"}, blocking=True)
    assert said[-1].data["preannounce"] is True
    await hass.services.async_call("switch", "turn_off", {"entity_id": "switch.nova_chime"}, blocking=True)
    await hass.async_block_till_done()
    assert entry.options["chime"] is False and hass.states.get("switch.nova_chime").state == "off"
    await hass.services.async_call(DOMAIN, "say", {"message": "Two"}, blocking=True)
    assert said[-1].data["preannounce"] is False
