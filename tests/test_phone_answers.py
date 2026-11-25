"""Questions answered from the phone when nobody's near a speaker."""

from homeassistant.core import HomeAssistant
from pytest_homeassistant_custom_component.common import MockConfigEntry, async_mock_service

from custom_components.sentient.const import DOMAIN

OPTIONS = {"persona": "Nova", "speakers": ["assist_satellite.kitchen"], "default_speaker": "assist_satellite.kitchen",
           "quiet_start": "00:00:00", "quiet_end": "00:00:00", "phone_apps": ["mobile_app_pixel"],
           "phone_buttons": ["questions"]}


async def _setup(hass: HomeAssistant, **extra) -> MockConfigEntry:
    hass.states.async_set("assist_satellite.kitchen", "idle", {"friendly_name": "Kitchen", "supported_features": 3})
    entry = MockConfigEntry(domain=DOMAIN, title="Nova", unique_id=DOMAIN, data={**OPTIONS, **extra})
    entry.add_to_hass(hass)
    assert await hass.config_entries.async_setup(entry.entry_id)
    await hass.async_block_till_done()
    return entry


async def test_yes_tapped_on_the_phone(hass: HomeAssistant) -> None:
    pushed = async_mock_service(hass, "notify", "mobile_app_pixel")
    entry = await _setup(hass)
    rt = entry.runtime_data
    asked = async_mock_service(hass, "assist_satellite", "start_conversation")
    answers: list[str] = []

    async def on_answer(kind: str) -> str:
        answers.append(kind)
        return "Okay, I'll do that from now on."

    rt.speech.nobody_about = True
    rt.speech.pick_speaker = lambda important=True: ["assist_satellite.kitchen"]  # nobody in any room
    result = await rt.speech.async_ask("Want me to put the lamp on at dusk?", source="learning", on_answer=on_answer)
    assert result.reason == "asked_phone" and not asked
    actions = pushed[0].data["data"]["actions"]
    assert [a["title"] for a in actions] == ["Yes", "No"]
    hass.bus.async_fire("mobile_app_notification_action", {"action": actions[0]["action"]})
    await hass.async_block_till_done()
    assert answers == ["yes"] and pushed[-1].data["message"] == "Okay, I'll do that from now on."
    hass.bus.async_fire("mobile_app_notification_action", {"action": actions[0]["action"]})
    await hass.async_block_till_done()
    assert answers == ["yes"]  # only once
    assert rt.timeline.since(1, ["said"], 1)[0]["detail"] == "asked on the phones"


async def test_off_unless_chosen(hass: HomeAssistant) -> None:
    async_mock_service(hass, "notify", "mobile_app_pixel")
    entry = await _setup(hass, phone_buttons=[])
    assert not entry.runtime_data.notifier.can_ask()


async def test_failed_phone_question_falls_back_to_satellite(hass):
    from unittest.mock import AsyncMock
    from homeassistant.exceptions import HomeAssistantError

    async def fail(call):
        raise HomeAssistantError("phone offline")

    hass.services.async_register("notify", "mobile_app_pixel", fail)
    entry = await _setup(hass)
    rt = entry.runtime_data
    asked = async_mock_service(hass, "assist_satellite", "start_conversation")
    rt.speech.nobody_about = True
    rt.speech.pick_speaker = lambda important=True: ["assist_satellite.kitchen"]
    result = await rt.speech.async_ask("Would you like a reminder?", on_answer=AsyncMock())
    assert result.reason == "asked"
    assert len(asked) == 1
    assert not rt.notifier._questions


async def test_pending_voice_answer_is_invalidated_during_classification(hass):
    from datetime import timedelta
    from unittest.mock import AsyncMock, patch
    from homeassistant.util import dt as dt_util

    entry = await _setup(hass, automatic_language=True)
    speech = entry.runtime_data.speech
    answer = AsyncMock()
    speech._questions["assist_satellite.kitchen"] = {
        "until": dt_util.utcnow() + timedelta(minutes=1),
        "asked": "Would you like a reminder?",
        "on_answer": answer,
    }

    async def rejection(*args):
        speech._privacy_generation += 1
        return True

    with patch("custom_components.sentient.answers.classify", return_value=None), \
            patch("custom_components.sentient.sessions.is_conversation_end", return_value=False), \
            patch("custom_components.sentient.language.async_language_rejection", side_effect=rejection):
        result = await speech.async_take_answer("assist_satellite.kitchen", "some words")
    assert result == "That question is no longer open."
    answer.assert_not_awaited()
