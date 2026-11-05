"""Plain answers to the house's own questions are read without the AI."""

from unittest.mock import AsyncMock

import pytest
from homeassistant.core import HomeAssistant
from pytest_homeassistant_custom_component.common import MockConfigEntry, async_mock_service

from custom_components.sentient.answers import classify
from custom_components.sentient.const import DOMAIN


@pytest.mark.parametrize(("said", "kind"), [
    ("Yes please", "yes"), ("yeah go on", "yes"), ("sure, why not", "yes"), ("Okay.", "yes"),
    ("No thanks", "no"), ("not now", "no"), ("nah I'm fine", "no"), ("leave it", "no"),
    ("It already does that for me", "done"), ("that's already set up", "done"), ("no, it already does it", "done"),
    ("yes but not the kitchen", None), ("Yeah no", None), ("what time is it", None), ("I don't know", None),
])
def test_plain_answers(said, kind) -> None:
    assert classify(said) == kind


async def test_already_done_is_not_taken_as_yes(hass: HomeAssistant) -> None:
    hass.states.async_set("assist_satellite.kitchen", "idle", {"supported_features": 3})
    entry = MockConfigEntry(domain=DOMAIN, title="Nova", unique_id=DOMAIN, data={
        "persona": "Nova", "speakers": ["assist_satellite.kitchen"], "default_speaker": "assist_satellite.kitchen",
        "quiet_start": "00:00:00", "quiet_end": "00:00:00"})
    entry.add_to_hass(hass)
    assert await hass.config_entries.async_setup(entry.entry_id)
    await hass.async_block_till_done()
    async_mock_service(hass, "assist_satellite", "start_conversation")
    speech = entry.runtime_data.speech
    handler = AsyncMock(return_value="Ah, right — I'll leave that one alone then.")
    await speech.async_ask("Want me to switch the blanket off at 11?", targets=["assist_satellite.kitchen"], on_answer=handler)
    assert await speech.async_take_answer("assist_satellite.kitchen", "what's the weather?") is None  # not an answer
    said = await speech.async_take_answer("assist_satellite.kitchen", "It already does that")
    handler.assert_awaited_once_with("done")
    assert said.startswith("Ah, right")
    assert await speech.async_take_answer("assist_satellite.kitchen", "yes") is None  # answered: nothing open now
