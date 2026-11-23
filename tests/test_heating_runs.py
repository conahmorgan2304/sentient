from datetime import datetime, timedelta

import pytest

from homeassistant.core import Context
from homeassistant.util import dt as dt_util
from pytest_homeassistant_custom_component.common import async_fire_time_changed, async_mock_service

from custom_components.sentient.heating_schedule import HeatingProgrammes, clean_programme
from tests.test_heating_programmes import BED, DAY, LOUNGE, _setup, at

TZ = dt_util.get_default_time_zone


async def test_programme_fires_at_its_minute(hass, freezer):
    freezer.move_to(datetime(2026, 11, 16, 15, 58, 30, tzinfo=TZ()))
    entry = await _setup(hass)
    calls = async_mock_service(hass, "climate", "set_temperature")
    prog = {"name": "Weekdays", "heaters": [LOUNGE], "days": ["mon"], "enabled": True, "periods": [{"on": "16:00", "off": "20:00", "temp": 20}]}
    saved = entry.runtime_data.heat_prog.items.upsert(clean_programme(entry.runtime_data.options, prog))
    for s in (60, 90):
        freezer.move_to(datetime(2026, 11, 16, 15, 58, 30, tzinfo=TZ()) + timedelta(seconds=s))
        async_fire_time_changed(hass, dt_util.utcnow())
        await hass.async_block_till_done(wait_background_tasks=True)
    assert calls
    hp = entry.runtime_data.heat_prog
    assert hp.last_tick and hp.ran[saved["id"]]["ok"] and "on to 20" in hp.ran[saved["id"]]["why"]


async def test_a_thermostat_that_never_answers_is_reported(hass):
    import asyncio
    from unittest.mock import patch

    entry = await _setup(hass)
    hp = entry.runtime_data.heat_prog

    async def hang(_call):
        await asyncio.sleep(3600)
    hass.services.async_register("climate", "set_temperature", hang)
    real = asyncio.timeout
    with patch("asyncio.timeout", lambda _s: real(0.05)):
        done = await hp.async_run([(LOUNGE, "on", 20.0, "Weekdays")], datetime(2026, 11, 16, 16, 0, tzinfo=TZ()))
    assert done == [] and hp.ran["Weekdays"]["ok"] is False and "30 seconds" in hp.ran["Weekdays"]["why"]


async def test_never_touch_is_rechecked_for_a_saved_programme(hass):
    entry = await _setup(hass)
    rt = entry.runtime_data
    saved = rt.heat_prog.items.upsert(clean_programme(rt.options, DAY))
    calls = async_mock_service(hass, "climate", "set_temperature")
    off = async_mock_service(hass, "climate", "turn_off")
    rt.options = {**rt.options, "never_entities": [LOUNGE]}
    await rt.heat_prog.async_run([(LOUNGE, "on", 21, saved["id"])], at(0, 6, 0))
    await rt.heat_prog.async_run([(LOUNGE, "off", None, saved["id"])], at(0, 9, 0))
    assert not calls and not off
    assert "never-touch" in rt.heat_prog.ran[saved["id"]]["zones"][LOUNGE]["why"]


async def test_two_zone_run_keeps_failure_after_other_zone_succeeds(hass):
    entry = await _setup(hass)
    hp = entry.runtime_data.heat_prog

    async def change(call):
        if call.data["entity_id"] == LOUNGE:
            raise RuntimeError("lounge failed")

    hass.services.async_register("climate", "set_temperature", change)
    await hp.async_run([(LOUNGE, "on", 21, "p1"), (BED, "on", 21, "p1")], at(0, 6, 0))
    report = hp.ran["p1"]
    assert report["ok"] is False
    assert report["zones"][LOUNGE]["ok"] is False
    assert report["zones"][BED]["ok"] is True


@pytest.mark.parametrize("manual_override", [False, True])
async def test_missed_off_only_recovers_confirmed_unchanged_heat(hass, freezer, manual_override):
    freezer.move_to(at(0, 6, 0))
    entry = await _setup(hass)
    rt = entry.runtime_data
    hp = rt.heat_prog
    saved = hp.items.upsert(clean_programme(rt.options, DAY))

    night: list = []  # a missed switch-off is put right by dropping to the night temperature, never by switching off

    async def change(call):
        if call.data["temperature"] == 15.0:
            night.append(call)
        hass.states.async_set(LOUNGE, "heat", {"temperature": call.data["temperature"]}, context=call.context)

    hass.services.async_register("climate", "set_temperature", change)
    await hp.async_run([(LOUNGE, "on", 21, saved["id"])], at(0, 6, 0))
    await hass.async_block_till_done(wait_background_tasks=True)
    assert hp._owned[LOUNGE]["controls"] == ["heat", 21, None]
    freezer.move_to(at(0, 6, 0) + timedelta(seconds=2))
    async_fire_time_changed(hass, dt_util.utcnow())
    await hass.async_block_till_done(wait_background_tasks=True)
    hp.async_stop()
    freezer.move_to(at(0, 9, 1))
    if manual_override:
        hass.states.async_set(LOUNGE, "heat", {"temperature": 23}, context=Context(user_id="manual"))
    restarted = HeatingProgrammes(hass, hp.items, lambda: rt, entry.entry_id)
    await restarted.async_start()
    try:
        assert LOUNGE in restarted._owned  # loaded the saved confirmation, not the original object
        restarted._tick(dt_util.utcnow())
        await hass.async_block_till_done(wait_background_tasks=True)
        assert len(night) == (0 if manual_override else 1)
        assert LOUNGE not in restarted._owned
    finally:
        restarted.async_stop()


async def _tick_at(hass, when):
    from unittest.mock import patch  # noqa: F401

    async_fire_time_changed(hass, dt_util.as_utc(when))
    await hass.async_block_till_done(wait_background_tasks=True)


async def test_a_voice_turn_up_lasts_its_time_through_a_period_start(hass, freezer):
    """23 Nov: turned up by voice at 11:05; the next period starting would have taken over straight away."""
    freezer.move_to(datetime(2026, 11, 16, 11, 0, tzinfo=TZ()))
    entry = await _setup(hass, heating_limit=1.0, heating_revert_day_hours=3.0)
    rt = entry.runtime_data
    calls = async_mock_service(hass, "climate", "set_temperature")
    prog = {"name": "Weekdays", "heaters": [LOUNGE], "days": ["mon"], "enabled": True,
            "periods": [{"on": "12:00", "off": "20:00", "temp": 18}]}
    rt.heat_prog.items.upsert(clean_programme(rt.options, prog))
    hass.states.async_set(LOUNGE, "heat", {"friendly_name": "Lounge", "temperature": 15, "hvac_modes": ["off", "heat"]})
    # someone turns it up to 20 (the app or the voice: a service call from a person)
    await hass.services.async_call("climate", "set_temperature", {"entity_id": LOUNGE, "temperature": 20},
                                   blocking=True, context=Context(user_id="someone"))
    hass.states.async_set(LOUNGE, "heat", {"friendly_name": "Lounge", "temperature": 20, "hvac_modes": ["off", "heat"]})
    await hass.async_block_till_done()
    boost = rt.heating._boosted[LOUNGE]
    until = dt_util.parse_datetime(boost["until"])
    assert until - dt_util.utcnow() > timedelta(hours=2, minutes=59)  # the schedule's 3 hours, not the 1-hour limit
    calls.clear()
    for minute in (11 * 60 + 59, 12 * 60, 12 * 60 + 1):
        freezer.move_to(datetime(2026, 11, 16, minute // 60, minute % 60, tzinfo=TZ()))
        await _tick_at(hass, dt_util.now())
    assert not [c for c in calls if c.data.get("temperature") == 18]  # the period waited
    assert any("waited" in i["text"] for i in rt.timeline.since(2, None, 50))
    # its time is up: back to what the schedule wants now (the period's 18), not to the 15 it was before
    freezer.move_to(until + timedelta(minutes=1))
    await _tick_at(hass, dt_util.now())
    assert [c.data["temperature"] for c in calls] == [18]


async def test_without_a_change_the_period_starts_on_time(hass, freezer):
    freezer.move_to(datetime(2026, 11, 16, 11, 58, tzinfo=TZ()))
    entry = await _setup(hass)
    rt = entry.runtime_data
    calls = async_mock_service(hass, "climate", "set_temperature")
    prog = {"name": "Weekdays", "heaters": [LOUNGE], "days": ["mon"], "enabled": True,
            "periods": [{"on": "12:00", "off": "20:00", "temp": 18}]}
    rt.heat_prog.items.upsert(clean_programme(rt.options, prog))
    hass.states.async_set(LOUNGE, "heat", {"friendly_name": "Lounge", "temperature": 21, "hvac_modes": ["off", "heat"]})
    for minute in (59, 60):
        freezer.move_to(datetime(2026, 11, 16, 11, 0, tzinfo=TZ()) + timedelta(minutes=minute))
        await _tick_at(hass, dt_util.now())
    assert [c.data["temperature"] for c in calls] == [18]  # found at 21, but Sentient never set it: not someone's


async def test_a_dial_change_after_the_schedule_set_it_also_waits(hass, freezer):
    freezer.move_to(datetime(2026, 11, 16, 9, 0, tzinfo=TZ()))
    entry = await _setup(hass)
    rt = entry.runtime_data
    calls = async_mock_service(hass, "climate", "set_temperature")
    prog = {"name": "Weekdays", "heaters": [LOUNGE], "days": ["mon"], "enabled": True,
            "periods": [{"on": "09:00", "off": "12:00", "temp": 19}]}
    rt.heat_prog.items.upsert(clean_programme(rt.options, prog))
    hass.states.async_set(LOUNGE, "heat", {"friendly_name": "Lounge", "temperature": 15, "hvac_modes": ["off", "heat"]})
    await _tick_at(hass, dt_util.now())
    assert [c.data["temperature"] for c in calls] == [19]
    hass.states.async_set(LOUNGE, "heat", {"friendly_name": "Lounge", "temperature": 19, "hvac_modes": ["off", "heat"]})
    freezer.move_to(datetime(2026, 11, 16, 11, 0, tzinfo=TZ()))
    hass.states.async_set(LOUNGE, "heat", {"friendly_name": "Lounge", "temperature": 22, "hvac_modes": ["off", "heat"]})
    await _tick_at(hass, dt_util.now())  # turned up on the thermostat itself
    calls.clear()
    freezer.move_to(datetime(2026, 11, 16, 12, 0, tzinfo=TZ()))
    await _tick_at(hass, dt_util.now())
    assert not calls  # the period's end waited for the change
    freezer.move_to(datetime(2026, 11, 16, 14, 1, tzinfo=TZ()))
    await _tick_at(hass, dt_util.now())
    assert [c.data["temperature"] for c in calls] == [15]  # then the night temperature, as the schedule wants now
