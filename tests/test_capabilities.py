"""Capability awareness: what this house can and can't do by voice."""

from homeassistant.components.homeassistant.exposed_entities import async_expose_entity
from homeassistant.core import HomeAssistant
from homeassistant.helpers import llm
from homeassistant.setup import async_setup_component
from pytest_homeassistant_custom_component.common import MockConfigEntry

from custom_components.sentient.const import DOMAIN

CTX = llm.LLMContext(platform="t", context=None, language="en", assistant=None, device_id=None)


async def test_what_the_house_can_and_cannot_do(hass: HomeAssistant) -> None:
    assert await async_setup_component(hass, "homeassistant", {})
    for e in ("light.a", "light.b", "climate.heating"):
        hass.states.async_set(e, "off")
        async_expose_entity(hass, "conversation", e, True)
    hass.states.async_set("lock.shed", "locked")
    async_expose_entity(hass, "conversation", "lock.shed", False)  # not for voice: as good as not there
    entry = MockConfigEntry(domain=DOMAIN, title="Nova", unique_id=DOMAIN, data={
        "persona": "Nova", "speakers": [], "capability_awareness": True})
    entry.add_to_hass(hass)
    assert await hass.config_entries.async_setup(entry.entry_id)
    await hass.async_block_till_done()
    prompt = (await llm.async_get_api(hass, DOMAIN, CTX)).api_prompt
    assert "What you can control in this house by voice: 2 lights, 1 heating or cooling control." in prompt
    assert ("This house has no smart locks, motorised blinds, curtains or garage doors, smart water valves, robot "
            "mower or smart water heater you can control. The ordinary ones are still there") in prompt
    hass.config_entries.async_update_entry(entry, options={**entry.data, "capability_awareness": False})
    await hass.async_block_till_done()
    assert "What you can control" not in (await llm.async_get_api(hass, DOMAIN, CTX)).api_prompt


async def test_an_offer_no_device_can_do_is_taken_out(hass: HomeAssistant) -> None:
    """22 Nov live: "...the heating is at sixteen degrees. Shall I close the bedroom window or leave it?\""""
    from custom_components.sentient.capabilities import impossible_offer
    from custom_components.sentient.security_offers import StreamFilter, remove

    assert await async_setup_component(hass, "homeassistant", {})
    hass.states.async_set("cover.garage_door", "closed", {"friendly_name": "Garage door"})
    async_expose_entity(hass, "conversation", "cover.garage_door", True)
    pattern = impossible_offer(hass, {"capability_awareness": True})
    said = "The bedroom window is open and the heating is at sixteen degrees. Shall I close the bedroom window or leave it?"
    kept, gone = remove(said, security=False, extra=pattern)
    assert kept == "The bedroom window is open and the heating is at sixteen degrees." and gone
    assert remove("Shall I close the garage door?", security=False, extra=pattern)[1] == []  # a door it can move
    assert remove("Would you like me to lock the back door? It's shut.", security=False, extra=pattern)[0] == "It's shut."
    stream = StreamFilter(security=False, extra=pattern)
    out = stream.feed("The window is open. Shall I close it, the window I mean? ") + stream.flush()
    assert out.strip() == "The window is open."
    assert impossible_offer(hass, {"capability_awareness": False}) is None


async def test_the_reply_is_spoken_without_the_impossible_offer(hass: HomeAssistant) -> None:
    from unittest.mock import AsyncMock, patch

    from tests.test_voice_sessions_control import agent, request, response

    assert await async_setup_component(hass, "homeassistant", {})
    obj, rt = agent(hass, {"capability_awareness": True})
    said = "The bedroom window is open. Shall I close the bedroom window or leave it?"
    with patch.object(obj, "_async_process_impl", AsyncMock(return_value=response(said))):
        result = await obj._process_streaming_aware(request("Anything happening in the house?"))
    assert result.response.speech["plain"]["speech"] == "The bedroom window is open."
    assert result.continue_conversation is False  # the question was the offer: nothing to wait for
