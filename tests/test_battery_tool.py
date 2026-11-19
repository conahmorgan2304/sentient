"""The voice can read the house's battery levels (19 Nov: "how much battery does the back door sensor have left?")."""
from homeassistant.core import HomeAssistant
from homeassistant.helpers import llm
from homeassistant.setup import async_setup_component
from pytest_homeassistant_custom_component.common import MockConfigEntry

from custom_components.sentient.const import DOMAIN
from custom_components.sentient.focus import Focus


async def _api(hass: HomeAssistant):
    assert await async_setup_component(hass, "homeassistant", {})
    for eid, name, level in (("sensor.backdoor_sensor_battery", "backdoor sensor Battery", "75.0"),
                             ("sensor.frontdoor_sensor_battery", "frontdoor sensor Battery", "50.0"),
                             ("sensor.landing_motion_sensor_battery", "landing motion sensor Battery", "100.0")):
        hass.states.async_set(eid, level, {"device_class": "battery", "unit_of_measurement": "%", "friendly_name": name})
    hass.states.async_set("sensor.phone_charge", "unknown", {"device_class": "battery", "friendly_name": "Phone"})
    entry = MockConfigEntry(domain=DOMAIN, title="Nova", unique_id=DOMAIN,
                            data={"persona": "Nova", "quiet_start": "00:00:00", "quiet_end": "00:00:00"})
    entry.add_to_hass(hass)
    assert await hass.config_entries.async_setup(entry.entry_id)
    await hass.async_block_till_done()
    return await llm.async_get_api(hass, DOMAIN, llm.LLMContext(platform="t", context=None, language="en",
                                                                assistant=None, device_id=None))


async def test_one_battery_by_name_and_the_lowest(hass: HomeAssistant) -> None:
    api = await _api(hass)
    out = await api.async_call_tool(llm.ToolInput(tool_name="sentient_batteries", tool_args={"name": "back door"}))
    assert out["batteries"] == [{"name": "backdoor sensor", "level": "75%"}]
    every = await api.async_call_tool(llm.ToolInput(tool_name="sentient_batteries", tool_args={}))
    assert [b["name"] for b in every["batteries"]] == ["frontdoor sensor", "backdoor sensor", "landing motion sensor"]
    none = await api.async_call_tool(llm.ToolInput(tool_name="sentient_batteries", tool_args={"name": "shed"}))
    assert "error" in none


def test_battery_questions_get_the_battery_tool() -> None:
    focus = Focus()
    for asked in ("how much battery does the back door sensor have left", "any batteries running low"):
        assert "sentient_batteries" in (focus.tools_for(asked, asked) or []), asked
