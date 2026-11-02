"""Never touch these: nothing Sentient does on its own changes a protected device; and the AI-limited note."""

from datetime import datetime

from homeassistant.core import HomeAssistant
from homeassistant.helpers import llm
from homeassistant.util import dt as dt_util
from pytest_homeassistant_custom_component.common import MockConfigEntry, async_mock_service

from custom_components.sentient.const import DOMAIN
from custom_components.sentient.never import allowed, blocked, touches

OPTIONS = {"persona": "Nova", "speakers": [], "quiet_start": "00:00:00", "quiet_end": "00:00:00"}


async def _setup(hass: HomeAssistant, **extra) -> MockConfigEntry:
    entry = MockConfigEntry(domain=DOMAIN, title="Nova", unique_id=DOMAIN, data={**OPTIONS, **extra})
    entry.add_to_hass(hass)
    assert await hass.config_entries.async_setup(entry.entry_id)
    await hass.async_block_till_done()
    return entry


async def _api(hass):
    return await llm.async_get_api(hass, DOMAIN, llm.LLMContext(platform="t", context=None, language="en",
                                                               assistant=None, device_id=None))


async def test_the_list_is_respected(hass: HomeAssistant) -> None:
    hass.states.async_set("light.projector", "off", {"friendly_name": "Girls projector"})
    await _setup(hass, never_entities=["light.projector"])
    assert blocked(hass, "light.projector") and not blocked(hass, "light.lamp")
    assert allowed(hass, ["light.lamp", "light.projector"]) == ["light.lamp"]
    assert touches(hass, [{"action": "light.turn_on", "target": {"entity_id": ["light.lamp", "light.projector"]}}]) == ["light.projector"]
    assert "Never switch, dim or change these" in (await _api(hass)).api_prompt and "Girls projector" in (await _api(hass)).api_prompt


async def test_rules_and_routines_that_would_touch_one_do_not_run(hass: HomeAssistant) -> None:
    hass.states.async_set("light.projector", "off", {"friendly_name": "Girls projector"})
    entry = await _setup(hass, never_entities=["light.projector"])
    on = async_mock_service(hass, "light", "turn_on")
    from custom_components.sentient.shortcuts import async_run_actions

    await async_run_actions(hass, [{"action": "light.turn_on", "target": {"entity_id": "light.projector"}}], "test")
    await hass.async_block_till_done()
    assert not on
    await async_run_actions(hass, [{"action": "light.turn_on", "target": {"entity_id": "light.lamp"}}], "test")
    await hass.async_block_till_done()
    assert len(on) == 1  # anything else still works
    assert entry.runtime_data is not None


async def test_motion_lights_leave_a_protected_light_alone(hass: HomeAssistant, freezer) -> None:
    freezer.move_to(dt_util.as_utc(datetime(2026, 10, 22, 19, 0, tzinfo=dt_util.get_default_time_zone())))
    hass.states.async_set("light.projector", "off", {"friendly_name": "Girls projector"})
    hass.states.async_set("binary_sensor.girls_motion", "off")
    hass.states.async_set("sun.sun", "below_horizon")
    entry = await _setup(hass, never_entities=["light.projector"], quiet_start="23:00:00", quiet_end="06:00:00")
    on = async_mock_service(hass, "light", "turn_on")
    entry.runtime_data.motion_lights.upsert({"light": "light.projector", "motion": ["binary_sensor.girls_motion"]})
    hass.states.async_set("binary_sensor.girls_motion", "on")
    await hass.async_block_till_done()
    assert not on


async def test_nothing_protected_when_the_list_is_empty(hass: HomeAssistant) -> None:
    await _setup(hass)
    assert not blocked(hass, "light.projector")
    assert "Never switch" not in (await _api(hass)).api_prompt


async def test_voice_told_when_the_ai_is_limited(hass: HomeAssistant) -> None:
    entry = await _setup(hass)
    assert "background AI" not in (await _api(hass)).api_prompt
    gate = entry.runtime_data.ai
    for _ in range(5):
        gate.failed(RuntimeError("503 high demand"))
    assert gate.failing
    assert "not answering right now" in (await _api(hass)).api_prompt
