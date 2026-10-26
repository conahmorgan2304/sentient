"""Heating that learns, night watch and the weekly report."""

from datetime import datetime, timedelta

from homeassistant.core import HomeAssistant, State
from homeassistant.util import dt as dt_util
from pytest_homeassistant_custom_component.common import (
    MockConfigEntry,
    async_fire_time_changed,
    async_mock_service,
)

from custom_components.sentient.const import DOMAIN
from custom_components.sentient.heating import comfort_target, usual_wake, warmup_rate
from custom_components.sentient.nightwatch import how_usual

def _t(day: int, h: int, m: int) -> datetime:
    return datetime(2026, 10, day, h, m, tzinfo=dt_util.get_default_time_zone())


def _clim(when: datetime, cur: float, target: float, state: str = "auto") -> State:
    return State("climate.heating", state, {"current_temperature": cur, "temperature": target},
                 last_changed=when, last_updated=when)


def test_warmup_rate_and_comfort() -> None:
    states = []
    for day in (12, 13, 14):
        states += [_clim(_t(day, 5, 0), 16.5, 15), _clim(_t(day, 6, 0), 16.2, 18), _clim(_t(day, 7, 0), 17.4, 18),
                   _clim(_t(day, 7, 30), 18.0, 18), _clim(_t(day, 22, 30), 18.2, 15)]
    rate, n = warmup_rate(states)
    assert n == 3 and rate == 1.2
    assert comfort_target(states) == 18


def test_usual_wake() -> None:
    sensors = {"binary_sensor.kitchen": []}
    for day in range(12, 26):  # 12 Oct 2026 is a Monday
        d = _t(day, 0, 0)
        minute = 6 * 60 + 35 if d.weekday() < 5 else 8 * 60 + 10
        when = d.replace(hour=minute // 60, minute=minute % 60)
        sensors["binary_sensor.kitchen"] += [State("binary_sensor.kitchen", "on", last_changed=when),
                                            State("binary_sensor.kitchen", "off", last_changed=when + timedelta(minutes=2)),
                                            State("binary_sensor.kitchen", "on", last_changed=when + timedelta(minutes=6))]
        sensors.setdefault("binary_sensor.landing", []).append(
            State("binary_sensor.landing", "on", last_changed=when + timedelta(minutes=3)))
        # a cat at 4:40 every night: one blip, not people up
        cat = d.replace(hour=4, minute=40)
        sensors["binary_sensor.kitchen"].insert(0, State("binary_sensor.kitchen", "on", last_changed=cat))
    wake = usual_wake(sensors)
    assert wake == {"weekdays": 6 * 60 + 35, "weekends": 8 * 60 + 10}
    # a light switched on by hand is the better evidence when there is one
    lights = {"light.kitchen": [_t(day, 6, 20) for day in range(12, 26) if _t(day, 0, 0).weekday() < 5]}
    assert usual_wake(sensors, lights)["weekdays"] == 6 * 60 + 20


def test_flapping_sensor_is_not_people_up() -> None:
    states = []
    for day in range(12, 26):
        base = _t(day, 4, 40)
        for k in range(6):  # off for a second, on again: one long movement, not six
            states += [State("binary_sensor.lounge", "off", last_changed=base + timedelta(minutes=k)),
                       State("binary_sensor.lounge", "on", last_changed=base + timedelta(minutes=k, seconds=1))]
    other = [State("binary_sensor.landing", "on", last_changed=_t(day, 4, 42)) for day in range(12, 26)]
    assert usual_wake({"binary_sensor.lounge": states, "binary_sensor.landing": other})["weekdays"] is None


async def _setup(hass: HomeAssistant, **extra) -> MockConfigEntry:
    hass.states.async_set("assist_satellite.bedroom", "idle", {"supported_features": 3})
    hass.states.async_set("binary_sensor.kitchen_motion", "off", {"device_class": "motion"})
    hass.states.async_set("binary_sensor.back_gate", "off", {"device_class": "door", "friendly_name": "Back gate"})
    entry = MockConfigEntry(domain=DOMAIN, title="Nova", unique_id=DOMAIN, data={
        "persona": "Nova", "speakers": ["assist_satellite.bedroom"], "default_speaker": "assist_satellite.bedroom",
        "quiet_start": "21:00:00", "quiet_end": "08:00:00", "inside_sensors": ["binary_sensor.kitchen_motion", "binary_sensor.landing_motion"],
        **extra,
    })
    entry.add_to_hass(hass)
    assert await hass.config_entries.async_setup(entry.entry_id)
    await hass.async_block_till_done()
    return entry


async def test_preheat_warms_early_then_restores_schedule(hass: HomeAssistant, freezer) -> None:
    freezer.move_to(dt_util.as_utc(_t(21, 3, 0)))  # a Wednesday
    hass.states.async_set("climate.heating", "auto", {
        "current_temperature": 16.0, "temperature": 15, "preset_mode": "auto", "preset_modes": ["auto", "manual"],
        "friendly_name": "Heating",
    })
    entry = await _setup(hass, preheat=True, heating_entities=["climate.heating"])
    heating = entry.runtime_data.heating
    heating.zones = {"climate.heating": {"rate": 1.0, "episodes": 5, "comfort": 18.0}}
    heating.wake = {"weekdays": 6 * 60 + 40, "weekends": None}
    set_temp = async_mock_service(hass, "climate", "set_temperature")
    set_preset = async_mock_service(hass, "climate", "set_preset_mode")
    plan = heating.plan("climate.heating")
    assert plan["needs_to_start"] == "04:40" and plan["warm_by"] == "06:40"
    for minute in (4 * 60 + 20, 4 * 60 + 36):
        when = dt_util.as_utc(_t(21, minute // 60, minute % 60).replace(second=5))
        freezer.move_to(when)
        async_fire_time_changed(hass, when)
        await hass.async_block_till_done()
    assert len(set_temp) == 1 and set_temp[0].data["temperature"] == 18.0
    hass.states.async_set("climate.heating", "auto", {
        "current_temperature": 18.0, "temperature": 18.0, "preset_mode": "manual", "preset_modes": ["auto", "manual"],
    })
    when = dt_util.as_utc(_t(21, 7, 26).replace(second=5))
    freezer.move_to(when)
    async_fire_time_changed(hass, when)
    await hass.async_block_till_done()
    assert set_preset and set_preset[0].data["preset_mode"] == "auto"


async def test_preheat_never_turns_on_a_zone_thats_off(hass: HomeAssistant, freezer) -> None:
    freezer.move_to(dt_util.as_utc(_t(21, 4, 36)))
    hass.states.async_set("climate.heating", "off", {"current_temperature": 16.0, "temperature": 15})
    entry = await _setup(hass, preheat=True, heating_entities=["climate.heating"])
    heating = entry.runtime_data.heating
    heating.zones = {"climate.heating": {"rate": 1.0, "episodes": 5, "comfort": 18.0}}
    heating.wake = {"weekdays": 6 * 60 + 40}
    set_temp = async_mock_service(hass, "climate", "set_temperature")
    async_fire_time_changed(hass, dt_util.as_utc(_t(21, 4, 37).replace(second=5)))
    await hass.async_block_till_done()
    assert not set_temp


def test_how_usual() -> None:
    openings = [_t(d, 6, 50) for d in range(1, 29) if _t(d, 0, 0).weekday() < 5]
    assert not how_usual(openings, _t(21, 6, 55), 28)["rare"]
    assert how_usual(openings, _t(21, 1, 10), 28)["rare"]


async def test_night_watch_alerts_only_when_nobody_was_up(hass: HomeAssistant, freezer) -> None:
    freezer.move_to(dt_util.as_utc(_t(21, 1, 0)))
    entry = await _setup(hass, night_watch_announce=True, night_watch_entities=["binary_sensor.back_gate"])
    rt = entry.runtime_data
    nw = rt.night_watch
    nw.history = {"binary_sensor.back_gate": [_t(d, 6, 50) for d in range(1, 21) if _t(d, 0, 0).weekday() < 5]}
    nw.days_known = 28
    said = async_mock_service(hass, "assist_satellite", "announce")

    hass.states.async_set("binary_sensor.back_gate", "on", {"device_class": "door", "friendly_name": "Back gate"})
    await hass.async_block_till_done()
    assert said and said[0].data["message"].startswith("Heads up: the back gate has just opened")
    assert any(i["kind"] == "security" and i.get("alert") for i in list(rt.timeline.items)[:3])

    # 6:50 on a weekday, someone up inside: the usual leaving for work — nothing
    hass.states.async_set("binary_sensor.back_gate", "off", {"device_class": "door"})
    freezer.move_to(dt_util.as_utc(_t(21, 6, 46)))
    hass.states.async_set("binary_sensor.landing_motion", "on", {"device_class": "motion"})
    hass.states.async_set("binary_sensor.landing_motion", "off", {"device_class": "motion"})
    freezer.move_to(dt_util.as_utc(_t(21, 6, 48)))
    hass.states.async_set("binary_sensor.kitchen_motion", "on", {"device_class": "motion"})
    freezer.move_to(dt_util.as_utc(_t(21, 6, 51)))
    hass.states.async_set("binary_sensor.back_gate", "on", {"device_class": "door"})
    await hass.async_block_till_done()
    assert len(said) == 1

    # unusual time but someone was up inside first: noted, not said
    hass.states.async_set("binary_sensor.back_gate", "off", {"device_class": "door"})
    freezer.move_to(dt_util.as_utc(_t(21, 3, 10)))
    hass.states.async_set("binary_sensor.kitchen_motion", "off", {"device_class": "motion"})
    hass.states.async_set("binary_sensor.landing_motion", "on", {"device_class": "motion"})
    hass.states.async_set("binary_sensor.landing_motion", "off", {"device_class": "motion"})
    freezer.move_to(dt_util.as_utc(_t(21, 3, 12)))
    hass.states.async_set("binary_sensor.kitchen_motion", "on", {"device_class": "motion"})
    hass.states.async_set("binary_sensor.back_gate", "on", {"device_class": "door"})
    await hass.async_block_till_done()
    assert len(said) == 1
    assert any(i["kind"] == "noticed" and "back gate" in i["text"].lower() for i in list(rt.timeline.items)[:3])
    facts = await hass.services.async_call(DOMAIN, "briefing", {"speak": False}, blocking=True, return_response=True)
    assert any("Back gate opened at 01:00" in x for x in facts.get("unusual_overnight", []))


async def test_weekly_report(hass: HomeAssistant) -> None:
    entry = await _setup(hass)
    entry.runtime_data.memory.add("The bins go out on Thursday")
    res = await hass.services.async_call(DOMAIN, "weekly_report", {}, blocking=True, return_response=True)
    assert "This week it spoke" in res["summary"] and res["new_memories"]
    assert entry.runtime_data.timeline.items[0]["kind"] == "learned"


async def test_a_single_blip_inside_is_not_someone_up(hass: HomeAssistant, freezer) -> None:
    freezer.move_to(dt_util.as_utc(_t(21, 1, 0)))
    entry = await _setup(hass, night_watch_announce=True, night_watch_entities=["binary_sensor.back_gate"])
    nw = entry.runtime_data.night_watch
    nw.history, nw.days_known = {"binary_sensor.back_gate": []}, 28
    said = async_mock_service(hass, "assist_satellite", "announce")
    for _ in range(4):  # the cat, or a sensor flickering: lots of movement, but only in one place
        hass.states.async_set("binary_sensor.kitchen_motion", "on", {"device_class": "motion"})
        hass.states.async_set("binary_sensor.kitchen_motion", "off", {"device_class": "motion"})
    freezer.move_to(dt_util.as_utc(_t(21, 1, 4)))
    hass.states.async_set("binary_sensor.back_gate", "on", {"device_class": "door", "friendly_name": "Back gate"})
    await hass.async_block_till_done()
    assert len(said) == 1


async def test_warm_by_setting_beats_learned_time(hass: HomeAssistant, freezer) -> None:
    freezer.move_to(dt_util.as_utc(_t(21, 3, 0)))
    hass.states.async_set("climate.heating", "auto", {"current_temperature": 16.0, "temperature": 15})
    entry = await _setup(hass, heating_entities=["climate.heating"], warm_by_weekdays="06:15:00")
    heating = entry.runtime_data.heating
    heating.zones = {"climate.heating": {"rate": 1.0, "episodes": 5, "comfort": 18.0}}
    heating.wake = {"weekdays": 7 * 60 + 30}
    plan = heating.plan("climate.heating")
    assert plan["warm_by"] == "06:15" and plan["needs_to_start"] == "04:15"


async def test_up_for_an_hour_then_out_the_door_and_gate_is_not_an_alert(hass: HomeAssistant, freezer) -> None:
    """Saturday 06:55: up since about six, in the kitchen for the last while, out of the front door then the gate."""
    hass.states.async_set("binary_sensor.front_door", "off", {"device_class": "door", "friendly_name": "Front door"})
    freezer.move_to(dt_util.as_utc(_t(24, 5, 50)))  # a Saturday
    entry = await _setup(hass, night_watch_announce=True,
                         night_watch_entities=["binary_sensor.back_gate", "binary_sensor.front_door"])
    nw = entry.runtime_data.night_watch
    nw.history, nw.days_known = {"binary_sensor.back_gate": [], "binary_sensor.front_door": []}, 28
    said = async_mock_service(hass, "assist_satellite", "announce")
    for minute, sensor in [(6, "landing"), (8, "kitchen"), (14, "kitchen"), (20, "landing"), (27, "kitchen"),
                           (33, "kitchen"), (41, "kitchen"), (48, "kitchen")]:
        freezer.move_to(dt_util.as_utc(_t(24, 6, minute)))
        hass.states.async_set(f"binary_sensor.{sensor}_motion", "on", {"device_class": "motion"})
        freezer.move_to(dt_util.as_utc(_t(24, 6, minute)) + timedelta(seconds=40))
        hass.states.async_set(f"binary_sensor.{sensor}_motion", "off", {"device_class": "motion"})
    freezer.move_to(dt_util.as_utc(_t(24, 6, 55)))
    hass.states.async_set("binary_sensor.front_door", "on", {"device_class": "door", "friendly_name": "Front door"})
    await hass.async_block_till_done()
    freezer.move_to(dt_util.as_utc(_t(24, 6, 56)))
    hass.states.async_set("binary_sensor.back_gate", "on", {"device_class": "door", "friendly_name": "Back gate"})
    await hass.async_block_till_done()
    assert not said
    assert not any(i.get("alert") for i in entry.runtime_data.timeline.items)


async def test_gate_then_back_door_with_nobody_up_is_said_once(hass: HomeAssistant, freezer) -> None:
    hass.states.async_set("binary_sensor.back_door", "off", {"device_class": "door", "friendly_name": "Back door"})
    freezer.move_to(dt_util.as_utc(_t(21, 1, 0)))
    entry = await _setup(hass, night_watch_announce=True,
                         night_watch_entities=["binary_sensor.back_gate", "binary_sensor.back_door"])
    nw = entry.runtime_data.night_watch
    nw.history, nw.days_known = {}, 28
    said = async_mock_service(hass, "assist_satellite", "announce")
    hass.states.async_set("binary_sensor.back_gate", "on", {"device_class": "door", "friendly_name": "Back gate"})
    await hass.async_block_till_done()
    freezer.move_to(dt_util.as_utc(_t(21, 1, 1)))
    hass.states.async_set("binary_sensor.back_door", "on", {"device_class": "door", "friendly_name": "Back door"})
    await hass.async_block_till_done()
    assert len(said) == 1
    assert sum(1 for i in entry.runtime_data.timeline.items if i.get("alert")) == 2
