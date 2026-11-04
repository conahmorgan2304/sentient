"""Saying so when a voice request didn't happen; heating time limit."""

from datetime import timedelta

from homeassistant.core import Context, HomeAssistant
from homeassistant.util import dt as dt_util
from pytest_homeassistant_custom_component.common import (
    MockConfigEntry,
    async_fire_time_changed,
    async_mock_service,
)

from custom_components.sentient.const import DOMAIN


async def _setup(hass: HomeAssistant, **extra) -> MockConfigEntry:
    hass.states.async_set("assist_satellite.kitchen", "idle", {"supported_features": 3})
    entry = MockConfigEntry(domain=DOMAIN, title="Nova", unique_id=DOMAIN, data={
        "persona": "Nova", "speakers": ["assist_satellite.kitchen"], "default_speaker": "assist_satellite.kitchen",
        "quiet_start": "00:00:00", "quiet_end": "00:00:00", **extra,
    })
    entry.add_to_hass(hass)
    assert await hass.config_entries.async_setup(entry.entry_id)
    await hass.async_block_till_done()
    return entry


async def test_says_so_only_when_it_did_not_happen(hass: HomeAssistant, freezer) -> None:
    await _setup(hass)
    said = async_mock_service(hass, "assist_satellite", "announce")
    hass.states.async_set("climate.heating", "unavailable", {"friendly_name": "Heating"})
    hass.states.async_set("light.lamp", "off", {"friendly_name": "Lamp"})
    async_mock_service(hass, "climate", "turn_on")
    async_mock_service(hass, "light", "turn_on")
    # someone just spoke to the kitchen speaker
    hass.states.async_set("assist_satellite.kitchen", "listening")
    hass.states.async_set("assist_satellite.kitchen", "idle")
    await hass.services.async_call("climate", "turn_on", {"entity_id": "climate.heating"}, blocking=True, context=Context())
    await hass.services.async_call("light", "turn_on", {"entity_id": "light.lamp"}, blocking=True, context=Context())
    hass.states.async_set("light.lamp", "on", {"friendly_name": "Lamp"})  # the lamp did come on
    freezer.tick(timedelta(seconds=21))
    async_fire_time_changed(hass)
    await hass.async_block_till_done()
    assert [c.data["message"] for c in said] == [
        "Sorry, the heating didn't respond, so it isn't on. It looks offline. If it runs on batteries, they may be flat."
    ]
    assert said[0].data.get("entity_id", said[0].data) is not None


async def test_patient_with_slow_devices_newest_request_wins_and_a_later_change_by_hand_is_fine(
    hass: HomeAssistant, freezer
) -> None:
    await _setup(hass)
    said = async_mock_service(hass, "assist_satellite", "announce")
    async_mock_service(hass, "climate", "set_temperature")
    async_mock_service(hass, "light", "turn_on")
    hass.states.async_set("climate.heating", "heat", {"temperature": 18, "friendly_name": "Heating"})
    hass.states.async_set("light.girls", "off", {"friendly_name": "Girls light"})
    hass.states.async_set("assist_satellite.kitchen", "listening")
    hass.states.async_set("assist_satellite.kitchen", "idle")

    async def tick(seconds):
        freezer.tick(timedelta(seconds=seconds))
        async_fire_time_changed(hass)
        await hass.async_block_till_done()

    # the offer sets 20, then the voice sets 21: only 21 is checked
    await hass.services.async_call("climate", "set_temperature", {"entity_id": "climate.heating", "temperature": 20}, blocking=True, context=Context())
    await hass.services.async_call("climate", "set_temperature", {"entity_id": "climate.heating", "temperature": 21}, blocking=True, context=Context())
    # the light comes on, then one of the children switches it off again
    await hass.services.async_call("light", "turn_on", {"entity_id": "light.girls"}, blocking=True, context=Context())
    hass.states.async_set("light.girls", "on", {"friendly_name": "Girls light"})
    hass.states.async_set("light.girls", "off", {"friendly_name": "Girls light"})
    await tick(21)
    assert not said  # the thermostat hasn't reported back yet: wait longer, don't complain
    hass.states.async_set("climate.heating", "heat", {"temperature": 21, "friendly_name": "Heating"})
    await tick(91)
    assert not said


async def test_quiet_when_nobody_asked_by_voice(hass: HomeAssistant, freezer) -> None:
    await _setup(hass)
    said = async_mock_service(hass, "assist_satellite", "announce")
    hass.states.async_set("climate.heating", "unavailable")
    async_mock_service(hass, "climate", "turn_on")
    await hass.services.async_call("climate", "turn_on", {"entity_id": "climate.heating"}, blocking=True)
    freezer.tick(timedelta(seconds=21))
    async_fire_time_changed(hass)
    await hass.async_block_till_done()
    assert not said


async def test_heating_turned_up_by_hand_goes_back_after_the_limit(hass: HomeAssistant, freezer) -> None:
    hass.states.async_set("climate.heating", "auto", {
        "temperature": 15, "current_temperature": 16, "preset_mode": "auto", "preset_modes": ["auto", "manual"],
        "friendly_name": "Heating",
    })
    entry = await _setup(hass, heating_entities=["climate.heating"], heating_limit=2)
    async_mock_service(hass, "climate", "set_temperature")
    presets = async_mock_service(hass, "climate", "set_preset_mode")
    await hass.services.async_call("climate", "set_temperature", {"entity_id": "climate.heating", "temperature": 21}, blocking=True)
    hass.states.async_set("climate.heating", "auto", {
        "temperature": 21, "current_temperature": 16, "preset_mode": "manual", "preset_modes": ["auto", "manual"],
    })
    assert "climate.heating" in entry.runtime_data.heating._boosted
    freezer.tick(timedelta(hours=2, minutes=1))
    async_fire_time_changed(hass)
    await hass.async_block_till_done()
    assert presets and presets[0].data["preset_mode"] == "auto"


async def test_heating_limit_is_off_by_default_and_never_on_unchosen_thermostats(hass: HomeAssistant) -> None:
    for eid in ("climate.heating", "climate.office"):
        hass.states.async_set(eid, "heat", {"temperature": 15, "current_temperature": 16, "friendly_name": eid})
    async_mock_service(hass, "climate", "set_temperature")
    entry = await _setup(hass)  # no limit set, no thermostats chosen
    await hass.services.async_call("climate", "set_temperature", {"entity_id": "climate.heating", "temperature": 21}, blocking=True)
    assert not entry.runtime_data.heating._boosted
    await hass.config_entries.async_unload(entry.entry_id)
    await hass.async_block_till_done()
    hass.config_entries.async_update_entry(entry, data={**entry.data, "heating_limit": 2, "heating_entities": ["climate.heating"]})
    assert await hass.config_entries.async_setup(entry.entry_id)
    await hass.async_block_till_done()
    await hass.services.async_call("climate", "set_temperature", {"entity_id": "climate.office", "temperature": 21}, blocking=True)
    assert "climate.office" not in entry.runtime_data.heating._boosted  # never picked in Sentient


async def test_an_announcement_is_not_someone_asking(hass: HomeAssistant, freezer) -> None:
    await _setup(hass)
    said = async_mock_service(hass, "assist_satellite", "announce")
    hass.states.async_set("switch.popup", "on", {"friendly_name": "Popup"})
    async_mock_service(hass, "switch", "turn_off")
    # the satellite reading out an announcement goes to "responding" — nobody spoke to it
    hass.states.async_set("assist_satellite.kitchen", "responding")
    hass.states.async_set("assist_satellite.kitchen", "idle")
    await hass.services.async_call("switch", "turn_off", {"entity_id": "switch.popup"}, blocking=True, context=Context())
    freezer.tick(timedelta(seconds=21))
    async_fire_time_changed(hass)
    await hass.async_block_till_done()
    assert not said
