"""Natural speaking mode and carrying on a conversation across wake words — both optional."""

from datetime import timedelta
from unittest.mock import patch

from homeassistant.components import conversation
from homeassistant.core import Context, HomeAssistant
from homeassistant.helpers import intent, llm
from pytest_homeassistant_custom_component.common import MockConfigEntry

from custom_components.sentient.const import DOMAIN

OPTIONS = {"persona": "Nova", "speakers": [], "quiet_start": "00:00:00", "quiet_end": "00:00:00",
           "voice_agent": "conversation.ai"}


async def _setup(hass: HomeAssistant, **extra) -> MockConfigEntry:
    entry = MockConfigEntry(domain=DOMAIN, title="Nova", unique_id=DOMAIN, data={**OPTIONS, **extra})
    entry.add_to_hass(hass)
    assert await hass.config_entries.async_setup(entry.entry_id)
    await hass.async_block_till_done()
    return entry


async def _prompt(hass):
    api = await llm.async_get_api(hass, DOMAIN, llm.LLMContext(platform="t", context=None, language="en",
                                                               assistant=None, device_id=None))
    return api.api_prompt


async def test_natural_style_only_when_chosen(hass: HomeAssistant) -> None:
    entry = await _setup(hass)
    assert "How to talk" not in await _prompt(hass)
    hass.config_entries.async_update_entry(entry, options={**OPTIONS, "conversation_style": "natural"})
    await hass.async_block_till_done()
    prompt = await _prompt(hass)
    assert "How to talk" in prompt and "follow-up question when a person naturally would" in prompt


def _input(text: str, cid: str) -> conversation.ConversationInput:
    return conversation.ConversationInput(text=text, context=Context(), conversation_id=cid, device_id="dev1",
                                          satellite_id="assist_satellite.kitchen", language="en", agent_id="x")


async def _agent(hass):
    from custom_components.sentient.conversation import BackedUpAgent

    agent = BackedUpAgent(hass.config_entries.async_entries(DOMAIN)[0])
    agent.hass = hass
    agent.entity_id = "conversation.nova_with_backup"
    agent.async_write_ha_state = lambda: None
    return agent


async def _talk(agent, turns):
    seen = []

    async def converse(agent_id, user_input):
        seen.append(user_input.conversation_id)
        response = intent.IntentResponse(language="en")
        response.async_set_speech("Sure.")
        return conversation.ConversationResult(response=response, conversation_id=user_input.conversation_id)

    with patch.object(agent, "_converse", side_effect=converse):
        for text, cid in turns:
            await agent.async_process(_input(text, cid))
    return seen


async def test_carries_on_from_the_same_speaker(hass: HomeAssistant, freezer) -> None:
    await _setup(hass, carry_on_minutes=3)
    agent = await _agent(hass)
    seen = await _talk(agent, [("how warm is the lounge", "first")])
    freezer.tick(timedelta(minutes=2))
    seen += await _talk(agent, [("and upstairs?", "fresh-id")])
    assert seen == ["first", "first"]
    freezer.tick(timedelta(minutes=4))
    seen += await _talk(agent, [("what's the weather", "later-id")])
    assert seen[-1] == "later-id"  # too long ago: a new conversation


async def test_that_is_all_ends_it_and_0_turns_it_off(hass: HomeAssistant) -> None:
    entry = await _setup(hass, carry_on_minutes=3)
    agent = await _agent(hass)
    seen = await _talk(agent, [("turn the lamp on", "one"), ("that's all", "one"), ("hello", "two")])
    assert seen[-1] == "two"
    hass.config_entries.async_update_entry(entry, options={**OPTIONS, "carry_on_minutes": 0})
    await hass.async_block_till_done()
    agent = await _agent(hass)
    assert await _talk(agent, [("hi", "a"), ("and again", "b")]) == ["a", "b"]
