"""Voice entry points and reported-state verification for household tasks."""
from types import SimpleNamespace
from unittest.mock import AsyncMock, patch

import pytest
from homeassistant.core import callback

from custom_components.sentient.household_tasks import HouseholdTasks
from tests.test_voice_sessions_control import agent, request


def spoken(result):
    assert not result.continue_conversation
    return result.response.speech["plain"]["speech"]


@pytest.mark.parametrize("sessions", [False, True])
async def test_bedtime_rechecks_pending_without_replaying_commands(hass, sessions):
    obj, rt = agent(hass, {
        "conversation_sessions": sessions, "verified_bedtime": True,
        "verified_bedtime_lights": ["light.hall", "light.offline", "light.delayed"],
        "verified_bedtime_contacts": ["binary_sensor.gate"],
    })
    rt.household_tasks = HouseholdTasks(hass, rt.control)
    hass.states.async_set("light.hall", "on", {"friendly_name": "Hall"})
    hass.states.async_set("light.offline", "unavailable", {"friendly_name": "Landing"})
    hass.states.async_set("light.delayed", "on", {"friendly_name": "Porch"})
    hass.states.async_set("binary_sensor.gate", "on", {"device_class": "door", "friendly_name": "Gate"})
    calls = []

    @callback
    def turn_off(call):
        eid = call.data["entity_id"]
        calls.append(eid)
        if eid == "light.hall":
            hass.states.async_set(eid, "off")

    hass.services.async_register("light", "turn_off", turn_off)
    with patch("custom_components.sentient.household_tasks.async_should_expose", return_value=True), \
            patch("custom_components.sentient.control.async_should_expose", return_value=True), \
            patch.object(obj, "_async_process_legacy", AsyncMock()) as model:
        pending = spoken(await obj.async_process(request("Run downstairs bedtime.")))
        assert "pending" in pending and "Gate reports open" in pending
        assert "Landing has no current report" in pending and "Porch hasn't reported off" in pending
        assert set(calls) == {"light.hall", "light.delayed"}

        assert "no recent" in spoken(await obj.async_process(request("Downstairs bedtime status", device="shed")))
        hass.states.async_set("binary_sensor.gate", "off", {"device_class": "door"})
        hass.states.async_set("light.offline", "off")
        hass.states.async_set("light.delayed", "off")
        assert "complete" in spoken(await obj.async_process(request("Is downstairs bedtime done?")))
        assert "stopped tracking" in spoken(await obj.async_process(request("Cancel downstairs bedtime")))
        assert "no recent" in spoken(await obj.async_process(request("Downstairs bedtime status")))
        assert len(calls) == 2
        model.assert_not_awaited()


async def test_bedtime_respects_guest_exposure_never_touch_and_no_groups(hass):
    obj, rt = agent(hass, {
        "verified_bedtime": True,
        "verified_bedtime_lights": ["light.hidden", "light.protected", "light.group"],
        "verified_bedtime_contacts": ["binary_sensor.gate"],
    })
    rt.household_tasks = HouseholdTasks(hass, rt.control)
    rt.speech = SimpleNamespace(guest_mode=False)
    hass.states.async_set("light.hidden", "on", {"friendly_name": "Secret light"})
    hass.states.async_set("light.protected", "on")
    hass.states.async_set("light.group", "on", {"entity_id": ("light.protected",)})
    hass.states.async_set("binary_sensor.gate", "off", {"device_class": "door"})
    call = AsyncMock()
    hass.services.async_register("light", "turn_off", call)
    with patch("custom_components.sentient.household_tasks.async_should_expose", side_effect=lambda h, a, eid: eid != "light.hidden"), \
            patch("custom_components.sentient.control.async_should_expose", return_value=True), \
            patch("custom_components.sentient.never.blocked", return_value=True):
        pending = spoken(await obj.async_process(request("Run downstairs bedtime")))
        assert "Secret light" not in pending and "outside your Assist access" in pending
        assert "different selection" in pending and "pending" in pending
        rt.speech.guest_mode = True
        assert "unavailable" in spoken(await obj.async_process(request("Downstairs bedtime status")))
    call.assert_not_awaited()


async def test_bedtime_continues_when_one_light_service_raises(hass):
    obj, rt = agent(hass, {
        "verified_bedtime": True,
        "verified_bedtime_lights": ["light.hall", "light.porch"],
        "verified_bedtime_contacts": ["binary_sensor.gate"],
    })
    rt.household_tasks = HouseholdTasks(hass, rt.control)
    hass.states.async_set("light.hall", "on", {"friendly_name": "Hall"})
    hass.states.async_set("light.porch", "on", {"friendly_name": "Porch"})
    hass.states.async_set("binary_sensor.gate", "off", {"device_class": "door"})

    @callback
    def turn_off(call):
        if call.data["entity_id"] == "light.hall":
            raise RuntimeError("simulated integration failure")
        hass.states.async_set("light.porch", "off")

    hass.services.async_register("light", "turn_off", turn_off)
    with patch("custom_components.sentient.household_tasks.async_should_expose", return_value=True), \
            patch("custom_components.sentient.control.async_should_expose", return_value=True):
        pending = spoken(await obj.async_process(request("Run downstairs bedtime")))
        assert "pending" in pending
        assert "couldn't verify Hall switched off" in pending
        assert hass.states.get("light.porch").state == "off"
