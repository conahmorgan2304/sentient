"""Offline, deterministic multi-step household scenarios. Never dispatch services."""
import pytest

from homeassistant.core import HomeAssistant

from custom_components.sentient.plans import plan, async_verify


@pytest.mark.parametrize(
    ("states", "steps", "expected", "unavailable"),
    [
        (
            {"light.hall": "on", "light.kitchen": "on"},
            [{"action": "light.turn_off", "target": {"entity_id": ["light.hall", "light.kitchen"]}}],
            {"light.hall": "off", "light.kitchen": "off"}, [],
        ),
        (
            {"light.hall": "on", "light.kitchen": "off"},
            [{"action": "light.turn_off", "target": {"entity_id": ["light.hall", "light.kitchen"]}}],
            {"light.hall": "off", "light.kitchen": "off"}, [],
        ),
        (
            {"light.hall": "on", "lock.front": "unavailable"},
            [{"action": "light.turn_off", "target": {"entity_id": "light.hall"}},
             {"action": "lock.lock", "target": {"entity_id": "lock.front"}}],
            {"light.hall": "off", "lock.front": "locked"}, ["lock.front"],
        ),
        (
            {"light.hall": "on"},
            [{"action": "light.turn_off", "target": {"entity_id": "light.hall"}},
             {"action": "script.turn_on", "target": {"entity_id": "script.unknown"}}],
            {"light.hall": "off"}, [],
        ),
    ],
)
def test_offline_planner_expected_states(hass: HomeAssistant, states, steps, expected, unavailable):
    for entity_id, state in states.items():
        hass.states.async_set(entity_id, state)
    before = {entity_id: hass.states.get(entity_id).state for entity_id in states}
    result = plan(hass, steps)
    assert {step["entity_id"]: step["expect"] for step in result["steps"]} == expected
    assert result["unavailable"] == unavailable
    # Planning must never change a device.
    assert {entity_id: hass.states.get(entity_id).state for entity_id in states} == before


async def test_offline_plan_verification_distinguishes_success_and_missing(hass: HomeAssistant):
    hass.states.async_set("light.hall", "on")
    hass.states.async_set("light.kitchen", "on")
    proposed = plan(hass, [{"action": "light.turn_off",
                            "target": {"entity_id": ["light.hall", "light.kitchen"]}}])
    # Simulate only the hallway succeeding; no service invocation.
    hass.states.async_set("light.hall", "off")
    result = await async_verify(hass, proposed, seconds=0)
    assert result["done"] == ["light.hall"]
    assert [row["entity_id"] for row in result["not_done"]] == ["light.kitchen"]


async def test_offline_conditional_plan_never_claims_execution(hass: HomeAssistant):
    hass.states.async_set("light.hall", "on")
    actions = [{"condition": "state", "entity_id": "input_boolean.ready", "state": "on"},
               {"action": "light.turn_off", "target": {"entity_id": "light.hall"}}]
    proposed = plan(hass, actions)
    hass.states.async_set("light.hall", "off")
    result = await async_verify(hass, proposed, seconds=0)
    assert result["done"] == []
    assert result["unverified"] == ["light.hall"]
