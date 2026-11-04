"""Maintenance counted by use: due after so many hours running, said once, reset by voice."""

from datetime import timedelta

from homeassistant.core import HomeAssistant
from homeassistant.helpers import llm
from homeassistant.util import dt as dt_util
from pytest_homeassistant_custom_component.common import MockConfigEntry, async_mock_service

from custom_components.sentient.const import DOMAIN
from custom_components.sentient.upkeep import gather_briefing

OPTIONS = {"persona": "Nova", "speakers": ["assist_satellite.kitchen"], "default_speaker": "assist_satellite.kitchen",
           "quiet_start": "00:00:00", "quiet_end": "00:00:00"}


async def test_filter_due_by_use_then_done(hass: HomeAssistant, freezer) -> None:
    freezer.move_to(dt_util.start_of_local_day() + timedelta(hours=10))
    hass.states.async_set("assist_satellite.kitchen", "idle", {"friendly_name": "Kitchen"})
    hass.states.async_set("fan.purifier", "off", {"friendly_name": "Purifier"})
    entry = MockConfigEntry(domain=DOMAIN, title="Nova", unique_id=DOMAIN, data=OPTIONS)
    entry.add_to_hass(hass)
    assert await hass.config_entries.async_setup(entry.entry_id)
    await hass.async_block_till_done()
    rt = entry.runtime_data
    rt.lists["maintenance"].upsert({"name": "Purifier filter", "entity": "fan.purifier", "every_hours": 5})
    rt.tracker.best_area = lambda: "kitchen"
    said = async_mock_service(hass, "assist_satellite", "announce")
    hass.states.async_set("fan.purifier", "on", {"friendly_name": "Purifier"})
    await hass.async_block_till_done()
    freezer.tick(timedelta(hours=3))
    hass.states.async_set("fan.purifier", "off", {"friendly_name": "Purifier"})
    await hass.async_block_till_done()
    assert not rt.maintenance.due()
    hass.states.async_set("fan.purifier", "on", {"friendly_name": "Purifier"})
    await hass.async_block_till_done()
    freezer.tick(timedelta(hours=2, minutes=30))
    rt.maintenance._tick(dt_util.utcnow())  # still running: counted as it goes
    await hass.async_block_till_done()
    assert said[0].data["message"] == "The purifier filter is due — about 6 hours of use."
    assert "Purifier filter" in (await gather_briefing(hass, rt))["maintenance_due"][0]
    rt.maintenance._tick(dt_util.utcnow())
    await hass.async_block_till_done()
    assert len(said) == 1  # once, then again in a few days
    api = await llm.async_get_api(hass, DOMAIN, llm.LLMContext(platform="t", context=None, language="en",
                                                               assistant=None, device_id=None))
    out = await api.async_call_tool(llm.ToolInput(tool_name="sentient_maintenance", tool_args={"action": "done", "job": "purifier filter"}))
    assert getattr(out, "data", out) == {"done": "Purifier filter"} and not rt.maintenance.due()
