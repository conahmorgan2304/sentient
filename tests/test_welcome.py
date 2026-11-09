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


async def test_welcome_from_a_phone_when_the_person_is_always_home(hass: HomeAssistant, freezer) -> None:
    """A house whose person entity never leaves can pick a phone's tracker instead."""
    entry = MockConfigEntry(domain=DOMAIN, title="Nova", unique_id=DOMAIN,
                            data={**OPTIONS, "welcome_people": ["device_tracker.phone"]})
    entry.add_to_hass(hass)
    assert await hass.config_entries.async_setup(entry.entry_id)
    await hass.async_block_till_done()
    hass.states.async_set("assist_satellite.kitchen", "idle")
    said = async_mock_service(hass, "assist_satellite", "announce")
    hass.states.async_set("person.sam", "home")
    hass.states.async_set("device_tracker.phone", "not_home")
    freezer.tick(timedelta(minutes=45))
    hass.states.async_set("device_tracker.phone", "home")
    await hass.async_block_till_done()
    freezer.tick(timedelta(seconds=80))
    async_fire_time_changed(hass)
    await hass.async_block_till_done()
    assert said and said[-1].data["message"].startswith("Welcome back")


async def test_minimum_time_away_is_a_setting(hass: HomeAssistant) -> None:
    from custom_components.sentient.welcome import Welcome

    assert Welcome(hass, {}, lambda: None).min_away == timedelta(minutes=20)
    assert Welcome(hass, {"welcome_min_away": 60}, lambda: None).min_away == timedelta(minutes=60)
    assert Welcome(hass, {"welcome_min_away": "junk"}, lambda: None).min_away == timedelta(minutes=20)
    assert Welcome(hass, {"welcome_min_away": 1}, lambda: None).min_away == timedelta(minutes=5)


async def test_evening_allowance_and_held_back_note(hass: HomeAssistant, freezer) -> None:
    from types import SimpleNamespace
    from unittest.mock import AsyncMock, MagicMock

    from custom_components.sentient.welcome import Welcome

    await hass.config.async_set_time_zone("UTC")
    freezer.move_to("2026-11-09 21:30:00+00:00")
    speech = SimpleNamespace(guest_mode=False, quiet_now=lambda: True, async_say=AsyncMock())
    rt = SimpleNamespace(speech=speech, timeline=MagicMock())
    held = Welcome(hass, {}, lambda: rt)
    assert await held.async_welcome(dt_util.utcnow() - timedelta(hours=2)) == {"spoken": False, "reason": "quiet"}
    assert "quiet hours" in rt.timeline.add.call_args.args[1]
    assert not Welcome(hass, {"welcome_until": "21:00:00"}, lambda: rt)._evening_allowed()  # too late
    assert Welcome(hass, {"welcome_until": "23:00:00"}, lambda: rt)._evening_allowed()
    freezer.move_to("2026-11-09 02:00:00+00:00")
    assert not Welcome(hass, {"welcome_until": "23:00:00"}, lambda: rt)._evening_allowed()  # small hours stay quiet
