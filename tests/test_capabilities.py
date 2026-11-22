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
            "mower or smart water heater you can control (the ordinary ones are still there") in prompt
    hass.config_entries.async_update_entry(entry, options={**entry.data, "capability_awareness": False})
    await hass.async_block_till_done()
    assert "What you can control" not in (await llm.async_get_api(hass, DOMAIN, CTX)).api_prompt
