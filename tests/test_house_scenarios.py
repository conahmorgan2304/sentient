"""Scenarios from a real house with every 1.16 feature on: the parts working together, and the prompt kept lean."""

from unittest.mock import AsyncMock, patch

from homeassistant.components.homeassistant.exposed_entities import async_expose_entity
from homeassistant.core import Context, HomeAssistant
from homeassistant.helpers import area_registry as ar, device_registry as dr, entity_registry as er, llm
from homeassistant.setup import async_setup_component
from pytest_homeassistant_custom_component.common import MockConfigEntry

from custom_components.sentient import voice_id
from custom_components.sentient.const import DOMAIN

ALL_ON = {"persona": "Nova", "speakers": [], "quiet_start": "00:00:00", "quiet_end": "00:00:00",
          "moment_awareness": True, "room_memory": True, "know_people": True, "instant_answers": True,
          "capability_awareness": True, "memory_upkeep": True, "prove_routines": True,
          "current_house_context": True, "situations": True, "room_tracking": True,
          "room_trackers": ["sensor.sams_phone_area"]}


async def _house(hass: HomeAssistant):
    """A small version of a real house: two rooms, a satellite in the kitchen, lights, doors, heating, a phone."""
    assert await async_setup_component(hass, "homeassistant", {})
    areas = ar.async_get(hass)
    kitchen, lounge = areas.async_get_or_create("Kitchen"), areas.async_get_or_create("Living Room")
    ents = er.async_get(hass)
    for eid, area, state, attrs in (
            ("light.kitchen_light", kitchen, "on", {"friendly_name": "Kitchen light"}),
            ("light.living_room_lamp", lounge, "off", {"friendly_name": "Living room lamp"}),
            ("binary_sensor.back_door", kitchen, "off", {"friendly_name": "Back door", "device_class": "door"}),
            ("climate.heating", None, "heat", {"friendly_name": "Heating", "current_temperature": 17.5, "temperature": 18}),
            ("sensor.kitchen_temperature", kitchen, "19.2", {"device_class": "temperature", "unit_of_measurement": "°C"})):
        domain, obj = eid.split(".")
        ents.async_get_or_create(domain, "test", obj, suggested_object_id=obj)
        if area:
            ents.async_update_entity(eid, area_id=area.id)
        hass.states.async_set(eid, state, attrs)
        async_expose_entity(hass, "conversation", eid, True)
    hass.states.async_set("person.sam", "home", {"friendly_name": "Sam"})
    hass.states.async_set("sensor.sams_phone_area", "Kitchen", {"friendly_name": "Sam's phone Area"})
    other = MockConfigEntry(domain="esphome")
    other.add_to_hass(hass)
    sat = dr.async_get(hass).async_get_or_create(config_entry_id=other.entry_id, identifiers={("esphome", "kitchen")})
    dr.async_get(hass).async_update_device(sat.id, area_id=kitchen.id)
    entry = MockConfigEntry(domain=DOMAIN, title="Nova", unique_id=DOMAIN, data=ALL_ON)
    entry.add_to_hass(hass)
    assert await hass.config_entries.async_setup(entry.entry_id)
    await hass.async_block_till_done()
    return entry, sat


async def _prompt(hass, device_id=None, user_id=None) -> str:
    ctx = llm.LLMContext(platform="t", context=Context(user_id=user_id), language="en", assistant=None,
                         device_id=device_id)
    return (await llm.async_get_api(hass, DOMAIN, ctx)).api_prompt


async def test_everything_on_in_the_kitchen(hass: HomeAssistant) -> None:
    entry, sat = await _house(hass)
    prompt = await _prompt(hass, sat.id)
    assert "Who's about" in prompt and "Sam home since" in prompt
    assert "Probably Sam talking (their phone is the only tracked phone in this room)" in prompt
    assert "What you can control in this house by voice: 2 lights, 1 heating or cooling control." in prompt
    assert "This house has no smart locks" in prompt
    # nothing said twice: one "movement" line, no recent-changes block alongside the moment
    assert prompt.count("Movement in the last") <= 1 and "Movement lately in" not in prompt
    assert prompt.count("just got home") <= 1


async def test_the_prompt_stays_lean_with_everything_on(hass: HomeAssistant) -> None:
    """A guard against bloat: every turn's instructions with all seven features on stay under a budget."""
    entry, sat = await _house(hass)
    for n in range(30):
        entry.runtime_data.memory.add(f"Household fact {n}: the bins go out on a Thursday evening at the latest.")
    prompt = await _prompt(hass, sat.id)
    assert len(prompt) < 7000, len(prompt)  # 5,919 on 22 Nov (7,256 before the context was made lines)


async def test_an_instant_answer_is_remembered_in_the_room(hass: HomeAssistant) -> None:
    """Instant answers and room memory together: asked in the kitchen, known in the kitchen afterwards."""
    from tests.test_voice_sessions_control import agent, request, response

    entry, sat = await _house(hass)
    obj, _ = agent(hass, dict(entry.runtime_data.options))
    obj._entry = entry
    with patch.object(obj, "_async_process_legacy", AsyncMock(return_value=response("from the AI"))) as ai:
        ask = request("Is the back door open?", cid=None)
        ask.device_id = sat.id
        result = await obj.async_process(ask)
    assert result.response.speech["plain"]["speech"] == "No, the Back door is closed."
    ai.assert_not_awaited()
    await hass.async_block_till_done()
    later = await _prompt(hass, sat.id)
    assert "Earlier in the Kitchen" in later and "Is the back door open?" in later


async def test_an_unmatched_voice_is_never_guessed_from_a_phone(hass: HomeAssistant) -> None:
    """Voice recognition heard someone it didn't know: a phone nearby mustn't put a name to them."""
    entry, sat = await _house(hass)
    token = voice_id.current_speaker.set(voice_id.UNKNOWN_SPEAKER)
    try:
        assert "Probably Sam" not in await _prompt(hass, sat.id)
    finally:
        voice_id.current_speaker.reset(token)


async def test_the_prompt_can_be_looked_at(hass: HomeAssistant, hass_ws_client) -> None:
    entry, sat = await _house(hass)
    ws = await hass_ws_client(hass)
    await ws.send_json({"id": 1, "type": "sentient/voice/prompt", "device_id": sat.id})
    out = (await ws.receive_json())["result"]
    assert out["chars"] == len(out["prompt"]) and out["tools"] > 5 and out["biggest"]
