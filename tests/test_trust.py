"""Trust levels: just do it, say then do, or ask first — and offering to stop asking after a run of yeses."""

from datetime import timedelta
from copy import deepcopy

from homeassistant.core import HomeAssistant
from pytest_homeassistant_custom_component.common import MockConfigEntry, async_mock_service

from custom_components.sentient.const import DOMAIN
from custom_components.sentient.learning import Routine

OPTIONS = {"persona": "Nova", "speakers": ["assist_satellite.kitchen"], "default_speaker": "assist_satellite.kitchen",
           "quiet_start": "00:00:00", "quiet_end": "00:00:00"}


async def _setup(hass: HomeAssistant, **extra) -> MockConfigEntry:
    hass.states.async_set("assist_satellite.kitchen", "idle", {"friendly_name": "Kitchen", "supported_features": 3})
    hass.states.async_set("light.lamp", "off", {"friendly_name": "Lamp"})
    hass.states.async_set("cover.garage", "closed", {"friendly_name": "Garage door", "device_class": "garage"})
    entry = MockConfigEntry(domain=DOMAIN, title="Nova", unique_id=DOMAIN, data={**OPTIONS, **extra})
    entry.add_to_hass(hass)
    assert await hass.config_entries.async_setup(entry.entry_id)
    await hass.async_block_till_done()
    lamp = deepcopy(LAMP)
    entry.runtime_data.learner.routines[lamp.id] = lamp
    return entry


LAMP = Routine("light.lamp", "lamp", "on", 1140, "every day", 0.9, 12, 3.0, adopted=True)


async def test_just_do_it_is_the_default(hass: HomeAssistant) -> None:
    entry = await _setup(hass)
    on = async_mock_service(hass, "light", "turn_on")
    said = async_mock_service(hass, "assist_satellite", "announce")
    await entry.runtime_data.learner._async_do(entry.runtime_data.learner.get(LAMP.id))
    assert on and not said


async def test_say_then_do(hass: HomeAssistant) -> None:
    entry = await _setup(hass, trust_lights="tell")
    on = async_mock_service(hass, "light", "turn_on")
    said = async_mock_service(hass, "assist_satellite", "announce")
    await entry.runtime_data.learner._async_do(entry.runtime_data.learner.get(LAMP.id))
    await hass.async_block_till_done()
    assert on and said[0].data["message"] == "Putting the lamp on."


async def test_ask_first_and_offer_to_stop_asking(hass: HomeAssistant, freezer) -> None:
    entry = await _setup(hass, trust_lights="ask")
    rt = entry.runtime_data
    on = async_mock_service(hass, "light", "turn_on")
    asked = async_mock_service(hass, "assist_satellite", "start_conversation")
    for n in range(5):
        freezer.tick(timedelta(days=1))
        await rt.learner._async_do(entry.runtime_data.learner.get(LAMP.id))
        assert asked[-1].data["start_message"] == "Shall I put the lamp on?" and len(on) == n  # not before a yes
        assert await rt.speech.async_take_answer("assist_satellite.kitchen", "yes please") == "Request sent."
        assert len(on) == n + 1
    [finding] = rt.trust.findings()
    added = rt.proposals.refresh([finding])
    assert added[0]["change"].startswith("Stop asking before the lights")
    rt.proposals.answer(added[0]["id"], True)
    await hass.async_block_till_done()
    assert entry.options["trust_lights"] == "tell"


async def test_garage_always_asks(hass: HomeAssistant) -> None:
    entry = await _setup(hass)
    assert entry.runtime_data.trust.level("cover.garage") == "ask"
    assert entry.runtime_data.trust.level("light.lamp") == "do"
