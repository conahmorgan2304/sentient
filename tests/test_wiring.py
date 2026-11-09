"""House health names settings that point at things that are gone or long dead."""

from datetime import timedelta

from homeassistant.core import HomeAssistant

from custom_components.sentient.health import HouseHealth
from custom_components.sentient.wiring import broken


async def test_missing_and_long_dead_entities_are_reported(hass: HomeAssistant, freezer) -> None:
    hass.states.async_set("binary_sensor.doorbell", "off")
    hass.states.async_set("binary_sensor.car_occupancy", "unavailable")
    hass.states.async_set("assist_satellite.kitchen", "idle")
    options = {
        "door_triggers": ["binary_sensor.doorbell", "binary_sensor.deleted_bell"],
        "drive_triggers": ["binary_sensor.car_occupancy"],
        "speakers": ["assist_satellite.kitchen", "media_player.gone_echo"],
        "notify_targets": ["notify.old_style_phone"],
        "health_ignore": ["media_player.gone_echo"],
    }
    async def noop(call): ...
    hass.services.async_register("notify", "old_style_phone", noop)
    freezer.tick(timedelta(minutes=30))
    found = {(i["entity_id"], i["gone"]) for i in broken(hass, options)}
    assert found == {("binary_sensor.deleted_bell", True)}  # the dead one isn't dead for an hour yet
    freezer.tick(timedelta(hours=2))
    found = {(i["entity_id"], i["gone"]) for i in broken(hass, options)}
    assert found == {("binary_sensor.deleted_bell", True), ("binary_sensor.car_occupancy", False)}


async def test_health_says_it_in_words(hass: HomeAssistant) -> None:
    health = HouseHealth(hass, {"door_triggers": ["binary_sensor.deleted_bell"]}, lambda: object())
    health.issues = [{"kind": "wiring", "name": "binary_sensor.deleted_bell", "entity_id": "binary_sensor.deleted_bell",
                      "setting": "doorbell", "gone": True}]
    assert health.describe() == ["binary_sensor.deleted_bell is chosen in Sentient's settings (doorbell) but no longer exists"]
