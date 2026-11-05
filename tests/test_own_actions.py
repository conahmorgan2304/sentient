"""The house never mistakes its own switching for a person's."""

from datetime import timedelta

from homeassistant.core import Context, HomeAssistant
from pytest_homeassistant_custom_component.common import MockConfigEntry, async_fire_time_changed, async_mock_service

from custom_components.sentient.const import DOMAIN
from custom_components.sentient.own import is_own, own_actions, own_context
from custom_components.sentient.shortcuts import async_run_actions


async def _setup(hass: HomeAssistant, **extra) -> MockConfigEntry:
    hass.states.async_set("assist_satellite.kitchen", "idle", {"supported_features": 3})
    entry = MockConfigEntry(domain=DOMAIN, title="Nova", unique_id=DOMAIN, data={
        "persona": "Nova", "speakers": ["assist_satellite.kitchen"], "default_speaker": "assist_satellite.kitchen",
        "quiet_start": "00:00:00", "quiet_end": "00:00:00", **extra})
    entry.add_to_hass(hass)
    assert await hass.config_entries.async_setup(entry.entry_id)
    await hass.async_block_till_done()
    return entry


async def test_own_actions_are_recorded_and_recognised(hass: HomeAssistant, freezer) -> None:
    entry = await _setup(hass)
    calls = async_mock_service(hass, "switch", "turn_off")
    # an automated routine switches the blanket off: the context is the house's own
    await async_run_actions(hass, [{"action": "switch.turn_off", "target": {"entity_id": "switch.blanket"}}], "alarm: Blanket off")
    ctx = calls[-1].context
    assert is_own(hass, ctx) and not is_own(hass, Context())
    hass.states.async_set("switch.blanket", "off", context=ctx)
    await hass.async_block_till_done()
    own = own_actions(hass)
    assert "switch.blanket" in own.times() and own.last_reason("switch.blanket") == "alarm: Blanket off"
    # learning ignores it
    assert "switch.blanket" in entry.runtime_data.learner.extra_automated()
    # "why did the blanket go off?" names the house
    from custom_components.sentient.explain import async_explain

    out = await async_explain(hass, entry.runtime_data, "switch.blanket")
    assert out["by"].startswith("the house itself (alarm: Blanket off")


async def test_follow_through_ignores_the_houses_own_switching(hass: HomeAssistant, freezer) -> None:
    await _setup(hass)
    said = async_mock_service(hass, "assist_satellite", "announce")
    async_mock_service(hass, "light", "turn_on")
    hass.states.async_set("light.lamp", "unavailable", {"friendly_name": "Lamp"})
    hass.states.async_set("assist_satellite.kitchen", "listening")  # someone was just talking
    hass.states.async_set("assist_satellite.kitchen", "idle")
    await hass.services.async_call("light", "turn_on", {"entity_id": "light.lamp"}, blocking=True,
                                   context=own_context(hass, "motion lights"))
    freezer.tick(timedelta(seconds=200))
    async_fire_time_changed(hass)
    await hass.async_block_till_done()
    assert not said
