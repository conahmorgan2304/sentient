"""Security check and scenes tool."""

from homeassistant.core import HomeAssistant
from homeassistant.helpers import llm
from pytest_homeassistant_custom_component.common import MockConfigEntry, async_mock_service

from custom_components.sentient.const import DOMAIN
from custom_components.sentient.security import check


async def test_security_report(hass: HomeAssistant) -> None:
    assert check(hass)["secure"] is None
    assert "Aucun capteur de sécurité disponible" in check(hass)["summary"]
    hass.states.async_set("binary_sensor.back_door", "on", {"device_class": "door", "friendly_name": "Back door"})
    hass.states.async_set("lock.front", "unlocked", {"friendly_name": "Front lock"})
    hass.states.async_set("alarm_control_panel.alarmo", "disarmed", {"friendly_name": "Alarm"})
    r = check(hass)
    assert not r["secure"]
    assert r["summary"] == "Back door is open; Front lock is unlocked. The alarm is off."


async def test_scene_tool(hass: HomeAssistant) -> None:
    entry = MockConfigEntry(domain=DOMAIN, title="Nova", unique_id=DOMAIN, data={"persona": "Nova", "speakers": []})
    entry.add_to_hass(hass)
    assert await hass.config_entries.async_setup(entry.entry_id)
    await hass.async_block_till_done()
    hass.states.async_set("scene.movie_night", "unknown", {"friendly_name": "Movie night", "entity_id": ["light.lamp"]})
    hass.states.async_set("light.lamp", "off", {"friendly_name": "Lamp"})
    on = async_mock_service(hass, "scene", "turn_on")
    api = await llm.async_get_api(hass, "sentient", llm.LLMContext(platform="t", context=None, language="en", assistant=None, device_id=None))
    res = await api.async_call_tool(llm.ToolInput(tool_name="sentient_scene", tool_args={}))
    assert getattr(res, "data", res)["scenes"] == [{"name": "Movie night", "contains": ["Lamp"]}]
    res = await api.async_call_tool(llm.ToolInput(tool_name="sentient_scene", tool_args={"name": "movie"}))
    assert getattr(res, "data", res)["scene_on"] == "Movie night" and on[0].data["entity_id"] == "scene.movie_night"
