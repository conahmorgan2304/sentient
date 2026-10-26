"""Camera security: journeys, over the fence, at night."""

from datetime import datetime, timedelta

from homeassistant.core import HomeAssistant
from homeassistant.util import dt as dt_util
from pytest_homeassistant_custom_component.common import (
    MockConfigEntry,
    async_fire_time_changed,
    async_mock_service,
)

from custom_components.sentient.const import DOMAIN


def _t(day: int, h: int, m: int) -> datetime:
    return datetime(2026, 10, day, h, m, tzinfo=dt_util.get_default_time_zone())


async def _setup(hass: HomeAssistant) -> MockConfigEntry:
    for s, name in (("gate_person", "Gate Person occupancy"), ("garden_person", "Garden Person occupancy"),
                    ("shed_person", "Shed Person occupancy")):
        hass.states.async_set(f"binary_sensor.{s}", "off", {"friendly_name": name})
    hass.states.async_set("binary_sensor.back_gate", "off", {"device_class": "door"})
    hass.states.async_set("assist_satellite.bedroom", "idle", {"supported_features": 3})
    entry = MockConfigEntry(domain=DOMAIN, title="Nova", unique_id=DOMAIN, data={
        "persona": "Nova", "speakers": ["assist_satellite.bedroom"], "default_speaker": "assist_satellite.bedroom",
        "quiet_start": "21:00:00", "quiet_end": "07:00:00", "camera_security": True,
        "camera_security_sensors": ["binary_sensor.gate_person", "binary_sensor.garden_person", "binary_sensor.shed_person"],
        "camera_security_inside": ["binary_sensor.garden_person", "binary_sensor.shed_person"],
        "camera_security_entries": ["binary_sensor.back_gate"], "camera_security_describe": False,
        "night_watch_announce": True, "inside_sensors": [],
    })
    entry.add_to_hass(hass)
    assert await hass.config_entries.async_setup(entry.entry_id)
    await hass.async_block_till_done()
    return entry


async def _see(hass, sensor, on=True):
    name = sensor.split("_")[0].title() + " Person occupancy"
    hass.states.async_set(f"binary_sensor.{sensor}", "on" if on else "off", {"friendly_name": name})
    await hass.async_block_till_done()


async def test_daytime_journey_through_the_gate_is_logged_not_alerted(hass: HomeAssistant, freezer) -> None:
    freezer.move_to(dt_util.as_utc(_t(21, 14, 0)))
    entry = await _setup(hass)
    said = async_mock_service(hass, "assist_satellite", "announce")
    await _see(hass, "gate_person")
    hass.states.async_set("binary_sensor.back_gate", "on", {"device_class": "door"})
    freezer.tick(timedelta(seconds=40))
    await _see(hass, "gate_person", False)
    await _see(hass, "garden_person")
    freezer.tick(timedelta(minutes=1))
    await _see(hass, "shed_person")
    freezer.tick(timedelta(minutes=4))
    async_fire_time_changed(hass)
    await hass.async_block_till_done()
    items = list(entry.runtime_data.timeline.items)
    assert any(i["text"] == "Someone walked Gate → Garden → Shed" for i in items)
    assert not any(i.get("alert") for i in items) and not said


async def test_daytime_pottering_is_not_logged_and_long_trips_are_short(hass: HomeAssistant, freezer) -> None:
    freezer.move_to(dt_util.as_utc(_t(21, 14, 0)))
    entry = await _setup(hass)
    async_mock_service(hass, "assist_satellite", "announce")
    hass.states.async_set("binary_sensor.back_gate", "on", {"device_class": "door"})
    # garden → shed → garden → shed…: someone pottering about inside, not news in the day
    for _ in range(3):
        await _see(hass, "garden_person")
        await _see(hass, "garden_person", False)
        await _see(hass, "shed_person")
        await _see(hass, "shed_person", False)
        freezer.tick(timedelta(seconds=30))
    freezer.tick(timedelta(minutes=4))
    async_fire_time_changed(hass)
    await hass.async_block_till_done()
    assert not any(i["text"].startswith("Someone walked") for i in entry.runtime_data.timeline.items)
    # in from the gate, back and forth a lot: one short line, each place once
    for s in ("gate_person", "garden_person", "shed_person", "garden_person", "shed_person", "gate_person"):
        await _see(hass, s)
        await _see(hass, s, False)
        freezer.tick(timedelta(seconds=20))
    freezer.tick(timedelta(minutes=4))
    async_fire_time_changed(hass)
    await hass.async_block_till_done()
    walked = [i["text"] for i in entry.runtime_data.timeline.items if i["text"].startswith("Someone walked")]
    assert walked == ["Someone walked Gate → Garden → Shed"]


async def test_in_the_garden_without_the_gate_opening_is_an_alert(hass: HomeAssistant, freezer) -> None:
    freezer.move_to(dt_util.as_utc(_t(21, 14, 0)))
    entry = await _setup(hass)
    freezer.tick(timedelta(minutes=20))
    await _see(hass, "garden_person")
    alerts = [i for i in entry.runtime_data.timeline.items if i.get("alert")]
    assert alerts and alerts[0]["text"].startswith("Someone is in the garden, but the gate and doors didn't open")


async def test_anyone_at_night_with_nobody_up_is_said(hass: HomeAssistant, freezer) -> None:
    freezer.move_to(dt_util.as_utc(_t(21, 2, 0)))
    entry = await _setup(hass)
    said = async_mock_service(hass, "assist_satellite", "announce")
    await _see(hass, "gate_person")
    assert said and said[0].data["message"].startswith("Heads up: someone is by the gate and nobody's up")
