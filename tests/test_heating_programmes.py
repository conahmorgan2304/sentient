"""A proper heating schedule: several on/off periods a day, for chosen heaters."""

from datetime import datetime, timedelta

import pytest
from homeassistant.core import HomeAssistant
from homeassistant.util import dt as dt_util
from pytest_homeassistant_custom_component.common import MockConfigEntry, async_fire_time_changed, async_mock_service

from custom_components.sentient.const import DOMAIN
from custom_components.sentient.heating_schedule import clean_programme, due, next_change, wanted_on

LOUNGE, BED = "climate.lounge", "climate.bedroom"
TZ = dt_util.get_default_time_zone
OPTS = {"heating_entities": [LOUNGE, BED]}
WEEKDAYS = ["mon", "tue", "wed", "thu", "fri"]
DAY = {"name": "Weekdays", "heaters": [LOUNGE], "days": WEEKDAYS, "enabled": True,
       "periods": [{"on": "06:00", "off": "09:00", "temp": 21}, {"on": "15:00", "off": "17:00", "temp": 20},
                   {"on": "20:00", "off": "21:00", "temp": 21}]}


def at(day, hh, mm):  # 2026-11-16 is a Monday
    return datetime(2026, 11, 16 + day, hh, mm, tzinfo=TZ())


def test_several_periods_a_day():
    progs = [clean_programme(OPTS, DAY)]
    assert due(progs, at(0, 6, 0)) == [(LOUNGE, "on", 21.0, "Weekdays")]
    assert due(progs, at(0, 9, 0)) == [(LOUNGE, "off", None, "Weekdays")]
    assert due(progs, at(0, 15, 0))[0][1:3] == ("on", 20.0)
    assert due(progs, at(0, 21, 0))[0][1] == "off"
    assert due(progs, at(5, 6, 0)) == []  # Saturday: not in this programme
    assert wanted_on(progs, LOUNGE, at(0, 7, 30))["temp"] == 21.0
    assert wanted_on(progs, LOUNGE, at(0, 10, 0)) is None
    assert wanted_on(progs, BED, at(0, 7, 30)) is None  # only the chosen heaters
    nxt = next_change(progs, at(0, 9, 30))
    assert nxt["what"] == "on" and nxt["time"].startswith("2026-11-16T15:00")


def test_a_period_past_midnight_ends_next_morning():
    progs = [clean_programme(OPTS, {**DAY, "days": ["fri"], "periods": [{"on": "22:00", "off": "01:00", "temp": 19}]})]
    assert due(progs, at(4, 22, 0))[0][1] == "on"  # Friday night
    assert due(progs, at(5, 1, 0))[0][1] == "off"  # Saturday morning
    assert due(progs, at(0, 1, 0)) == []  # Monday morning: Sunday wasn't in it
    assert wanted_on(progs, LOUNGE, at(5, 0, 30))


def test_back_to_back_periods_dont_switch_off_in_between():
    progs = [clean_programme(OPTS, {**DAY, "periods": [{"on": "06:00", "off": "09:00", "temp": 21}, {"on": "09:00", "off": "10:00", "temp": 19}]})]
    assert due(progs, at(0, 9, 0)) == [(LOUNGE, "on", 19.0, "Weekdays")]


@pytest.mark.parametrize("bad, words", [
    ({"heaters": []}, "heater"), ({"heaters": ["climate.kitchen"]}, "isn't one of"), ({"days": []}, "day"),
    ({"periods": []}, "period"), ({"periods": [{"on": "06:00", "off": "06:00", "temp": 21}]}, "same time"),
    ({"periods": [{"on": "06:00", "off": "09:00", "temp": 40}]}, "between"),
])
def test_checked_with_readable_errors(bad, words):
    with pytest.raises(ValueError, match=words):
        clean_programme(OPTS, {**DAY, **bad})


async def _setup(hass, **extra):
    hass.states.async_set(LOUNGE, "off", {"friendly_name": "Lounge", "hvac_modes": ["off", "heat"]})
    hass.states.async_set(BED, "heat", {"friendly_name": "Bedroom", "hvac_modes": ["off", "heat"]})
    entry = MockConfigEntry(domain=DOMAIN, title="Nova", unique_id=DOMAIN, data={
        "persona": "Nova", "heating_entities": [LOUNGE, BED], "heating_schedule": True,
        "quiet_start": "00:00:00", "quiet_end": "00:00:00", **extra})
    entry.add_to_hass(hass)
    assert await hass.config_entries.async_setup(entry.entry_id)
    await hass.async_block_till_done(wait_background_tasks=True)
    return entry


async def test_runs_on_and_off_through_the_page(hass: HomeAssistant, freezer, hass_ws_client, hass_admin_user):
    entry = await _setup(hass)
    ws = await hass_ws_client(hass)
    await ws.send_json({"id": 1, "type": "sentient/heating/programme/save", "programme": DAY})
    saved = (await ws.receive_json())["result"]
    assert saved["id"] and len(saved["periods"]) == 3
    await ws.send_json({"id": 2, "type": "sentient/heating"})
    out = (await ws.receive_json())["result"]
    assert out["programmes"][0]["name"] == "Weekdays" and out["next"]["what"] in ("on", "off")
    temps = async_mock_service(hass, "climate", "set_temperature")
    offs = async_mock_service(hass, "climate", "turn_off")
    freezer.move_to(dt_util.as_utc(at(0, 6, 0)))
    async_fire_time_changed(hass, dt_util.utcnow() + timedelta(seconds=1))
    await hass.async_block_till_done(wait_background_tasks=True)
    assert temps[-1].data == {"entity_id": LOUNGE, "temperature": 21.0, "hvac_mode": "heat"}  # off, so switched on
    freezer.move_to(dt_util.as_utc(at(0, 9, 0)))
    async_fire_time_changed(hass, dt_util.utcnow() + timedelta(seconds=1))
    await hass.async_block_till_done(wait_background_tasks=True)
    assert offs[-1].data == {"entity_id": LOUNGE}
    # switched off in Settings: kept, nothing runs
    entry.runtime_data.options = {**entry.runtime_data.options, "heating_schedule": False}
    freezer.move_to(dt_util.as_utc(at(0, 15, 0)))
    async_fire_time_changed(hass, dt_util.utcnow() + timedelta(seconds=1))
    await hass.async_block_till_done(wait_background_tasks=True)
    assert len(temps) == 1
    assert entry.runtime_data.heat_prog.items.remove(saved["id"])


async def test_today_for_the_overview_and_screen(hass: HomeAssistant, hass_ws_client, hass_admin_user):
    await _setup(hass, briefing_weather="weather.home", welcome_people=["device_tracker.phone"])
    hass.states.async_set("weather.home", "rainy", {"temperature": 9, "temperature_unit": "°C"})
    hass.states.async_set("device_tracker.phone", "not_home")
    hass.states.async_set("person.sam", "home", {"friendly_name": "Sam", "device_trackers": ["device_tracker.phone"]})
    ws = await hass_ws_client(hass)
    await ws.send_json({"id": 1, "type": "sentient/today"})
    out = (await ws.receive_json())["result"]
    assert out["weather"]["temp"] == 9
    assert out["people"] == [{"name": "Sam", "home": False, "where": "not_home"}]  # the phone, not the always-home person
    assert {z["name"] for z in out["heating"]["zones"]} == {"Lounge", "Bedroom"}


async def test_schedule_takes_over_from_a_hand_boost(hass: HomeAssistant, freezer):
    entry = await _setup(hass)
    rt = entry.runtime_data
    async_mock_service(hass, "climate", "set_temperature")
    rt.heating._boosted[LOUNGE] = {"origin": "limit", "status": "active", "until": "2099-01-01T00:00:00+00:00"}
    done = await rt.heat_prog.async_run([(LOUNGE, "on", 21.0, "Weekdays")], at(0, 6, 0))
    assert done and LOUNGE not in rt.heating._boosted  # its "put it back later" would undo the schedule


def test_a_suggested_schedule_from_what_was_learned():
    from custom_components.sentient.heating_schedule import suggest

    progs = suggest([LOUNGE], {"weekdays": 6 * 60 + 30, "weekends": 8 * 60}, {LOUNGE: {"rate": 1.0, "comfort": 20.5}})
    wd, we = progs
    assert wd["days"] == WEEKDAYS and wd["periods"][0] == {"on": "05:00", "off": "08:30", "temp": 20.5}
    assert we["periods"][0]["on"] == "06:30" and we["periods"][0]["off"] == "22:00"
    for p in progs:  # each one passes the same checks as one made by hand
        clean_programme(OPTS, p)
    assert suggest([], {}, {}) == []


async def test_stop_watching_and_memory_check_up_over_the_websocket(hass: HomeAssistant, hass_ws_client, hass_admin_user):
    entry = await _setup(hass, notice_entities=["sensor.kettle", "sensor.window"])
    ws = await hass_ws_client(hass)
    await ws.send_json({"id": 1, "type": "sentient/notice/unwatch", "entity_id": "sensor.kettle"})
    assert (await ws.receive_json())["result"]["notice_entities"] == ["sensor.window"]
    await hass.async_block_till_done()
    assert entry.options["notice_entities"] == ["sensor.window"]
    mem = entry.runtime_data.memory
    a = mem.add("Keep the hall light on at night", "preference")
    mem.pin(a.id)
    mem.add("Keep the hall light off at night", "preference")
    await ws.send_json({"id": 2, "type": "sentient/memory/review"})
    pairs = (await ws.receive_json())["result"]["pairs"]
    assert pairs and pairs[0]["kind"] == "conflict"
    await ws.send_json({"id": 3, "type": "sentient/memory/resolve", "pair": pairs[0]["pair"], "keep": a.id})
    assert (await ws.receive_json())["result"]["ok"] and [m.text for m in mem.items] == ["Keep the hall light on at night"]
