"""sentient.notice: anything an automation passes in goes through the same rules and decision."""

from homeassistant.core import HomeAssistant, ServiceCall, SupportsResponse
from pytest_homeassistant_custom_component.common import MockConfigEntry, async_mock_service

from custom_components.sentient.const import DOMAIN

OPTIONS = {"persona": "Nova", "speakers": ["assist_satellite.kitchen"], "default_speaker": "assist_satellite.kitchen",
           "quiet_start": "00:00:00", "quiet_end": "00:00:00", "chattiness": "normal"}


async def _setup(hass: HomeAssistant, reply: dict) -> list[ServiceCall]:
    hass.states.async_set("assist_satellite.kitchen", "idle", {"friendly_name": "Kitchen"})
    entry = MockConfigEntry(domain=DOMAIN, title="Nova", unique_id=DOMAIN, data=OPTIONS)
    entry.add_to_hass(hass)
    assert await hass.config_entries.async_setup(entry.entry_id)
    await hass.async_block_till_done()
    calls: list[ServiceCall] = []

    async def generate(call: ServiceCall):
        calls.append(call)
        return {"data": reply}

    hass.services.async_register("ai_task", "generate_data", generate, supports_response=SupportsResponse.ONLY)
    return calls


async def test_the_ai_decides_and_words_it(hass: HomeAssistant) -> None:
    ai = await _setup(hass, {"speak": True, "message": "The dishwasher's done."})
    said = async_mock_service(hass, "assist_satellite", "announce")
    out = await hass.services.async_call(DOMAIN, "notice", {"message": "Dishwasher finished its cycle"},
                                         blocking=True, return_response=True)
    assert "Dishwasher finished its cycle" in ai[0].data["instructions"]
    assert said[0].data["message"] == "The dishwasher's done." and out["result"] == "spoken"


async def test_said_as_given_but_still_follows_the_rules(hass: HomeAssistant) -> None:
    ai = await _setup(hass, {"speak": False})
    said = async_mock_service(hass, "assist_satellite", "announce")
    await hass.services.async_call(DOMAIN, "notice", {"message": "The gate's been left open.", "decide": False},
                                   blocking=True, return_response=True)
    assert not ai and said[0].data["message"] == "The gate's been left open."
    await hass.services.async_call(DOMAIN, "hush", {"minutes": 30}, blocking=True)
    out = await hass.services.async_call(DOMAIN, "notice", {"message": "The post has come.", "decide": False},
                                         blocking=True, return_response=True)
    assert out["result"] == "hushed" and len(said) == 1
