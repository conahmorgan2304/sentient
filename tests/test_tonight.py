"""'Make sure everything's ready for tonight': report, switch off only permitted lights, follow what's outstanding."""

from homeassistant.components.homeassistant.exposed_entities import async_expose_entity
from homeassistant.core import Context
from homeassistant.setup import async_setup_component

from tests.test_heating_programmes import _setup

BACK = "binary_sensor.back_door"
FRONT = "lock.front_door"
HALL = "light.hallway"
LAMP = "light.lounge_lamp"


async def _house(hass, **opts):
    assert await async_setup_component(hass, "homeassistant", {})
    hass.states.async_set(BACK, "on", {"device_class": "door", "friendly_name": "Back door"})
    hass.states.async_set(FRONT, "locked", {"friendly_name": "Front door"})
    hass.states.async_set(HALL, "on", {"friendly_name": "Hallway"})
    hass.states.async_set(LAMP, "on", {"friendly_name": "Lounge lamp"})
    entry = await _setup(hass, ready_tonight=True, ready_tonight_lights=[HALL], **opts)
    for e in (BACK, FRONT, HALL, LAMP):
        async_expose_entity(hass, "conversation", e, True)
    rt = entry.runtime_data
    said = []

    async def say(text, priority="normal", targets=None, source=None, **_kw):
        said.append((text, targets))
    rt.speech.async_say = say

    async def light_off(call):
        hass.states.async_set(call.data["entity_id"], "off", {"friendly_name": "Hallway"})
    hass.services.async_register("light", "turn_off", light_off)
    return rt, said


async def test_ready_for_tonight_reports_and_only_switches_permitted_lights(hass):
    rt, _ = await _house(hass)
    out = await rt.tonight.async_check("kitchen", Context(), "assist_satellite.kitchen")
    assert hass.states.get(HALL).state == "off" and hass.states.get(LAMP).state == "on"
    assert out["switched_off"] == ["Hallway"] and out["left_on"] == ["Lounge lamp"]
    assert out["open"] == ["Back door"] and not out["unlocked"]
    assert "Back door is still open" in out["summary"] and "alarm" not in out["summary"].casefold()
    assert out["following"] == ["Back door"]


async def test_says_once_when_the_outstanding_door_shuts(hass):
    rt, said = await _house(hass)
    await rt.tonight.async_check("kitchen", Context(), "assist_satellite.kitchen")
    hass.states.async_set(BACK, "off", {"device_class": "door", "friendly_name": "Back door"})
    await hass.async_block_till_done(wait_background_tasks=True)
    hass.states.async_set(BACK, "on", {"device_class": "door", "friendly_name": "Back door"})
    hass.states.async_set(BACK, "off", {"device_class": "door", "friendly_name": "Back door"})
    await hass.async_block_till_done(wait_background_tasks=True)
    assert said == [("That's everything ready for tonight now: Back door's shut.", ["assist_satellite.kitchen"])]
    assert not rt.tonight.runs


async def test_status_changes_nothing_and_unknown_is_reported(hass):
    rt, said = await _house(hass)
    hass.states.async_set(FRONT, "unavailable", {"friendly_name": "Front door"})
    out = await rt.tonight.async_check("kitchen", Context(), None, act=False)
    assert hass.states.get(HALL).state == "on" and not out["switched_off"]
    assert "Front door" in out["no_current_report"] and "can't confirm" in out["summary"]
    assert not rt.tonight.runs


async def test_never_touch_light_is_not_switched_off(hass):
    rt, _ = await _house(hass, never_entities=[HALL])
    out = await rt.tonight.async_check("kitchen", Context(), None)
    assert hass.states.get(HALL).state == "on" and out["could_not_confirm_off"] == ["Hallway"]


async def test_cancel_and_switched_off(hass):
    rt, said = await _house(hass)
    await rt.tonight.async_check("kitchen", Context(), "assist_satellite.kitchen")
    assert rt.tonight.cancel("kitchen") and not rt.tonight.cancel("kitchen")
    await rt.tonight.async_check("kitchen", Context(), "assist_satellite.kitchen")
    rt.tonight.options = {**rt.tonight.options, "ready_tonight": False}
    hass.states.async_set(BACK, "off", {"device_class": "door", "friendly_name": "Back door"})
    await hass.async_block_till_done(wait_background_tasks=True)
    assert not said and not rt.tonight.runs


async def test_tool_only_offered_when_switched_on(hass):
    from homeassistant.helpers import llm

    rt, _ = await _house(hass)
    api = await llm.async_get_api(hass, "sentient", llm.LLMContext(
        platform="test", context=Context(), language="en", assistant="conversation", device_id=None))
    assert "sentient_ready_tonight" in {t.name for t in api.tools}


async def test_no_spoken_promise_in_quiet_hours(hass):
    rt, _ = await _house(hass)
    rt.speech.quiet_now = lambda: True
    out = await rt.tonight.async_check("kitchen", Context(), "assist_satellite.kitchen")
    assert out["summary"].endswith("I'll note when that's sorted.")
