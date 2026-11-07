"""Speaking in the house's language: Sentient's own English sentences are translated before they're said."""

import pytest
from homeassistant.core import HomeAssistant, ServiceCall, SupportsResponse
from homeassistant.exceptions import HomeAssistantError
from pytest_homeassistant_custom_component.common import MockConfigEntry, async_mock_service

from custom_components.sentient import language
from custom_components.sentient.const import DOMAIN
from custom_components.sentient.language import async_in_house_language, looks_english

OPTIONS = {"persona": "Nova", "speakers": ["assist_satellite.kitchen"], "default_speaker": "assist_satellite.kitchen",
           "quiet_start": "00:00:00", "quiet_end": "00:00:00", "speak_house_language": True}


@pytest.fixture(autouse=True)
def _fresh_cache():
    language._kept.clear()
    yield
    language._kept.clear()


async def _setup(hass: HomeAssistant, reply: str | None = "Pesukone on valmis.", **options) -> list[ServiceCall]:
    hass.config.language = options.pop("house_language", "fi")
    hass.states.async_set("assist_satellite.kitchen", "idle", {"friendly_name": "Kitchen"})
    entry = MockConfigEntry(domain=DOMAIN, title="Nova", unique_id=DOMAIN, data={**OPTIONS, **options})
    entry.add_to_hass(hass)
    assert await hass.config_entries.async_setup(entry.entry_id)
    await hass.async_block_till_done()
    calls: list[ServiceCall] = []

    async def generate(call: ServiceCall):
        calls.append(call)
        if reply is None:
            raise HomeAssistantError("the AI is down")
        return {"data": reply}

    hass.services.async_register("ai_task", "generate_data", generate, supports_response=SupportsResponse.ONLY)
    return calls


@pytest.mark.parametrize("text", [
    "Pesukone Job's finished.", "Welcome back.", "The back door has been open for ten minutes.",
    "Reminder: put the oven on.", "Smoke in the kitchen!",
])
def test_english_is_recognised(text: str) -> None:
    assert looks_english(text)


@pytest.mark.parametrize("text", [
    "Pesukone on valmis.", "Ilta on viilentynyt 1,2 asteeseen ja taivas on puolipilvinen.",
    "Saisinko selvennyksen, tarkoitetaanko vessan valojen ohjauksella yleensä vain kattovaloa?",
    "Die Waschmaschine ist fertig.", "",
])
def test_other_languages_are_left_alone(text: str) -> None:
    assert not looks_english(text)


async def test_english_sentence_is_said_in_the_house_language(hass: HomeAssistant) -> None:
    ai = await _setup(hass)
    said = async_mock_service(hass, "assist_satellite", "announce")
    out = await hass.services.async_call(DOMAIN, "say", {"message": "Pesukone Job's finished."},
                                         blocking=True, return_response=True)
    assert said[0].data["message"] == "Pesukone on valmis."
    assert out["message"] == "Pesukone on valmis."
    assert len(ai) == 1 and "'fi'" in ai[0].data["instructions"] and "Pesukone Job's finished." in ai[0].data["instructions"]


async def test_each_sentence_is_translated_once(hass: HomeAssistant) -> None:
    ai = await _setup(hass)
    options = hass.config_entries.async_entries(DOMAIN)[0].data
    first = await async_in_house_language(hass, options, "Pesukone Job's finished.")
    again = await async_in_house_language(hass, options, "Pesukone Job's finished.")
    assert first == again == "Pesukone on valmis." and len(ai) == 1


async def test_house_language_sentences_cost_nothing(hass: HomeAssistant) -> None:
    ai = await _setup(hass)
    said = async_mock_service(hass, "assist_satellite", "announce")
    await hass.services.async_call(DOMAIN, "say", {"message": "Pesukone on valmis."}, blocking=True)
    assert said[0].data["message"] == "Pesukone on valmis." and not ai


async def test_off_by_default_and_when_switched_off(hass: HomeAssistant) -> None:
    ai = await _setup(hass, speak_house_language=False)
    said = async_mock_service(hass, "assist_satellite", "announce")
    await hass.services.async_call(DOMAIN, "say", {"message": "Pesukone Job's finished."}, blocking=True)
    assert said[0].data["message"] == "Pesukone Job's finished." and not ai


async def test_english_house_needs_no_translation(hass: HomeAssistant) -> None:
    ai = await _setup(hass, house_language="en-GB")
    said = async_mock_service(hass, "assist_satellite", "announce")
    await hass.services.async_call(DOMAIN, "say", {"message": "The washing has finished."}, blocking=True)
    assert said[0].data["message"] == "The washing has finished." and not ai


async def test_english_is_said_when_the_ai_is_down(hass: HomeAssistant) -> None:
    ai = await _setup(hass, reply=None)
    said = async_mock_service(hass, "assist_satellite", "announce")
    out = await hass.services.async_call(DOMAIN, "say", {"message": "Pesukone Job's finished."},
                                         blocking=True, return_response=True)
    assert len(ai) == 1 and said[0].data["message"] == "Pesukone Job's finished." and out["spoken"]


async def test_english_is_said_once_todays_ai_limit_is_used(hass: HomeAssistant) -> None:
    ai = await _setup(hass, daily_ai_limit=1)
    hass.config_entries.async_entries(DOMAIN)[0].runtime_data.ai.spend("camera checks")
    said = async_mock_service(hass, "assist_satellite", "announce")
    await hass.services.async_call(DOMAIN, "say", {"message": "Pesukone Job's finished."}, blocking=True)
    assert said[0].data["message"] == "Pesukone Job's finished." and not ai


async def test_safety_alert_is_translated_even_at_the_limit(hass: HomeAssistant) -> None:
    ai = await _setup(hass, reply="Savua keittiössä!", daily_ai_limit=1)
    hass.config_entries.async_entries(DOMAIN)[0].runtime_data.ai.spend("camera checks")
    said = async_mock_service(hass, "assist_satellite", "announce")
    await hass.services.async_call(DOMAIN, "say", {"message": "Smoke in the kitchen!", "priority": "urgent"}, blocking=True)
    assert said[0].data["message"] == "Savua keittiössä!" and len(ai) == 1


async def test_a_runaway_reply_is_not_said(hass: HomeAssistant) -> None:
    ai = await _setup(hass, reply="Pesukone " * 200)
    said = async_mock_service(hass, "assist_satellite", "announce")
    await hass.services.async_call(DOMAIN, "say", {"message": "Pesukone Job's finished."}, blocking=True)
    assert len(ai) == 1 and said[0].data["message"] == "Pesukone Job's finished."
