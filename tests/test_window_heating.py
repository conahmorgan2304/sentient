"""Open windows and the heating: room heaters turned down and put back; whole-house heating only mentioned."""

from datetime import timedelta
from types import SimpleNamespace
from unittest.mock import AsyncMock, MagicMock, patch

from homeassistant.util import dt as dt_util
from pytest_homeassistant_custom_component.common import async_mock_service

from custom_components.sentient.window_heating import WindowHeating

LIVING = "climate.living_room"
HALL = "climate.hallway"
WIN_LIVING = "binary_sensor.living_room_window"
WIN_BED = "binary_sensor.bedroom_window"
AREAS = {LIVING: "living_room", HALL: "hallway", WIN_LIVING: "living_room", WIN_BED: "bedroom"}


def _rt(target_now=None):
    return SimpleNamespace(
        timeline=MagicMock(),
        speech=SimpleNamespace(async_say=AsyncMock()),
        heat_prog=SimpleNamespace(target_now=lambda _h: target_now),
    )


def _engine(hass, rt, **opts):
    options = {"window_heating": True, "heating_entities": [LIVING, HALL], "pass_through_areas": ["hallway"],
               "heating_night_temp": 15, **opts}
    return WindowHeating(hass, "test", options, lambda: rt)


def _heater(hass, entity, temp, action="heating"):
    hass.states.async_set(entity, "heat", {"temperature": temp, "current_temperature": 18, "hvac_action": action,
                                          "friendly_name": entity.split(".")[1].replace("_", " ").title()})


def _window(hass, entity, state):
    hass.states.async_set(entity, state, {"device_class": "window",
                                          "friendly_name": entity.split(".")[1].replace("_", " ")})


def _later(minutes):
    return dt_util.utcnow() + timedelta(minutes=minutes)


def _areas():
    return patch("custom_components.sentient.presence.area_of", lambda _h, e: AREAS.get(e))


async def test_room_heater_turned_down_and_put_back(hass):
    rt = _rt()
    calls = async_mock_service(hass, "climate", "set_temperature")
    _heater(hass, LIVING, 21)
    _heater(hass, HALL, 15, action="idle")
    _window(hass, WIN_LIVING, "on")
    eng = _engine(hass, rt)
    with _areas():
        await eng.async_check(_later(2))
        assert not calls  # not open long enough yet
        await eng.async_check(_later(6))
        assert calls[-1].data == {"entity_id": LIVING, "temperature": 15.0}
        assert eng.holding(LIVING) and not eng.holding(HALL)
        _heater(hass, LIVING, 15)
        _window(hass, WIN_LIVING, "off")
        await eng.async_check(_later(20))
    assert calls[-1].data == {"entity_id": LIVING, "temperature": 21.0}
    assert not eng.holding(LIVING)


async def test_put_back_follows_the_schedule(hass):
    rt = _rt(target_now=19.0)
    calls = async_mock_service(hass, "climate", "set_temperature")
    _heater(hass, LIVING, 21)
    _window(hass, WIN_LIVING, "on")
    eng = _engine(hass, rt)
    with _areas():
        await eng.async_check(_later(6))
        _heater(hass, LIVING, 15)
        _window(hass, WIN_LIVING, "off")
        await eng.async_check(_later(10))
    assert calls[-1].data["temperature"] == 19.0


async def test_changed_by_hand_meanwhile_is_left_alone(hass):
    rt = _rt()
    calls = async_mock_service(hass, "climate", "set_temperature")
    _heater(hass, LIVING, 21)
    _window(hass, WIN_LIVING, "on")
    eng = _engine(hass, rt)
    with _areas():
        await eng.async_check(_later(6))
        _heater(hass, LIVING, 23)  # someone turned it up
        _window(hass, WIN_LIVING, "off")
        await eng.async_check(_later(10))
    assert len(calls) == 1 and not eng.holding(LIVING)


async def test_whole_house_heating_is_never_turned_down_only_mentioned_once(hass):
    rt = _rt()
    calls = async_mock_service(hass, "climate", "set_temperature")
    _heater(hass, LIVING, 15, action="idle")
    _heater(hass, HALL, 21)
    _window(hass, WIN_BED, "on")
    eng = _engine(hass, rt)
    with _areas():
        await eng.async_check(_later(6))
        await eng.async_check(_later(7))
    assert not calls
    rt.speech.async_say.assert_awaited_once()
    assert "heating's on" in rt.speech.async_say.await_args.args[0]


async def test_window_in_a_pass_through_area_touches_nothing(hass):
    rt = _rt()
    calls = async_mock_service(hass, "climate", "set_temperature")
    _heater(hass, HALL, 21)
    hall_window = "binary_sensor.hallway_window"
    _window(hass, hall_window, "on")
    eng = _engine(hass, rt, heating_entities=[HALL])
    with patch("custom_components.sentient.presence.area_of", lambda _h, e: "hallway"):
        await eng.async_check(_later(6))
    assert not calls and not eng.holding(HALL)


async def test_gives_up_after_three_hours(hass):
    rt = _rt()
    calls = async_mock_service(hass, "climate", "set_temperature")
    _heater(hass, LIVING, 21)
    _window(hass, WIN_LIVING, "on")
    eng = _engine(hass, rt)
    with _areas():
        await eng.async_check(_later(6))
        _heater(hass, LIVING, 15)
        await eng.async_check(_later(6 + 181))
        assert calls[-1].data["temperature"] == 21.0
        _heater(hass, LIVING, 21)
        await eng.async_check(_later(6 + 185))
    assert len(calls) == 2  # not turned down again while the same window stays open


async def test_off_does_nothing_and_switching_off_puts_back(hass):
    rt = _rt()
    calls = async_mock_service(hass, "climate", "set_temperature")
    _heater(hass, LIVING, 21)
    _window(hass, WIN_LIVING, "on")
    eng = _engine(hass, rt, window_heating=False)
    with _areas():
        await eng.async_start()
        eng._tick(_later(6))
        await hass.async_block_till_done()
        assert not calls
        eng.options = {**eng.options, "window_heating": True}
        await eng.async_check(_later(6))
        _heater(hass, LIVING, 15)
        eng.options = {**eng.options, "window_heating": False}
        await eng.async_disable()
    eng.async_stop()
    assert calls[-1].data["temperature"] == 21.0 and not eng.down


async def test_turned_down_is_remembered_over_a_restart(hass, hass_storage):
    rt = _rt()
    async_mock_service(hass, "climate", "set_temperature")
    _heater(hass, LIVING, 21)
    _window(hass, WIN_LIVING, "on")
    eng = _engine(hass, rt)
    with _areas():
        await eng.async_check(_later(6))
    await eng._store.async_save({"down": eng.down})
    again = _engine(hass, rt)
    await again.async_start()
    again.async_stop()
    assert again.holding(LIVING) and again.down[LIVING]["was"] == 21.0


async def test_schedule_waits_for_a_window_held_heater(hass):
    from tests.test_heating_programmes import LOUNGE, _setup

    entry = await _setup(hass)
    rt = entry.runtime_data
    rt.window_heat = SimpleNamespace(holding=lambda h: h == LOUNGE)
    assert rt.heat_prog.held(LOUNGE, dt_util.now())


async def test_never_touch_room_heater_is_left_alone(hass):
    rt = _rt()
    calls = async_mock_service(hass, "climate", "set_temperature")
    _heater(hass, LIVING, 21)
    _window(hass, WIN_LIVING, "on")
    eng = _engine(hass, rt)
    with _areas(), patch("custom_components.sentient.never.never_list", lambda _h: {LIVING}):
        await eng.async_check(_later(6))
    assert not calls and not eng.holding(LIVING)
    rt.speech.async_say.assert_awaited_once()  # still told, as for whole-house heating
