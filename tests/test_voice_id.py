"""Sentient Voice Recognition link: options only when it is installed, and the speaker reaches the prompt."""
from types import SimpleNamespace
from unittest.mock import AsyncMock, Mock, patch

from homeassistant.config_entries import ConfigEntryState
from homeassistant.core import HomeAssistant
from homeassistant.helpers import llm
from pytest_homeassistant_custom_component.common import MockConfigEntry

from custom_components.sentient import voice_id
from custom_components.sentient.const import DOMAIN
from custom_components.sentient.speaker_identity import SPEAKER_FOR_TURN, VOICE_RECOGNITION_DOMAIN
from tests.test_books_recipes import _setup
from tests.test_voice_sessions_control import agent, request, response


def _install(hass: HomeAssistant, state=ConfigEntryState.LOADED) -> None:
    e = MockConfigEntry(domain=VOICE_RECOGNITION_DOMAIN)
    e.add_to_hass(hass)
    e.mock_state(hass, state)


def test_lines_name_the_speaker_or_say_it_is_unknown_or_cautious() -> None:
    alex = SimpleNamespace(speaker_id="a", display_name="Alex")
    nobody = SimpleNamespace(speaker_id=None, display_name=None)
    assert voice_id.prompt_lines({}, None) == []
    assert "Alex" in voice_id.prompt_lines({}, alex)[0] and "not proof" in voice_id.prompt_lines({}, alex)[0]
    assert "house rules say not to address people by name" in voice_id.prompt_lines({}, alex)[0]
    assert not voice_id.cautious({"voice_id_unknown": True}, alex)
    assert not voice_id.cautious({}, nobody)
    assert voice_id.cautious({"voice_id_unknown": True}, nobody)
    assert any("guest" in x for x in voice_id.prompt_lines({"voice_id_unknown": True}, nobody))
    assert not any("guest" in x for x in voice_id.prompt_lines({}, nobody))


async def test_lookup_needs_the_setting_and_a_running_integration(hass: HomeAssistant) -> None:
    find = Mock(return_value=SimpleNamespace(speaker_id="a", display_name="Alex"))
    hass.data[SPEAKER_FOR_TURN] = find
    assert voice_id.lookup(hass, {"voice_id": True}, "req") is None  # not installed
    _install(hass, ConfigEntryState.NOT_LOADED)
    assert not voice_id.installed(hass) and voice_id.lookup(hass, {"voice_id": True}, "req") is None
    hass.config_entries.async_entries(VOICE_RECOGNITION_DOMAIN)[0].mock_state(hass, ConfigEntryState.LOADED)
    assert voice_id.lookup(hass, {}, "req") is None  # setting off
    find.assert_not_called()
    assert voice_id.name_of(voice_id.lookup(hass, {"voice_id": True}, "req")) == "Alex"
    assert hass.data[voice_id.LAST_KEY]["name"] == "Alex"
    find.side_effect = RuntimeError("add-on fault")
    assert voice_id.lookup(hass, {"voice_id": True}, "req") is None  # never stops an answer


async def test_conversation_binds_the_speaker_for_the_turn_only(hass: HomeAssistant) -> None:
    _install(hass)
    obj, _ = agent(hass, {"voice_id": True})
    hass.data[SPEAKER_FOR_TURN] = Mock(return_value=SimpleNamespace(speaker_id="a", display_name="Alex"))
    seen = []

    async def turn(_request):
        seen.append(voice_id.name_of(voice_id.current_speaker.get()))
        return response()

    with patch.object(obj, "_recoverable_turn", side_effect=turn):
        await obj.async_process(request("turn on the lamp"))
    assert seen == ["Alex"] and voice_id.current_speaker.get() is None


async def test_prompt_carries_the_speaker_and_unknown_voices_can_be_guests(hass: HomeAssistant, tmp_path) -> None:
    entry, _ = await _setup(hass, tmp_path, voice_id=True, voice_id_unknown=True)
    api = llm.async_get_api  # noqa: F841
    from custom_components.sentient.llm_api import SentientAPI

    ctx = llm.LLMContext(platform="t", context=None, language="en", assistant=None, device_id=None)
    token = voice_id.current_speaker.set(SimpleNamespace(speaker_id="a", display_name="Alex"))
    try:
        prompt = SentientAPI(hass)._prompt(ctx)
    finally:
        voice_id.current_speaker.reset(token)
    assert "probably Alex" in prompt and "This room is private" not in prompt
    token = voice_id.current_speaker.set(SimpleNamespace(speaker_id=None, display_name=None))
    try:
        prompt = SentientAPI(hass)._prompt(ctx)
    finally:
        voice_id.current_speaker.reset(token)
    assert "wasn't matched" in prompt and "Treat this voice as a guest" in prompt and "Things you know about" not in prompt
    assert "probably" not in SentientAPI(hass)._prompt(ctx)  # no result: nothing about a speaker


async def test_settings_tab_offers_download_until_installed(hass: HomeAssistant, tmp_path, hass_ws_client) -> None:
    await _setup(hass, tmp_path)
    ws = await hass_ws_client(hass)
    await ws.send_json({"id": 1, "type": "sentient/settings"})
    tab = next(g for g in (await ws.receive_json())["result"]["groups"] if g["id"] == "voiceid")
    assert tab["schema"] == [] and tab["voice_id"]["installed"] is False
    assert "sentient-voice-recognition" in tab["voice_id"]["repo"] and "hacs_repository" in tab["voice_id"]["hacs"]
    _install(hass)
    await ws.send_json({"id": 2, "type": "sentient/settings"})
    tab = next(g for g in (await ws.receive_json())["result"]["groups"] if g["id"] == "voiceid")
    assert {f["name"] for f in tab["schema"]} == {"voice_id", "voice_id_unknown"} and tab["voice_id"]["installed"]
    await ws.send_json({"id": 3, "type": "sentient/voice_id"})
    assert (await ws.receive_json())["result"]["installed"] is True


async def test_latest_turn_report_says_why_and_reaches_the_page_status(hass: HomeAssistant, tmp_path) -> None:
    await _setup(hass, tmp_path)
    hass.bus.async_fire("sentient_voice_recognition_turn", {"speaker_name": None, "reason": "below_threshold",
                                                            "similarity": 0.52, "runner_up_gap": None})
    await hass.async_block_till_done()
    heard = voice_id.status(hass, {})["heard"]
    assert heard["name"] is None and heard["reason"] == "below_threshold" and heard["similarity"] == 0.52
    assert "below the match level" in heard["why"]
    hass.bus.async_fire("sentient_voice_recognition_turn", {"speaker_name": "Alex", "reason": "matched", "similarity": 0.8})
    await hass.async_block_till_done()
    assert voice_id.status(hass, {})["heard"]["name"] == "Alex"
