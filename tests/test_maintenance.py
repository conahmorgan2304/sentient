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


async def test_a_date_typed_on_the_page_never_crashes_the_check(hass: HomeAssistant, freezer) -> None:
    """12 Nov: a last-done date with no time zone crashed the minute check ("can't subtract offset-naive and
    offset-aware datetimes"), so nothing was ever said as due again."""
    freezer.move_to(dt_util.start_of_local_day() + timedelta(hours=10))
    hass.states.async_set("assist_satellite.kitchen", "idle", {"friendly_name": "Kitchen"})
    entry = MockConfigEntry(domain=DOMAIN, title="Nova", unique_id=DOMAIN, data=OPTIONS)
    entry.add_to_hass(hass)
    assert await hass.config_entries.async_setup(entry.entry_id)
    await hass.async_block_till_done()
    rt = entry.runtime_data
    ten_days_ago = (dt_util.now() - timedelta(days=10)).date()
    rt.lists["maintenance"].upsert({"name": "Descale kettle", "every_days": 7, "last_done": ten_days_ago.isoformat()})
    rt.lists["maintenance"].upsert({"name": "Coffee filter", "every_days": 30, "last_done": f"{ten_days_ago.isoformat()}T09:00"})
    rt.tracker.best_area = lambda: "kitchen"
    said = async_mock_service(hass, "assist_satellite", "announce")
    rt.maintenance._tick(dt_util.utcnow())
    await hass.async_block_till_done()
    status = {s["name"]: s for s in (rt.maintenance.status(i) for i in rt.lists["maintenance"].enabled())}
    assert status["Descale kettle"]["days_since"] == 10 and status["Descale kettle"]["due"]
    assert status["Coffee filter"]["days_since"] in (9, 10) and not status["Coffee filter"]["due"]
    assert said and "Descale kettle" in said[0].data["message"] or "descale" in said[0].data["message"].lower()
