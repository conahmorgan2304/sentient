"""Fixes from testing against a live house: AI busy retries, readable held-back reasons, plain-words health."""

from unittest.mock import patch

from homeassistant.core import HomeAssistant, ServiceResponse, SupportsResponse
from homeassistant.exceptions import HomeAssistantError
from pytest_homeassistant_custom_component.common import MockConfigEntry

from custom_components.sentient.ai import AIUnavailable, busy
from custom_components.sentient.const import DOMAIN
from custom_components.sentient.timeline import held_reason


async def _setup(hass: HomeAssistant) -> MockConfigEntry:
    entry = MockConfigEntry(domain=DOMAIN, title="Nova", unique_id=DOMAIN, data={"persona": "Nova", "speakers": []})
    entry.add_to_hass(hass)
    assert await hass.config_entries.async_setup(entry.entry_id)
    await hass.async_block_till_done()
    return entry


def _ai_task(hass, outcomes):
    calls = []

    async def generate(call) -> ServiceResponse:
        calls.append(call)
        outcome = outcomes[min(len(calls), len(outcomes)) - 1]
        if isinstance(outcome, Exception):
            raise outcome
        return outcome

    hass.services.async_register("ai_task", "generate_data", generate, supports_response=SupportsResponse.ONLY)
    return calls


async def test_busy_ai_is_tried_once_more(hass: HomeAssistant) -> None:
    entry = await _setup(hass)
    calls = _ai_task(hass, [HomeAssistantError("503 This model is currently experiencing high demand"), {"data": "ok"}])
    with patch("custom_components.sentient.ai.RETRY_AFTER", 0):
        out = await entry.runtime_data.ai.async_call({"task_name": "t", "instructions": "x"})
    assert out == {"data": "ok"} and len(calls) == 2 and entry.runtime_data.ai.fails_in_row == 0


async def test_other_ai_errors_are_not_retried(hass: HomeAssistant) -> None:
    entry = await _setup(hass)
    calls = _ai_task(hass, [HomeAssistantError("400 invalid argument")])
    try:
        await entry.runtime_data.ai.async_call({"task_name": "t", "instructions": "x"})
        raise AssertionError("should have failed")
    except AIUnavailable:
        pass
    assert len(calls) == 1 and not busy(HomeAssistantError("400 invalid argument"))


async def test_held_reasons_in_words(hass: HomeAssistant) -> None:
    hass.states.async_set("automation.front_door", "on", {"friendly_name": "Front door person"})
    assert held_reason(hass, "hushed_by:announcement") == "right after another announcement"
    assert held_reason(hass, "hushed_by:automation.front_door") == "right after Front door person"
    assert held_reason(hass, "quiet_hours") == "quiet hours"
    assert held_reason(hass, "said_recently") == "said the same not long ago"


async def test_health_sensor_has_plain_sentences(hass: HomeAssistant) -> None:
    entry = await _setup(hass)
    health = entry.runtime_data.health
    health.issues = [{"kind": "offline", "name": "Shed light", "since": "2026-11-02T17:00:00+00:00", "device_id": "x", "with": 0}]
    st = None
    for e in hass.states.async_all("sensor"):
        if e.attributes.get("issues") is not None:
            st = e
    assert st is not None
    health.hass.bus.async_fire("sentient_health_changed", {"count": 1, "problems": {}, "updates": []})
    await hass.async_block_till_done()
    st = hass.states.get(st.entity_id)
    assert st.attributes["problems"] == ["Shed light is offline"]
