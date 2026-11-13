"""Phase 4: a voice routine is planned, checked, carried out, verified, and says what didn't happen."""
from homeassistant.core import HomeAssistant, ServiceCall

from custom_components.sentient.plans import async_verify, describe, plan
from custom_components.sentient.shortcuts import Shortcuts

GOODNIGHT = [
    {"action": "light.turn_off", "target": {"entity_id": ["light.landing", "light.hall"]}},
    {"action": "lock.lock", "target": {"entity_id": "lock.front"}},
    {"action": "script.turn_on", "target": {"entity_id": "script.bedtime_story"}},
    {"delay": {"seconds": 1}},
]


def test_a_routine_is_read_into_what_each_device_should_end_up_as(hass: HomeAssistant) -> None:
    hass.states.async_set("light.landing", "on")
    hass.states.async_set("lock.front", "unavailable")
    p = plan(hass, GOODNIGHT)
    assert [(s["entity_id"], s["expect"]) for s in p["steps"]] == [
        ("light.landing", "off"), ("light.hall", "off"), ("lock.front", "locked")]
    assert p["unchecked"] == 2  # the script and the delay: carried out, not checked
    assert p["unavailable"] == ["light.hall", "lock.front"]


async def test_what_didnt_happen_is_said(hass: HomeAssistant) -> None:
    hass.states.async_set("light.landing", "on", {"friendly_name": "Landing light"})
    hass.states.async_set("light.hall", "off", {"friendly_name": "Hall light"})
    hass.states.async_set("lock.front", "unavailable", {"friendly_name": "Front door"})
    out = await async_verify(hass, plan(hass, GOODNIGHT), seconds=0.1)
    assert out["done"] == [] and out["already_satisfied"] == ["light.hall"]
    assert describe(hass, out) == "Done, except the landing light is still on and the front door didn't respond."
    hass.states.async_set("light.landing", "off", {"friendly_name": "Landing light"})
    hass.states.async_set("lock.front", "locked", {"friendly_name": "Front door"})
    assert describe(hass, await async_verify(hass, plan(hass, GOODNIGHT), seconds=0.1)) == ""


async def test_a_routine_run_checks_itself_and_tells_motion_lights_to_wait(hass: HomeAssistant) -> None:
    from custom_components.sentient.action_intents import ActionIntentMonitor

    hass.states.async_set("light.landing", "on", {"friendly_name": "Landing light"})
    hass.states.async_set("light.hall", "on", {"friendly_name": "Hall light"})

    async def off(call: ServiceCall) -> None:
        for entity_id in call.data["entity_id"]:
            if entity_id != "light.landing":  # the landing bulb doesn't respond
                hass.states.async_set(entity_id, "off", {"friendly_name": "Hall light"})

    hass.services.async_register("light", "turn_off", off)
    routines = Shortcuts(hass, "t")
    await routines.async_load()
    routines.verify = lambda: True
    routines.intents = ActionIntentMonitor()
    routines.upsert({"name": "goodnight", "actions": GOODNIGHT[:1], "say": "Goodnight."})
    import custom_components.sentient.plans as plans

    plans.VERIFY_SECONDS = 0.2
    result = await routines.async_run("goodnight", requested_by_person=True)
    assert result["said"] == "Goodnight. Done, except the landing light is still on."
    assert result["checked"]["done"] == ["light.hall"] and result["decision"]["kind"] == "routine_plan"
    # a person asked for it: motion lights opted in to wait won't put the hall light straight back on
    assert routines.intents.preview("light.hall", "turn_on", "motion_lights")["decision"] == "recommend_hold"
    routines.verify = lambda: False
    hass.states.async_set("light.landing", "on", {"friendly_name": "Landing light"})
    assert (await routines.async_run("goodnight"))["said"] == "Goodnight."  # switched off: unchecked, as before

async def test_automated_routines_do_not_gain_manual_action_priority(hass: HomeAssistant) -> None:
    from custom_components.sentient.action_intents import ActionIntentMonitor
    hass.states.async_set("light.hall", "on")
    async def off(call: ServiceCall) -> None:
        hass.states.async_set("light.hall", "off")
    hass.services.async_register("light", "turn_off", off)
    routines = Shortcuts(hass, "automated")
    await routines.async_load()
    routines.verify = lambda: True
    routines.intents = ActionIntentMonitor()
    routines.upsert({"name": "scheduled", "actions": [{"action": "light.turn_off",
                     "target": {"entity_id": "light.hall"}}]})
    await routines.async_run("scheduled")
    assert routines.intents.preview("light.hall", "turn_on", "motion_lights")["decision"] != "recommend_hold"


async def test_already_satisfied_does_not_gain_manual_priority(hass: HomeAssistant) -> None:
    from custom_components.sentient.action_intents import ActionIntentMonitor
    hass.states.async_set("light.hall", "off")
    async def off(call: ServiceCall) -> None:
        return None
    hass.services.async_register("light", "turn_off", off)
    routines = Shortcuts(hass, "already")
    await routines.async_load()
    routines.verify = lambda: True
    routines.intents = ActionIntentMonitor()
    routines.upsert({"name": "off", "actions": [{"action": "light.turn_off",
                     "target": {"entity_id": "light.hall"}}]})
    result = await routines.async_run("off", requested_by_person=True)
    assert result["checked"]["already_satisfied"] == ["light.hall"]
    assert routines.intents.preview("light.hall", "turn_on", "motion_lights")["decision"] != "recommend_hold"


async def test_a_conditional_routine_cannot_claim_skipped_steps_executed(hass: HomeAssistant) -> None:
    """A matching state after a condition does not prove the target action ran."""
    hass.states.async_set("light.landing", "on")
    actions = [{"condition": "state", "entity_id": "input_boolean.allowed", "state": "on"},
               {"action": "light.turn_off", "target": {"entity_id": "light.landing"}}]
    planned = plan(hass, actions)
    assert planned["conditional"]
    # Some unrelated change could make the light off even though the condition skipped it.
    hass.states.async_set("light.landing", "off")
    result = await async_verify(hass, planned, seconds=0.1)
    assert "light.landing" not in result["done"]
    assert result["unverified"] == ["light.landing"]
    assert result["unchecked"] >= 1
