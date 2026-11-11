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
    assert broken(hass, options) == []  # just noticed missing: could be a restart or a reload
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


async def test_restored_entities_are_dead_even_straight_after_a_restart(hass: HomeAssistant) -> None:
    """A restored (no longer provided) entity is reported although its last_changed is only seconds old."""
    hass.states.async_set("binary_sensor.old_car", "unavailable", {"restored": True})
    hass.states.async_set("binary_sensor.blip", "unavailable")  # just went offline: not news yet
    found = broken(hass, {"drive_triggers": ["binary_sensor.old_car", "binary_sensor.blip"],
                          "learn_entities": ["binary_sensor.old_car"]})
    assert {(f["entity_id"], f["setting"]) for f in found} == {
        ("binary_sensor.old_car", "drive sensors"), ("binary_sensor.old_car", "habit learning")}


async def test_an_unplugged_habit_switch_is_not_a_problem(hass: HomeAssistant, freezer) -> None:
    hass.states.async_set("switch.projector", "unavailable")
    freezer.tick(timedelta(hours=5))
    assert broken(hass, {"learn_entities": ["switch.projector"]}) == []
    assert broken(hass, {"drive_triggers": ["switch.projector"]})  # but a doorbell or drive sensor is


async def test_an_entity_missing_during_a_reload_is_never_called_gone(hass: HomeAssistant, freezer) -> None:
    """10 Nov: ~30 chosen devices were reported "no longer exists" (and phoned) while integrations reloaded."""
    options = {"speakers": ["media_player.kitchen_echo"]}
    assert broken(hass, options) == []
    freezer.tick(timedelta(minutes=10))
    assert broken(hass, options) == []
    hass.states.async_set("media_player.kitchen_echo", "idle")  # back again
    assert broken(hass, options) == []
    hass.states.async_remove("media_player.kitchen_echo")  # gone again later: the 15 minutes start afresh
    freezer.tick(timedelta(minutes=10))
    assert broken(hass, options) == []
    freezer.tick(timedelta(minutes=16))
    assert [i["gone"] for i in broken(hass, options)] == [True]


async def test_an_entity_whose_integration_is_still_starting_is_not_gone(hass: HomeAssistant, freezer) -> None:
    from homeassistant.helpers import entity_registry as er
    from pytest_homeassistant_custom_component.common import MockConfigEntry

    entry = MockConfigEntry(domain="alexa_media")
    entry.add_to_hass(hass)  # added but not set up: as during a restart or a reload
    er.async_get(hass).async_get_or_create("media_player", "alexa_media", "echo1", config_entry=entry,
                                           suggested_object_id="kitchen_echo")
    options = {"speakers": ["media_player.kitchen_echo"]}
    broken(hass, options)
    freezer.tick(timedelta(hours=2))
    assert broken(hass, options) == []
