from datetime import datetime, timedelta

from homeassistant.util import dt as dt_util
from pytest_homeassistant_custom_component.common import async_fire_time_changed, async_mock_service

from tests.test_heating_programmes import LOUNGE, _setup

TZ = dt_util.get_default_time_zone


async def test_programme_fires_at_its_minute(hass, freezer):
    freezer.move_to(datetime(2026, 11, 16, 15, 58, 30, tzinfo=TZ()))
    entry = await _setup(hass)
    calls = async_mock_service(hass, "climate", "set_temperature")
    prog = {"name": "Weekdays", "heaters": [LOUNGE], "days": ["mon"], "enabled": True, "periods": [{"on": "16:00", "off": "20:00", "temp": 20}]}
    from custom_components.sentient.heating_schedule import clean_programme
    entry.runtime_data.heat_prog.items.upsert(clean_programme(entry.runtime_data.options, prog))
    for s in (60, 90):
        freezer.move_to(datetime(2026, 11, 16, 15, 58, 30, tzinfo=TZ()) + timedelta(seconds=s))
        async_fire_time_changed(hass, dt_util.utcnow())
        await hass.async_block_till_done(wait_background_tasks=True)
    assert calls
    hp = entry.runtime_data.heat_prog
    assert hp.last_tick and hp.ran["Weekdays"]["ok"] and "on to 20" in hp.ran["Weekdays"]["why"]


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
