"""Language checks for Sentient's announcements and critical alerts."""

import sys
from types import SimpleNamespace

import pytest
from homeassistant.core import HomeAssistant
from pytest_homeassistant_custom_component.common import async_mock_service

from custom_components.sentient import phrases
from custom_components.sentient.const import DOMAIN
from custom_components.sentient.voice_locale import LABELS, safety_sentence
from tests.test_language import _setup


@pytest.mark.parametrize("priority", ["normal", "urgent"])
async def test_short_english_message_is_translated_even_when_urgent(hass: HomeAssistant, priority) -> None:
    ai = await _setup(hass, strict_speech_language=True, reply="Pyykit ovat valmiit.")
    spoken = async_mock_service(hass, "assist_satellite", "announce")
    result = await hass.services.async_call(
        DOMAIN, "say", {"message": "Laundry cycle complete.", "priority": priority},
        blocking=True, return_response=True,
    )
    assert result["spoken"]
    assert [call.data["message"] for call in spoken] == ["Pyykit ovat valmiit."]
    assert [call.data["task_name"] for call in ai] == ["Sentient: speech language"]


@pytest.mark.parametrize("reply", [None, "Laundry cycle complete.", "Pyykit ovat valmiit. Please check the washer."])
async def test_unverified_wording_is_held_without_speaking(hass: HomeAssistant, reply) -> None:
    message = "Laundry cycle complete."
    ai = await _setup(hass, strict_speech_language=True, reply=reply)
    spoken = async_mock_service(hass, "assist_satellite", "announce")
    result = await hass.services.async_call(
        DOMAIN, "say", {"message": message}, blocking=True, return_response=True,
    )
    await hass.async_block_till_done()
    assert not result["spoken"]
    assert result["reason"] == "language_unavailable"
    assert len(ai) == 1 and not spoken
    runtime = hass.config_entries.async_entries(DOMAIN)[0].runtime_data
    assert any(item["kind"] == "held" and item["text"] == message for item in runtime.timeline.items)


async def test_native_wording_is_spoken_without_requesting_translation(hass: HomeAssistant) -> None:
    ai = await _setup(hass, strict_speech_language=True, reply=None)
    spoken = async_mock_service(hass, "assist_satellite", "announce")
    await hass.services.async_call(DOMAIN, "say", {"message": "Joku on ovella."}, blocking=True)
    assert [call.data["message"] for call in spoken] == ["Joku on ovella."]
    assert not ai


async def test_pronunciation_changes_cannot_replace_verified_wording_with_english(hass: HomeAssistant) -> None:
    await _setup(hass, strict_speech_language=True, reply=None)
    runtime = hass.config_entries.async_entries(DOMAIN)[0].runtime_data
    runtime.speech.pronounce = lambda _: "Someone is at the door."
    spoken = async_mock_service(hass, "assist_satellite", "announce")
    await hass.services.async_call(DOMAIN, "say", {"message": "Joku on ovella."}, blocking=True)
    assert [call.data["message"] for call in spoken] == ["Joku on ovella."]


async def test_unverified_question_does_not_open_the_microphone(hass: HomeAssistant) -> None:
    ai = await _setup(hass, strict_speech_language=True, reply=None)
    hass.states.async_set("assist_satellite.kitchen", "idle", {"supported_features": 3})
    asked = async_mock_service(hass, "assist_satellite", "start_conversation")
    spoken = async_mock_service(hass, "assist_satellite", "announce")
    result = await hass.services.async_call(
        DOMAIN, "ask", {"question": "Should I close that door?"}, blocking=True, return_response=True,
    )
    assert result["reason"] == "language_unavailable"
    assert len(ai) == 1 and not asked and not spoken


@pytest.mark.parametrize(("house_language", "alert"), [
    ("fi-FI", "Savua havaittu!"),
    ("de", "Rauch erkannt!"),
])
async def test_critical_alert_has_local_wording_and_keeps_device_details(hass: HomeAssistant, house_language, alert) -> None:
    ai = await _setup(hass, house_language=house_language, strict_speech_language=True, reply=None,
                      notify_targets=["notify.phone"])
    spoken = async_mock_service(hass, "assist_satellite", "announce")
    notified = async_mock_service(hass, "notify", "send_message")
    hass.states.async_set("notify.phone", "unknown")
    sensor = "binary_sensor.garage_smoke"
    attributes = {"device_class": "smoke", "friendly_name": "Garage smoke sensor"}
    hass.states.async_set(sensor, "off", attributes)
    await hass.async_block_till_done()
    hass.states.async_set(sensor, "on", attributes)
    await hass.async_block_till_done()

    assert [call.data["message"] for call in spoken] == [alert]
    assert not ai
    assert notified and "Garage smoke sensor" in notified[0].data["message"]
    runtime = hass.config_entries.async_entries(DOMAIN)[0].runtime_data
    assert any(item["kind"] == "critical" and "Garage smoke sensor" in item["text"] for item in runtime.timeline.items)


async def test_extra_critical_sensor_and_test_alert_use_offline_finnish(hass: HomeAssistant) -> None:
    sensor = "input_boolean.panic"
    ai = await _setup(hass, strict_speech_language=True, critical_extra=[sensor], reply=None)
    spoken = async_mock_service(hass, "assist_satellite", "announce")
    runtime = hass.config_entries.async_entries(DOMAIN)[0].runtime_data
    await runtime.critical.async_test()
    hass.states.async_set(sensor, "on", {"friendly_name": "Panic button"})
    await hass.async_block_till_done()

    assert [call.data["message"] for call in spoken] == [
        "Tämä on turvallisuusvaroituksen testi. Mikään ei ole vialla.",
        "Hälytys!",
    ]
    assert not ai


def test_bundled_languages_have_offline_alerts_for_every_critical_type() -> None:
    for path in phrases.DIR.glob("*.json"):
        if path.stem == "en":
            continue
        phrases.load(path.stem)
        for label in LABELS:
            assert safety_sentence(f"{label}: Garage smoke sensor!", path.stem, include_location=False)


@pytest.mark.parametrize("supports_finnish", [True, False])
async def test_direct_tts_uses_the_selected_language(hass: HomeAssistant, monkeypatch, supports_finnish) -> None:
    await _setup(hass, voice_language="fi", strict_speech_language=True, tts_entity="tts.test")
    hass.states.async_set("media_player.speaker", "idle")
    engine = SimpleNamespace(default_language="en-US", supported_languages=["en-US", "fi-FI"] if supports_finnish else ["en-US"])
    helper = SimpleNamespace(get_engine_instance=lambda *_: engine)
    monkeypatch.setitem(sys.modules, "homeassistant.components.tts.helper", helper)
    spoken = async_mock_service(hass, "tts", "speak")
    result = await hass.services.async_call(
        DOMAIN, "say", {"message": "Joku on ovella.", "speaker": "media_player.speaker"},
        blocking=True, return_response=True,
    )
    assert result["spoken"] == supports_finnish
    assert [call.data["language"] for call in spoken] == (["fi-FI"] if supports_finnish else [])
