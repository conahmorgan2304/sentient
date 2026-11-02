"""Web search (the companion, or Sentient's own through SearXNG) and asking through Alexa — each working when
on and doing nothing when off."""

import asyncio
import time
from unittest.mock import AsyncMock, patch

from homeassistant.components import conversation
from homeassistant.core import Context, HomeAssistant
from homeassistant.helpers import intent, llm
from pytest_homeassistant_custom_component.common import MockConfigEntry, async_mock_service

from custom_components.sentient.const import DOMAIN

OPTIONS = {"persona": "Nova", "speakers": ["assist_satellite.kitchen"], "default_speaker": "assist_satellite.kitchen",
           "quiet_start": "00:00:00", "quiet_end": "00:00:00"}


async def _setup(hass: HomeAssistant, **extra) -> MockConfigEntry:
    hass.states.async_set("assist_satellite.kitchen", "idle", {"supported_features": 3})
    entry = MockConfigEntry(domain=DOMAIN, title="Nova", unique_id=DOMAIN, data={**OPTIONS, **extra})
    entry.add_to_hass(hass)
    assert await hass.config_entries.async_setup(entry.entry_id)
    await hass.async_block_till_done()
    return entry


async def _api(hass: HomeAssistant):
    return await llm.async_get_api(hass, DOMAIN, llm.LLMContext(platform="t", context=None, language="en",
                                                                assistant=None, device_id=None))


async def test_companion_search_tools_are_mentioned(hass: HomeAssistant) -> None:
    MockConfigEntry(domain="voice_satellite_llm_tools", title="Web Search - SearXNG").add_to_hass(hass)
    MockConfigEntry(domain="voice_satellite_llm_tools", title="Video Search - YouTube").add_to_hass(hass)
    await _setup(hass, web_search=True, searxng_url="http://searx:8080")
    api = await _api(hass)
    assert "Search tools are available (Web Search - SearXNG, Video Search - YouTube)" in api.api_prompt
    assert "sentient_search" not in [t.name for t in api.tools]  # never two web searches


async def test_own_search_through_searxng(hass: HomeAssistant, aioclient_mock) -> None:
    await _setup(hass, web_search=True, searxng_url="http://searx:8080/")
    aioclient_mock.get("http://searx:8080/search", json={
        "results": [{"title": "Tip opening times", "content": "Open 8am to 6pm daily", "url": "https://council.example"}],
        "answers": []})
    api = await _api(hass)
    assert "call sentient_search" in api.api_prompt
    out = await api.async_call_tool(llm.ToolInput(tool_name="sentient_search", tool_args={"query": "tip opening times"}))
    assert out["results"][0]["snippet"] == "Open 8am to 6pm daily"
    assert aioclient_mock.mock_calls[-1][1].query["format"] == "json"


async def test_search_off(hass: HomeAssistant) -> None:
    entry = await _setup(hass, searxng_url="http://searx:8080", search_hints=False)
    api = await _api(hass)
    assert "sentient_search" not in [t.name for t in api.tools] and "Search tools" not in api.api_prompt
    items = entry.runtime_data.suggestions.listing(entry.runtime_data.options)
    assert any(i["id"] == "companion|search" and i["url"] for i in items)  # the companion is suggested


def _result(text: str) -> conversation.ConversationResult:
    response = intent.IntentResponse(language="en")
    response.async_set_speech(text)
    return conversation.ConversationResult(response=response, conversation_id="c1")


def _input(text: str) -> conversation.ConversationInput:
    return conversation.ConversationInput(text=text, context=Context(), conversation_id=None, device_id=None,
                                          satellite_id=None, language="en", agent_id="x")


async def _alexa_agent(hass):
    from custom_components.sentient.conversation import AlexaAgent

    entry = hass.config_entries.async_entries(DOMAIN)[0]
    agent = AlexaAgent(entry)
    agent.hass = hass
    agent.entity_id = "conversation.nova_for_alexa"
    agent.async_write_ha_state = lambda: None
    return agent


async def test_alexa_quick_answer_knows_the_echo(hass: HomeAssistant) -> None:
    hass.states.async_set("media_player.kitchen_echo", "idle", {"last_called": True, "last_called_timestamp": time.time() * 1000})
    hass.states.async_set("media_player.bedroom_echo", "idle", {"last_called": False})
    await _setup(hass, alexa_skill=True, voice_agent="conversation.ai")
    agent = await _alexa_agent(hass)
    seen = []

    async def converse(agent_id, user_input):
        seen.append(user_input)
        return _result("Everything's shut.")

    with patch.object(agent, "_converse", side_effect=converse):
        result = await agent.async_process(_input("how is the house. device_id: amzn1.ask.device.ABC123"))
    assert result.response.speech["plain"]["speech"] == "Everything's shut."
    assert seen[0].text == "how is the house"  # the skill's device tag is taken off
    assert "Amazon Echo" in seen[0].extra_system_prompt


async def test_alexa_slow_answer_said_on_that_echo(hass: HomeAssistant) -> None:
    hass.states.async_set("media_player.kitchen_echo", "idle", {"last_called": True, "last_called_timestamp": time.time() * 1000})
    entry = await _setup(hass, alexa_skill=True, alexa_wait=2, voice_agent="conversation.ai")
    agent = await _alexa_agent(hass)
    release = asyncio.Event()

    async def converse(agent_id, user_input):
        await release.wait()
        return _result("The bins go out tomorrow.")

    said = AsyncMock()
    with patch.object(agent, "_converse", side_effect=converse), \
         patch("custom_components.sentient.conversation.DEFAULT_ALEXA_WAIT", 2), \
         patch.object(entry.runtime_data.speech, "async_say", said):
        with patch("asyncio.wait", wraps=asyncio.wait) as waited:
            entry.runtime_data.options = {**entry.runtime_data.options, "alexa_wait": 0.05}
            result = await agent.async_process(_input("when are the bins"))
        assert result.response.speech["plain"]["speech"].startswith("Give me a moment")
        release.set()
        await hass.async_block_till_done()
        await asyncio.sleep(0)
        await hass.async_block_till_done()
    said.assert_awaited_once_with("The bins go out tomorrow.", targets=["media_player.kitchen_echo"], source="alexa:reply")


async def test_alexa_off(hass: HomeAssistant) -> None:
    await _setup(hass, voice_agent="conversation.ai")
    agent = await _alexa_agent(hass)
    with patch.object(agent, "_converse", AsyncMock(return_value=_result("x"))) as converse:
        result = await agent.async_process(_input("how is the house"))
    assert "switched off" in result.response.speech["plain"]["speech"] and not converse.called


async def test_replies_through_alexa_are_never_held_for_quiet_hours(hass: HomeAssistant) -> None:
    entry = await _setup(hass)
    speech = entry.runtime_data.speech
    with patch.object(speech, "quiet_now", return_value=True):
        assert speech._gate("The bins go out tomorrow.", "normal", "alexa:reply") is None
        assert speech._gate("The bins go out tomorrow.", "normal", "notice") is not None
