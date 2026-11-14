"""Focused voice tools and asking later how something went — both optional, both off by default."""

from datetime import timedelta

from homeassistant.core import HomeAssistant
from homeassistant.helpers import llm
from pytest_homeassistant_custom_component.common import MockConfigEntry

from custom_components.sentient.const import DOMAIN
from custom_components.sentient.focus import Focus, current_request

OPTIONS = {"persona": "Nova", "speakers": [], "quiet_start": "00:00:00", "quiet_end": "00:00:00"}


async def _setup(hass: HomeAssistant, **extra) -> MockConfigEntry:
    entry = MockConfigEntry(domain=DOMAIN, title="Nova", unique_id=DOMAIN, data={**OPTIONS, **extra})
    entry.add_to_hass(hass)
    assert await hass.config_entries.async_setup(entry.entry_id)
    await hass.async_block_till_done()
    return entry


async def _api(hass, asked: str | None = None):
    token = current_request.set(asked)
    try:
        return await llm.async_get_api(hass, DOMAIN, llm.LLMContext(platform="t", context=None, language="en",
                                                                    assistant=None, device_id=None))
    finally:
        current_request.reset(token)


def test_topics_and_follow_ups() -> None:
    focus = Focus()
    tools = focus.tools_for("is anyone at the gate?", "c1")
    assert "sentient_look" in tools and "sentient_remember" in tools and "sentient_party" not in tools
    assert "sentient_look" in focus.tools_for("yes please", "c1")  # a short answer keeps the last topic's tools
    assert focus.tools_for("what do you reckon to pineapple on pizza", "c2") is None  # no clear match: everything


async def test_focused_tools_only_when_on(hass: HomeAssistant) -> None:
    entry = await _setup(hass)
    every = {t.name for t in (await _api(hass, "is anyone at the gate?")).tools}
    assert "sentient_party" in every
    hass.config_entries.async_update_entry(entry, options={**OPTIONS, "focused_tools": True})
    await hass.async_block_till_done()
    focused = {t.name for t in (await _api(hass, "is anyone at the gate?")).tools}
    assert "sentient_look" in focused and "sentient_party" not in focused and len(focused) < len(every)
    assert {t.name for t in (await _api(hass, None)).tools} == every  # not through the voice agent: everything


async def test_asks_once_when_due_and_never_mid_command(hass: HomeAssistant, freezer) -> None:
    entry = await _setup(hass, ask_later=True)
    api = await _api(hass, "we're taking the cat to the vet tomorrow")
    assert "sentient_ask_later" in {t.name for t in api.tools}
    out = await api.async_call_tool(llm.ToolInput(tool_name="sentient_ask_later", tool_args={
        "question": "How did the vet go with the cat?", "about": "the cat's vet visit", "hours": 20}))
    assert out["saved"] == "the cat's vet visit"
    assert "How did the vet go" not in (await _api(hass, "hello")).api_prompt  # not due yet
    freezer.tick(timedelta(hours=21))
    assert "How did the vet go" not in (await _api(hass, "turn the kitchen light off")).api_prompt  # a plain command
    assert "How did the vet go" in (await _api(hass, "how's the weather looking")).api_prompt
    assert "How did the vet go" not in (await _api(hass, "how's the weather looking")).api_prompt  # only once
    assert any(i["kind"] == "mind" and "vet" in i["text"] for i in entry.runtime_data.timeline.items)


async def test_ask_later_off_and_cleared(hass: HomeAssistant) -> None:
    entry = await _setup(hass, ask_later=True)
    entry.runtime_data.threads.add("How was the trip?", "the trip", 1)
    hass.config_entries.async_update_entry(entry, options={**OPTIONS, "ask_later": False})
    await hass.async_block_till_done()
    assert not entry.runtime_data.threads.items
    api = await _api(hass, "hello")
    assert "sentient_ask_later" not in {t.name for t in api.tools} and "sentient_ask_later" not in api.api_prompt


def test_plain_command_gets_only_the_essentials() -> None:
    tools = Focus().tools_for("turn the kitchen light off")
    assert tools is not None and "sentient_remember" in tools and "sentient_look" not in tools


async def test_the_voice_knows_it_cant_do_physical_things(hass: HomeAssistant) -> None:
    """14 Nov 13:09: "Shall I pop out and feed them now?" about the rabbits."""
    await _setup(hass)
    prompt = (await _api(hass, "have the rabbits been fed?")).api_prompt
    assert "You have no body" in prompt and "Never offer to do anything physical yourself" in prompt
