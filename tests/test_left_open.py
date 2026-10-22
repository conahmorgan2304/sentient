"""Left open nudges and the leaving check."""

from datetime import timedelta

from homeassistant.core import HomeAssistant
from pytest_homeassistant_custom_component.common import MockConfigEntry, async_fire_time_changed, async_mock_service

from custom_components.sentient.const import DOMAIN

OPTIONS = {"persona": "Nova", "speakers": ["assist_satellite.kitchen"], "default_speaker": "assist_satellite.kitchen",
           "quiet_start": "00:00:00", "quiet_end": "00:00:00", "left_open_minutes": 15,
           "notify_targets": ["notify.phone"]}


async def test_left_open_and_leaving(hass: HomeAssistant, freezer) -> None:
    entry = MockConfigEntry(domain=DOMAIN, title="Nova", unique_id=DOMAIN, data=OPTIONS)
    entry.add_to_hass(hass)
    assert await hass.config_entries.async_setup(entry.entry_id)
    await hass.async_block_till_done()
    hass.states.async_set("assist_satellite.kitchen", "idle")
    hass.states.async_set("notify.phone", "unknown")
    said = async_mock_service(hass, "assist_satellite", "announce")
    sent = async_mock_service(hass, "notify", "send_message")
    hass.states.async_set("binary_sensor.back_door", "on", {"device_class": "door", "friendly_name": "Back door"})
    for _ in range(16):
        freezer.tick(timedelta(minutes=1))
        async_fire_time_changed(hass)
        await hass.async_block_till_done()
    assert [c.data["message"] for c in said] == ["Back door has been open for 15 minutes."]
    for _ in range(5):
        freezer.tick(timedelta(minutes=1))
        async_fire_time_changed(hass)
        await hass.async_block_till_done()
    assert len(said) == 1  # not again for half an hour

    hass.states.async_set("person.sam", "home")
    await hass.async_block_till_done()
    hass.states.async_set("person.sam", "not_home")
    await hass.async_block_till_done()
    assert sent and sent[-1].data["title"] == "Nova · Left open"
    assert sent[-1].data["message"] == "Back door is open."
