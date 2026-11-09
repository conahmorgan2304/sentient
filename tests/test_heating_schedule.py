"""Heating schedules: set a chosen thermostat at a time; off in Settings means they don't run."""

from datetime import datetime, timedelta

from homeassistant.core import HomeAssistant
from homeassistant.util import dt as dt_util
from pytest_homeassistant_custom_component.common import MockConfigEntry, async_fire_time_changed, async_mock_service

from custom_components.sentient.const import DOMAIN
from custom_components.sentient.heating_schedule import heating_actions

ZONE = "climate.living_room"


def _options(**extra):
    return {"persona": "Nova", "heating_entities": [ZONE], "heating_schedule": True,
            "quiet_start": "00:00:00", "quiet_end": "00:00:00", **extra}


async def _setup(hass: HomeAssistant, **extra) -> MockConfigEntry:
    hass.states.async_set(ZONE, "heat", {"friendly_name": "Living room", "temperature": 18, "current_temperature": 17})
    hass.states.async_set("climate.bedroom", "heat", {"friendly_name": "Bedroom"})
    entry = MockConfigEntry(domain=DOMAIN, title="Nova", unique_id=DOMAIN, data=_options(**extra))
    entry.add_to_hass(hass)
    assert await hass.config_entries.async_setup(entry.entry_id)
    await hass.async_block_till_done()
    return entry


def test_actions_follow_the_spec_and_limits(hass: HomeAssistant) -> None:
    opts = _options()
    assert heating_actions(hass, opts, {"entity_id": ZONE, "temperature": 21.5})[0] == [
        {"action": "climate.set_temperature", "target": {"entity_id": ZONE}, "data": {"temperature": 21.5}}]
    assert heating_actions(hass, opts, {"entity_id": ZONE, "temperature": None})[0] == [
        {"action": "climate.turn_off", "target": {"entity_id": ZONE}}]
    assert "between" in heating_actions(hass, opts, {"entity_id": ZONE, "temperature": 40})[1]
    assert "chosen" in heating_actions(hass, opts, {"entity_id": "climate.bedroom", "temperature": 20})[1]


async def test_saved_through_the_page_and_listed(hass: HomeAssistant, hass_ws_client, hass_admin_user) -> None:
    await _setup(hass)
    ws = await hass_ws_client(hass)
    await ws.send_json({"id": 1, "type": "sentient/heating/save", "entity_id": ZONE, "time": "06:30", "days": ["mon", "tue"], "temperature": 21})
    assert (await ws.receive_json())["success"]
    await ws.send_json({"id": 2, "type": "sentient/heating"})
    out = (await ws.receive_json())["result"]
    assert out["schedule_on"] and out["zones"][0]["entity_id"] == ZONE
    assert [(s["time"], s["days"], s["temperature"]) for s in out["schedules"]] == [("06:30", ["mon", "tue"], 21.0)]
    await ws.send_json({"id": 3, "type": "sentient/heating/save", "entity_id": "climate.bedroom", "time": "06:30", "temperature": 21})
    assert not (await ws.receive_json())["success"]  # not one of the chosen zones


async def test_it_runs_at_its_time_and_not_when_switched_off(hass: HomeAssistant, freezer) -> None:
    freezer.move_to(dt_util.as_utc(datetime(2026, 11, 9, 6, 29, tzinfo=dt_util.get_default_time_zone())))
    entry = await _setup(hass)
    calls = async_mock_service(hass, "climate", "set_temperature")
    entry.runtime_data.lists["alarms"].upsert({
        "name": "Heating: Living room to 21° at 06:30", "time": "06:30:00", "days": [], "silent": True, "made_by": "page",
        "heating": {"entity_id": ZONE, "temperature": 21.0}, "actions": []})
    freezer.tick(timedelta(minutes=1))
    async_fire_time_changed(hass)
    await hass.async_block_till_done()
    assert [c.data["temperature"] for c in calls] == [21.0]

    # switched off: kept, but it doesn't run
    entry.runtime_data.options = {**entry.runtime_data.options, "heating_schedule": False}
    alarm = next(iter(entry.runtime_data.lists["alarms"].items.values()))
    out = await entry.runtime_data.alarms.async_ring(alarm)
    assert out == {"skipped": "heating schedules are switched off in Settings"} and len(calls) == 1
    assert alarm["heating"]["temperature"] == 21.0  # the saved time is still there
