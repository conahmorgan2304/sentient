"""Know who's where (one best guess per person at home) and plans for big requests."""

from collections import deque
from types import SimpleNamespace
from unittest.mock import Mock, patch

from homeassistant.components.homeassistant.exposed_entities import async_expose_entity
from homeassistant.core import HomeAssistant, ServiceCall
from homeassistant.helpers import area_registry as ar
from homeassistant.helpers import llm
from homeassistant.setup import async_setup_component
from homeassistant.util import dt as dt_util
from pytest_homeassistant_custom_component.common import MockConfigEntry

from custom_components.sentient.const import DOMAIN
from custom_components.sentient.voice_id import RECENT_KEY
from custom_components.sentient.whereabouts import prompt_line, who_where

BASE = {"persona": "Nova", "quiet_start": "00:00:00", "quiet_end": "00:00:00"}


def _rt(options, *, episodes=(), best=None):
    return SimpleNamespace(options=options, conversations=SimpleNamespace(recent=Mock(return_value=list(episodes))),
                           tracker=SimpleNamespace(best_area=Mock(return_value=best)))


async def test_each_source_with_how_sure(hass: HomeAssistant) -> None:
    areas = ar.async_get(hass)
    kitchen = areas.async_create("Kitchen")
    areas.async_create("Living room")
    hass.states.async_set("person.conah", "home", {"friendly_name": "Conah"})
    hass.states.async_set("person.kayleigh", "home", {"friendly_name": "Kayleigh"})
    hass.states.async_set("person.ryan", "not_home", {"friendly_name": "Ryan"})
    hass.states.async_set("person.bella", "home", {"friendly_name": "Bella"})
    hass.states.async_set("sensor.conah_phone_area", "Kitchen", {"friendly_name": "Conah's phone Area", "area_id": kitchen.id})
    now = dt_util.utcnow()
    hass.data[RECENT_KEY] = deque([{"name": "Kayleigh", "at": now.isoformat()}])
    episodes = [{"room": "Living room", "start": now.isoformat(), "last": now.isoformat()}]
    options = {"who_where": True, "room_tracking": True, "room_trackers": ["sensor.conah_phone_area"]}
    people = {p["name"]: p for p in who_where(hass, _rt(options, episodes=episodes))}
    assert set(people) == {"Conah", "Kayleigh", "Bella"}  # nobody who's out
    assert people["Kayleigh"] == {"name": "Kayleigh", "room": "Living room", "sure": "sure", "why": "heard talking there just now"}
    assert people["Conah"]["room"] == "Kitchen" and people["Conah"]["sure"] == "probably"
    assert people["Bella"]["room"] is None
    line = prompt_line(hass, _rt(options, episodes=episodes))
    assert "Bella: can't tell where" in line and "Ryan" not in line and "never say anyone is out" in line
    assert who_where(hass, _rt({**options, "who_where": False})) == []


async def test_movement_only_counts_when_one_person_is_home(hass: HomeAssistant) -> None:
    lounge = ar.async_get(hass).async_create("Lounge")
    hass.states.async_set("person.conah", "home", {"friendly_name": "Conah"})
    # the only person set up: the rest of the household could be the ones moving
    assert who_where(hass, _rt({"who_where": True}, best=lounge.id))[0]["room"] is None
    hass.states.async_set("person.kayleigh", "not_home", {"friendly_name": "Kayleigh"})
    people = who_where(hass, _rt({"who_where": True}, best=lounge.id))
    assert people == [{"name": "Conah", "room": "Lounge", "sure": "a guess",
                       "why": "the only one home, and there's been movement there"}]
    hass.states.async_set("person.kayleigh", "home", {"friendly_name": "Kayleigh"})
    assert all(p["room"] is None for p in who_where(hass, _rt({"who_where": True}, best=lounge.id)))


async def _plan_setup(hass: HomeAssistant, on: bool = True):
    assert await async_setup_component(hass, "homeassistant", {})
    entry = MockConfigEntry(domain=DOMAIN, title="Nova", unique_id=DOMAIN, data={**BASE, "big_plans": on})
    entry.add_to_hass(hass)
    assert await hass.config_entries.async_setup(entry.entry_id)
    await hass.async_block_till_done()
    hass.states.async_set("light.lamp", "off", {"friendly_name": "Living room lamp"})
    hass.states.async_set("light.kitchen", "on", {"friendly_name": "Kitchen light"})
    for eid in ("light.lamp", "light.kitchen"):
        async_expose_entity(hass, "conversation", eid, True)
    calls: list[ServiceCall] = []

    async def switch(call: ServiceCall) -> None:
        calls.append(call)
        if call.service == "turn_on":
            for eid in call.data["entity_id"]:
                hass.states.async_set(eid, "on", {"friendly_name": "Living room lamp"})
        # the kitchen light ignores turn_off: a device that didn't respond

    hass.services.async_register("light", "turn_on", switch)
    hass.services.async_register("light", "turn_off", switch)
    ctx = llm.LLMContext(platform="test", context=None, language="en", assistant="conversation", device_id=None)
    return entry, await llm.async_get_api(hass, "sentient", ctx), calls


STEPS = [{"action": "light.turn_on", "target": "living room lamp", "brightness_pct": 30},
         {"action": "light.turn_off", "target": "kitchen light"}]


async def test_a_plan_is_read_back_then_done_and_checked(hass: HomeAssistant) -> None:
    entry, api, calls = await _plan_setup(hass)
    out = await api.async_call_tool(llm.ToolInput(tool_name="sentient_plan", tool_args={"goal": "film night", "steps": STEPS}))
    assert out["read_back"] == ("For film night, I'll turn on the living room lamp at 30% and turn off the kitchen "
                                "light. Shall I go ahead?")
    assert not calls
    with patch("custom_components.sentient.plans.VERIFY_SECONDS", 0.2):
        out = await api.async_call_tool(llm.ToolInput(tool_name="sentient_plan", tool_args={
            "goal": "film night", "steps": STEPS, "confirmed": True}))
    assert [c.service for c in calls] == ["turn_on", "turn_off"]
    assert calls[0].data["brightness_pct"] == 30
    assert out["said"] == "Done, except the kitchen light is still on."
    assert out["not_done"] == ["light.kitchen"]


async def test_a_reworded_goal_on_the_yes_still_counts(hass: HomeAssistant) -> None:
    from custom_components.sentient.readback import readbacks

    entry, api, calls = await _plan_setup(hass)
    session = SimpleNamespace(id="c1", turn=1)
    with patch("custom_components.sentient.readback.current", return_value=session):
        await api.async_call_tool(llm.ToolInput(tool_name="sentient_plan", tool_args={"goal": "film night", "steps": STEPS}))
        session.turn = 2
        with patch("custom_components.sentient.plans.VERIFY_SECONDS", 0.2):
            out = await api.async_call_tool(llm.ToolInput(tool_name="sentient_plan", tool_args={
                "goal": "getting ready for film night", "steps": STEPS, "confirmed": True}))
    assert "said" in out and len(calls) == 2
    assert readbacks(entry.runtime_data) is not None


async def test_plans_refuse_what_the_voice_cannot_control(hass: HomeAssistant) -> None:
    _, api, calls = await _plan_setup(hass)
    hass.states.async_set("lock.front", "locked", {"friendly_name": "Front door lock"})
    async_expose_entity(hass, "conversation", "lock.front", True)
    out = await api.async_call_tool(llm.ToolInput(tool_name="sentient_plan", tool_args={
        "goal": "going away", "steps": [{"action": "lock.lock", "target": "front door lock"}]}))
    assert "can't use lock.lock" in out["error"]
    hass.states.async_set("light.shed", "on", {"friendly_name": "Shed light"})
    async_expose_entity(hass, "conversation", "light.shed", False)
    out = await api.async_call_tool(llm.ToolInput(tool_name="sentient_plan", tool_args={
        "goal": "going away", "steps": [{"action": "light.turn_off", "target": "shed light"}]}))
    assert "allowed to control" in out["error"] and not calls


async def test_no_plan_tool_when_off(hass: HomeAssistant) -> None:
    _, api, _ = await _plan_setup(hass, on=False)
    assert "sentient_plan" not in {t.name for t in api.tools}
