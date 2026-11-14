"""The heating is never switched off by its schedule: it drops to a night temperature, wakes properly, and
a hand-made change goes back to the schedule after a while."""
import asyncio
from datetime import datetime, timedelta
from unittest.mock import patch

from homeassistant.core import HomeAssistant
from homeassistant.util import dt as dt_util
from pytest_homeassistant_custom_component.common import async_mock_service

from custom_components.sentient.heating_schedule import clean_programme
from tests.test_heating_programmes import DAY, LOUNGE, _setup, at

MODES = {"friendly_name": "Lounge", "hvac_modes": ["off", "auto", "heat_cool"]}


async def _saved(hass, entry, **extra):
    return entry.runtime_data.heat_prog.items.upsert(clean_programme(entry.runtime_data.options, {**DAY, **extra}))


async def test_the_end_of_a_period_drops_to_the_night_temperature_not_off(hass: HomeAssistant) -> None:
    entry = await _setup(hass)
    hass.states.async_set(LOUNGE, "heat_cool", {**MODES, "temperature": 21})
    saved = await _saved(hass, entry)
    temps = async_mock_service(hass, "climate", "set_temperature")
    offs = async_mock_service(hass, "climate", "turn_off")
    await entry.runtime_data.heat_prog.async_run([(LOUNGE, "off", None, saved["id"])], at(0, 9, 0))
    assert not offs and temps[0].data["temperature"] == 15.0
    assert "night" in entry.runtime_data.heat_prog.ran[saved["id"]]["why"]


async def test_the_night_temperature_can_be_changed(hass: HomeAssistant) -> None:
    entry = await _setup(hass, heating_night_temp=17.5)
    hass.states.async_set(LOUNGE, "auto", {**MODES, "temperature": 21})
    saved = await _saved(hass, entry)
    temps = async_mock_service(hass, "climate", "set_temperature")
    await entry.runtime_data.heat_prog.async_run([(LOUNGE, "off", None, saved["id"])], at(0, 9, 0))
    assert temps[0].data["temperature"] == 17.5


async def test_a_thermostat_that_ignores_the_mode_in_a_temperature_call_is_set_to_a_mode_and_checked(hass: HomeAssistant) -> None:
    """06:00 on 10 Nov: the temperature was taken, the mode stayed off, and the log still said it was fine."""
    entry = await _setup(hass)
    hass.states.async_set(LOUNGE, "off", {**MODES, "temperature": 14})
    saved = await _saved(hass, entry)
    async_mock_service(hass, "climate", "set_temperature")  # takes the temperature, leaves the mode
    real_sleep = asyncio.sleep
    modes: list[str] = []

    async def set_mode(call):
        modes.append(call.data["hvac_mode"])
        hass.states.async_set(LOUNGE, call.data["hvac_mode"], {**MODES, "temperature": 20})

    hass.services.async_register("climate", "set_hvac_mode", set_mode)
    hp = entry.runtime_data.heat_prog
    await hp.async_run([(LOUNGE, "on", 20.0, saved["id"])], at(0, 6, 0))
    assert hass.states.get(LOUNGE).state == "off"  # the temperature was taken; the mode wasn't
    with patch("custom_components.sentient.heating_schedule.asyncio.sleep", lambda _s: real_sleep(0)):
        assert await hp.async_verify(LOUNGE, 20.0, None, saved["id"], "Lounge", "Weekdays")
    assert modes and hass.states.get(LOUNGE).state != "off"


async def test_a_thermostat_that_will_not_start_is_reported_not_called_fine(hass: HomeAssistant) -> None:
    entry = await _setup(hass)
    hass.states.async_set(LOUNGE, "off", {**MODES, "temperature": 14})
    saved = await _saved(hass, entry)
    async_mock_service(hass, "climate", "set_temperature")
    async_mock_service(hass, "climate", "set_hvac_mode")
    hp = entry.runtime_data.heat_prog
    real_sleep = asyncio.sleep
    await hp.async_run([(LOUNGE, "on", 20.0, saved["id"])], at(0, 6, 0))
    with patch("custom_components.sentient.heating_schedule.asyncio.sleep", lambda _s: real_sleep(0)):
        assert not await hp.async_verify(LOUNGE, 20.0, None, saved["id"], "Lounge", "Weekdays")
    row = hp.ran[saved["id"]]
    assert row["ok"] is False and "stayed off" in row["why"]
    assert any("didn't start" in i["text"] for i in entry.runtime_data.timeline.items if i["kind"] == "health")


async def test_a_slow_thermostat_is_given_time_and_not_set_again_when_it_is_already_right(hass: HomeAssistant) -> None:
    entry = await _setup(hass)
    hass.states.async_set(LOUNGE, "off", {**MODES, "temperature": 15})
    saved = await _saved(hass, entry)
    temps = async_mock_service(hass, "climate", "set_temperature")
    async_mock_service(hass, "climate", "set_hvac_mode")
    hp = entry.runtime_data.heat_prog
    real_sleep, naps = asyncio.sleep, []

    async def nap(seconds):
        naps.append(seconds)
        if len(naps) == 7:  # it reports the change about 20 seconds after being told
            hass.states.async_set(LOUNGE, "heat_cool", {**MODES, "temperature": 15})
        await real_sleep(0)

    with patch("custom_components.sentient.heating_schedule.asyncio.sleep", nap):
        assert await hp.async_verify(LOUNGE, 15.0, None, saved["id"], "Lounge", "Weekdays")
    assert len(naps) == 7 and not temps  # patient, and it is on at 15 already, so it is left alone (setting it again would drop it out of auto)
    assert not any("didn't start" in i["text"] for i in entry.runtime_data.timeline.items)


def _tick_at(hass, freezer, when):
    freezer.move_to(dt_util.as_utc(when))


async def test_a_hand_made_change_goes_back_to_the_schedule_after_the_hours(hass: HomeAssistant, freezer) -> None:
    entry = await _setup(hass)
    hp = entry.runtime_data.heat_prog
    saved = await _saved(hass, entry, periods=[{"on": "06:00", "off": "12:00", "temp": 21}])
    # 06:30 in the period: someone turns it up to 24 by hand
    hass.states.async_set(LOUNGE, "auto", {**MODES, "temperature": 24})
    _tick_at(hass, freezer, at(0, 6, 30))
    assert hp.reconcile(at(0, 6, 30)) == []
    _tick_at(hass, freezer, at(0, 9, 29))
    assert hp.reconcile(at(0, 9, 29)) == []  # 3 hours by day: not yet
    _tick_at(hass, freezer, at(0, 9, 31))
    assert hp.reconcile(at(0, 9, 31)) == [(LOUNGE, "on", 21.0, saved["id"])]


async def test_at_night_it_goes_back_after_one_hour_even_if_turned_off_by_hand(hass: HomeAssistant, freezer) -> None:
    entry = await _setup(hass)
    rt0 = entry.runtime_data
    rt0.options = {**rt0.options, "quiet_start": "21:00:00", "quiet_end": "08:00:00"}  # night is the quiet hours
    hp = entry.runtime_data.heat_prog
    saved = await _saved(hass, entry)
    hass.states.async_set(LOUNGE, "off", {**MODES, "temperature": 15})
    _tick_at(hass, freezer, at(0, 22, 0))
    assert hp.reconcile(at(0, 22, 0)) == []
    _tick_at(hass, freezer, at(0, 22, 59))
    assert hp.reconcile(at(0, 22, 59)) == []
    _tick_at(hass, freezer, at(0, 23, 1))
    assert hp.reconcile(at(0, 23, 1)) == [(LOUNGE, "off", None, saved["id"])]  # "off" runs as the night temperature


async def test_matching_the_schedule_clears_the_wait_and_it_can_be_switched_off(hass: HomeAssistant, freezer) -> None:
    entry = await _setup(hass)
    rt0 = entry.runtime_data
    rt0.options = {**rt0.options, "quiet_start": "21:00:00", "quiet_end": "08:00:00"}  # night is the quiet hours
    hp = entry.runtime_data.heat_prog
    await _saved(hass, entry)
    hass.states.async_set(LOUNGE, "off", {**MODES, "temperature": 15})
    hp.reconcile(at(0, 22, 0))
    hass.states.async_set(LOUNGE, "heat_cool", {**MODES, "temperature": 15})  # back on the night temperature
    assert hp.reconcile(at(0, 22, 30)) == []
    hass.states.async_set(LOUNGE, "off", {**MODES, "temperature": 15})
    assert hp.reconcile(at(0, 22, 40)) == []  # a new wait starts from here
    assert hp.reconcile(at(0, 23, 30)) == []
    assert hp.reconcile(at(0, 23, 41)) != []
    rt = entry.runtime_data
    rt.options = {**rt.options, "heating_revert": False}
    hp._manual.clear()
    hass.states.async_set(LOUNGE, "off", {**MODES, "temperature": 15})
    hp.reconcile(at(1, 1, 0))
    assert hp.reconcile(at(1, 5, 0)) == []  # switched off in Settings: left as it is


def test_an_off_thermostat_wakes_into_a_hold_and_one_that_is_on_gets_only_the_temperature(hass: HomeAssistant) -> None:
    from custom_components.sentient.heating_schedule import heating_actions

    modes = {"hvac_modes": ["off", "auto", "heat_cool"], "temperature": 21}
    options = {"heating_entities": [LOUNGE]}
    hass.states.async_set(LOUNGE, "off", modes)
    actions, _ = heating_actions(hass, options, {"entity_id": LOUNGE, "temperature": 15})
    assert actions[0]["data"] == {"temperature": 15.0, "hvac_mode": "heat_cool"}  # off is never the answer; auto is its own app schedule
    for state in ("heat_cool", "heat"):  # a mode sent along would flip it in and out of a hold
        hass.states.async_set(LOUNGE, state, modes)
        actions, _ = heating_actions(hass, options, {"entity_id": LOUNGE, "temperature": 15})
        assert actions[0]["data"] == {"temperature": 15.0}
    hass.states.async_set(LOUNGE, "auto", modes)  # the app's own schedule can't be switched off, so it is put back in a hold
    actions, _ = heating_actions(hass, options, {"entity_id": LOUNGE, "temperature": 15})
    assert actions[0]["data"] == {"temperature": 15.0, "hvac_mode": "heat_cool"}
    hass.states.async_set(LOUNGE, "auto", {"hvac_modes": ["off", "auto"], "temperature": 21})  # nothing but auto: leave it
    actions, _ = heating_actions(hass, options, {"entity_id": LOUNGE, "temperature": 15})
    assert actions[0]["data"] == {"temperature": 15.0}


def test_night_is_the_quiet_hours_not_the_gaps_between_periods() -> None:
    from datetime import datetime

    from custom_components.sentient.heating_schedule import HeatingProgrammes

    options = {"quiet_start": "21:00:00", "quiet_end": "08:00:00"}
    assert HeatingProgrammes._night(options, datetime(2026, 11, 10, 11, 0)) is False  # mid-morning, between periods
    assert HeatingProgrammes._night(options, datetime(2026, 11, 10, 22, 30)) is True
    assert HeatingProgrammes._night(options, datetime(2026, 11, 10, 5, 0)) is True


async def test_a_thermostat_left_on_its_own_app_schedule_is_put_back_in_a_hold(hass: HomeAssistant, freezer) -> None:
    entry = await _setup(hass)
    hp = entry.runtime_data.heat_prog
    saved = await _saved(hass, entry)
    hass.states.async_set(LOUNGE, "auto", {**MODES, "temperature": 20})  # the right temperature, but in the app's schedule
    assert hp.reconcile(at(0, 6, 30)) == []
    assert hp.reconcile(at(0, 9, 29)) == []
    assert hp.reconcile(at(0, 9, 31)) == [(LOUNGE, "off", None, saved["id"])]  # 3 hours by day


async def test_it_isnt_put_back_to_the_night_temperature_just_before_a_period(hass: HomeAssistant, freezer) -> None:
    """14 Nov 06:57: put back to 15° three minutes before the 07:00 period, labelled "Weekdays" on a Saturday."""
    entry = await _setup(hass)
    rt0 = entry.runtime_data
    rt0.options = {**rt0.options, "quiet_start": "21:00:00", "quiet_end": "08:00:00"}
    hp = entry.runtime_data.heat_prog
    await _saved(hass, entry)  # Weekdays: 06:00-09:00 on weekdays
    weekend = await _saved(hass, entry, name="Weekend", days=["sat", "sun"],
                           periods=[{"on": "07:00", "off": "10:00", "temp": 20}])
    hass.states.async_set(LOUNGE, "heat_cool", {**MODES, "temperature": 18})  # its own programme set 18°
    saturday = lambda hh, mm: at(5, hh, mm)  # noqa: E731
    _tick_at(hass, freezer, saturday(5, 0))
    assert hp.reconcile(saturday(5, 0)) == []
    _tick_at(hass, freezer, saturday(6, 10))
    assert hp.reconcile(saturday(6, 10)) == [(LOUNGE, "off", None, weekend["id"])]  # named after today's programme
    hass.states.async_set(LOUNGE, "heat_cool", {**MODES, "temperature": 18.5})
    _tick_at(hass, freezer, saturday(6, 20))
    hp.reconcile(saturday(6, 20))
    _tick_at(hass, freezer, saturday(6, 45))
    assert hp.reconcile(saturday(6, 45)) == []  # an hour wouldn't be up yet anyway
    _tick_at(hass, freezer, saturday(7, 25))
    assert hp.reconcile(saturday(7, 25)) == [(LOUNGE, "on", 20.0, weekend["id"])]  # the period's own temperature
    hp._manual.clear()
    hass.states.async_set(LOUNGE, "heat_cool", {**MODES, "temperature": 18})
    sunday = lambda hh, mm: at(6, hh, mm)  # noqa: E731
    hp.reconcile(sunday(5, 30))
    _tick_at(hass, freezer, sunday(6, 40))
    assert hp.reconcile(sunday(6, 40)) == []  # its hour is up, but the 07:00 period is 20 minutes away


async def test_a_thermostat_on_its_own_programme_is_put_into_a_hold_with_the_mode_on_its_own(hass: HomeAssistant) -> None:
    """14 Nov: a Smart Life thermostat ignored heat_cool sent with the temperature and stayed on its own programme;
    sent on its own, the mode took. Its own programme ("auto") is never tried as the fix."""
    entry = await _setup(hass)
    hass.states.async_set(LOUNGE, "auto", {**MODES, "temperature": 18})
    saved = await _saved(hass, entry)
    async_mock_service(hass, "climate", "set_temperature")  # takes the temperature, keeps its own programme
    real_sleep = asyncio.sleep
    modes: list[str] = []

    async def set_mode(call):
        modes.append(call.data["hvac_mode"])
        hass.states.async_set(LOUNGE, call.data["hvac_mode"], {**MODES, "temperature": 18})

    hass.services.async_register("climate", "set_hvac_mode", set_mode)
    hp = entry.runtime_data.heat_prog
    with patch("custom_components.sentient.heating_schedule.asyncio.sleep", lambda _s: real_sleep(0)):
        assert await hp.async_verify(LOUNGE, 18.0, None, saved["id"], "Lounge", "Weekdays")
    assert modes == ["heat_cool"] and hass.states.get(LOUNGE).state == "heat_cool"


async def test_a_thermostat_that_wont_leave_its_own_programme_is_reported(hass: HomeAssistant) -> None:
    entry = await _setup(hass)
    hass.states.async_set(LOUNGE, "auto", {**MODES, "temperature": 18})
    saved = await _saved(hass, entry)
    async_mock_service(hass, "climate", "set_temperature")
    tried = async_mock_service(hass, "climate", "set_hvac_mode")  # accepted, but it stays on its programme
    real_sleep = asyncio.sleep
    hp = entry.runtime_data.heat_prog
    with patch("custom_components.sentient.heating_schedule.asyncio.sleep", lambda _s: real_sleep(0)):
        assert not await hp.async_verify(LOUNGE, 18.0, None, saved["id"], "Lounge", "Weekdays")
    assert "auto" not in [c.data["hvac_mode"] for c in tried]
    assert any(i["text"] == "Lounge stayed on its own programme" for i in entry.runtime_data.timeline.items)
