"""Real-day sequences: the timing problems that unit tests of single features miss."""

import asyncio
import logging
from datetime import timedelta
from unittest.mock import patch

from homeassistant.core import Context, HomeAssistant
from pytest_homeassistant_custom_component.common import MockConfigEntry, async_fire_time_changed, async_mock_service

from custom_components.sentient.const import DOMAIN

OPTIONS = {"persona": "Nova", "speakers": ["assist_satellite.kitchen"], "default_speaker": "assist_satellite.kitchen",
           "quiet_start": "00:00:00", "quiet_end": "00:00:00", "morning_briefing": True, "welcome_home": True,
           "critical_alerts": True, "leaving_check": True}


async def _setup(hass: HomeAssistant, **extra) -> MockConfigEntry:
    hass.states.async_set("assist_satellite.kitchen", "idle", {"supported_features": 3, "friendly_name": "Kitchen"})
    entry = MockConfigEntry(domain=DOMAIN, title="Nova", unique_id=DOMAIN, data={**OPTIONS, **extra})
    entry.add_to_hass(hass)
    assert await hass.config_entries.async_setup(entry.entry_id)
    await hass.async_block_till_done()
    return entry


async def test_sensors_firing_while_sentient_starts(hass: HomeAssistant, caplog) -> None:
    """A restart: doors, motion and people change while Sentient is still setting up — no errors."""
    caplog.set_level(logging.ERROR)
    entry = MockConfigEntry(domain=DOMAIN, title="Nova", unique_id=DOMAIN, data=OPTIONS)
    entry.add_to_hass(hass)
    setup = hass.async_create_task(hass.config_entries.async_setup(entry.entry_id))
    for n in range(20):
        hass.states.async_set("binary_sensor.door", "on" if n % 2 else "off", {"device_class": "door"})
        hass.states.async_set("binary_sensor.motion", "on" if n % 2 else "off", {"device_class": "motion"})
        hass.states.async_set("person.sam", "home" if n % 2 else "not_home")
        await asyncio.sleep(0)
    assert await setup
    await hass.async_block_till_done()
    assert not [r for r in caplog.records if r.levelno >= logging.ERROR and "sentient" in r.name]


async def test_a_story_on_a_speaker_that_stops_early_has_no_long_gap(hass: HomeAssistant) -> None:
    """Speakers that say when they've finished get no padding; ones that hand back straight away do."""
    entry = await _setup(hass)
    speech = entry.runtime_data.speech
    waits: list[float] = []
    real_sleep = asyncio.sleep

    async def fake_sleep(seconds, *a, **k):
        waits.append(seconds)
        await real_sleep(0)

    async def blocking_speak(text, speakers, chime=None):
        await real_sleep(0.15)  # the satellite reads it, then reports back (here: finished early)
        return [{"entity_id": speakers[0], "status": "accepted"}]

    passage = "word " * 120  # about 50 seconds by the estimate
    with patch("custom_components.sentient.speech.INSTANT_SECONDS", 0.1), \
            patch("custom_components.sentient.speech.asyncio.sleep", fake_sleep), \
            patch.object(speech, "_speak", blocking_speak):
        assert await speech.async_read_aloud(passage, "assist_satellite.kitchen", story=True)
    assert not [w for w in waits if w > 5]  # no long padding after a speaker that reported back


async def test_the_ai_failing_then_noticed_and_said(hass: HomeAssistant) -> None:
    """Three backup answers in an hour: said straight away as a health problem, once."""
    entry = await _setup(hass)
    rt = entry.runtime_data
    for _ in range(4):
        rt.timeline.add("health", "Voice answered by the backup agent", "the AI failed: 503")
        rt.selfaware.voice_fell_back("503")
    told = [i for i in rt.timeline.since(1, ["health"], 50) if i["text"].startswith("The AI hasn't answered")]
    assert len(told) == 1


async def test_offer_then_already_done_then_a_light_switched_back(hass: HomeAssistant, freezer) -> None:
    """A busy evening: an offer answered 'it's already on', then a voice request someone undoes by hand."""
    entry = await _setup(hass)
    rt = entry.runtime_data
    said = async_mock_service(hass, "assist_satellite", "announce")
    async_mock_service(hass, "assist_satellite", "start_conversation")
    heat = async_mock_service(hass, "climate", "set_temperature")
    async_mock_service(hass, "light", "turn_on")
    offer = rt.lists["offers"].upsert({"name": "Heating on", "actions": [
        {"action": "climate.set_temperature", "target": {"entity_id": "climate.heating"}, "data": {"temperature": 20}}]})
    await rt.offers.async_ask(offer)
    reply = await rt.speech.async_take_answer("assist_satellite.kitchen", "it's already on")
    assert reply and not heat  # nothing was done, and it wasn't taken as a yes
    hass.states.async_set("light.girls", "off", {"friendly_name": "Girls light"})
    hass.states.async_set("assist_satellite.kitchen", "listening")
    hass.states.async_set("assist_satellite.kitchen", "idle")
    await hass.services.async_call("light", "turn_on", {"entity_id": "light.girls"}, blocking=True, context=Context())
    hass.states.async_set("light.girls", "on", {"friendly_name": "Girls light"})
    hass.states.async_set("light.girls", "off", {"friendly_name": "Girls light"})  # switched straight back
    for _ in range(3):
        freezer.tick(timedelta(seconds=60))
        async_fire_time_changed(hass)
        await hass.async_block_till_done()
    assert not [c for c in said if "didn't respond" in c.data.get("message", "")]
