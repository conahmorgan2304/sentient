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

    async def change(call):
        hass.states.async_set(LOUNGE, "heat", {"temperature": 21}, context=call.context)

    hass.services.async_register("climate", "set_temperature", change)
    off = async_mock_service(hass, "climate", "turn_off")
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
        assert len(off) == (0 if manual_override else 1)
        assert LOUNGE not in restarted._owned
    finally:
        restarted.async_stop()
