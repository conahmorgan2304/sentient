"""Why the house said something: reasons kept with what it said and given back by sentient_why."""

from homeassistant.core import HomeAssistant
from homeassistant.helpers import llm
from pytest_homeassistant_custom_component.common import MockConfigEntry, async_mock_service

from custom_components.sentient.const import DOMAIN
from custom_components.sentient.reasons import because, from_source, reason_for

OPTIONS = {"persona": "Nova", "speakers": ["assist_satellite.kitchen"], "default_speaker": "assist_satellite.kitchen",
           "quiet_start": "00:00:00", "quiet_end": "00:00:00"}


async def _setup(hass: HomeAssistant) -> MockConfigEntry:
    hass.states.async_set("assist_satellite.kitchen", "idle", {"friendly_name": "Kitchen"})
    entry = MockConfigEntry(domain=DOMAIN, title="Nova", unique_id=DOMAIN, data=OPTIONS)
    entry.add_to_hass(hass)
    assert await hass.config_entries.async_setup(entry.entry_id)
    await hass.async_block_till_done()
    return entry


async def _why(hass: HomeAssistant, what: str) -> dict:
    api = await llm.async_get_api(hass, DOMAIN, llm.LLMContext(platform="t", context=None, language="en",
                                                               assistant=None, device_id=None))
    return await api.async_call_tool(llm.ToolInput(tool_name="sentient_why", tool_args={"what": what}))


def test_reasons_from_source_and_scope() -> None:
    assert from_source("alarm:Bins") == "the 'Bins' alarm was due"
    assert from_source("alarm") is None and from_source("nothing:x") is None and from_source(None) is None
    with because("the heating offer: it read 17"):
        assert reason_for("alarm:Bins") == "the heating offer: it read 17"
        with because(None):
            assert reason_for("welcome") == "someone had just come home"
    assert reason_for(None) is None


async def test_why_did_you_say_that(hass: HomeAssistant) -> None:
    entry = await _setup(hass)
    async_mock_service(hass, "assist_satellite", "announce")
    rt = entry.runtime_data
    with because("I noticed the washing machine changed to finished and thought it worth mentioning"):
        await rt.speech.async_say("The washing's done.", "normal", None, source="notice")
    await hass.async_block_till_done()
    said = rt.timeline.since(1, ["said"], 5)[0]
    assert said["why"].startswith("I noticed the washing machine")
    out = await _why(hass, "why did you say the washing was done?")
    assert out["said_because"][0]["text"] == "The washing's done."
    assert "washing machine" in out["said_because"][0]["why"]
    out = await _why(hass, "why did you say that?")
    assert out["said_because"][0]["text"] == "The washing's done."


async def test_why_it_held_back(hass: HomeAssistant) -> None:
    entry = await _setup(hass)
    rt = entry.runtime_data
    from datetime import timedelta

    from homeassistant.util import dt as dt_util

    rt.speech.hush_until = dt_util.utcnow() + timedelta(minutes=30)
    await rt.speech.async_say("The bins go out tonight.", "normal", None, source="bins")
    await hass.async_block_till_done()
    out = await _why(hass, "why didn't you mention the bins?")
    row = out["said_because"][0]
    assert row["why"] == "the bins go out soon" and row["held_back"]
