"""Welcome home."""

from datetime import timedelta

from homeassistant.core import HomeAssistant, ServiceCall, SupportsResponse
from homeassistant.util import dt as dt_util
from pytest_homeassistant_custom_component.common import MockConfigEntry, async_fire_time_changed, async_mock_service

from custom_components.sentient.const import DOMAIN, EVENT_WATCH_FIRED

OPTIONS = {"persona": "Nova", "speakers": ["assist_satellite.kitchen"], "default_speaker": "assist_satellite.kitchen",
           "quiet_start": "00:00:00", "quiet_end": "00:00:00", "welcome_home": True}


async def test_welcome_back_with_news(hass: HomeAssistant, freezer) -> None:
    entry = MockConfigEntry(domain=DOMAIN, title="Nova", unique_id=DOMAIN, data=OPTIONS)
    entry.add_to_hass(hass)
    assert await hass.config_entries.async_setup(entry.entry_id)
    await hass.async_block_till_done()
    hass.states.async_set("assist_satellite.kitchen", "idle")
    said = async_mock_service(hass, "assist_satellite", "announce")

    async def generate(call: ServiceCall):
        assert "The washer has finished." in call.data["instructions"]
        return {"conversation_id": "x", "data": "Welcome back. The washing's done."}

    hass.services.async_register("ai_task", "generate_data", generate, supports_response=SupportsResponse.ONLY)
    hass.states.async_set("person.sam", "not_home")
    freezer.tick(timedelta(minutes=30))
    hass.bus.async_fire(EVENT_WATCH_FIRED, {"what": "washer", "message": "The washer has finished."})
    await hass.async_block_till_done()
    hass.states.async_set("person.sam", "home")
    hass.states.async_set("person.alex", "not_home")  # (separate, ignored)
    await hass.async_block_till_done()
    assert not said
    freezer.tick(timedelta(seconds=80))
    async_fire_time_changed(hass)
    await hass.async_block_till_done()
    assert said[-1].data["message"] == "Welcome back. The washing's done."


async def test_short_trip_no_welcome(hass: HomeAssistant, freezer) -> None:
    entry = MockConfigEntry(domain=DOMAIN, title="Nova", unique_id=DOMAIN, data=OPTIONS)
    entry.add_to_hass(hass)
    assert await hass.config_entries.async_setup(entry.entry_id)
    await hass.async_block_till_done()
    said = async_mock_service(hass, "assist_satellite", "announce")
    hass.states.async_set("person.sam", "not_home")
    freezer.tick(timedelta(minutes=5))
    hass.states.async_set("person.sam", "home")
    freezer.tick(timedelta(seconds=80))
    async_fire_time_changed(hass)
    await hass.async_block_till_done()
    assert not said
