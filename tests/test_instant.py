"""Instant answers: everyday questions straight from the house, before any AI."""

from types import SimpleNamespace
from unittest.mock import AsyncMock, patch

from homeassistant.components.homeassistant.exposed_entities import async_expose_entity
from homeassistant.core import HomeAssistant
from homeassistant.helpers import area_registry as ar, entity_registry as er
from homeassistant.setup import async_setup_component

from custom_components.sentient.instant import async_answer
from tests.test_voice_sessions_control import agent, request, response


async def _house(hass: HomeAssistant):
    assert await async_setup_component(hass, "homeassistant", {})
    hass.states.async_set("light.kitchen_light", "on", {"friendly_name": "Kitchen light"})
    hass.states.async_set("light.kitchen_lamp", "off", {"friendly_name": "Kitchen lamp"})
    hass.states.async_set("binary_sensor.back_door", "off", {"friendly_name": "Back door", "device_class": "door"})
    hass.states.async_set("light.secret", "on", {"friendly_name": "Secret light"})
    hass.states.async_set("weather.home", "partlycloudy", {"temperature": 6.4, "temperature_unit": "°C"})
    for e in ("light.kitchen_light", "light.kitchen_lamp", "binary_sensor.back_door"):
        async_expose_entity(hass, "conversation", e, True)
    async_expose_entity(hass, "conversation", "light.secret", False)
    bedroom = ar.async_get(hass).async_get_or_create("Bedroom")
    er.async_get(hass).async_get_or_create("sensor", "test", "bed_t", suggested_object_id="bedroom_temp")
    er.async_get(hass).async_update_entity("sensor.bedroom_temp", area_id=bedroom.id)
    hass.states.async_set("sensor.bedroom_temp", "18.5", {"device_class": "temperature", "unit_of_measurement": "°C"})
    async_expose_entity(hass, "conversation", "sensor.bedroom_temp", True)
    return SimpleNamespace(options={"instant_answers": True}, speech=SimpleNamespace(guest_mode=False))


async def test_everyday_questions(hass: HomeAssistant) -> None:
    rt = await _house(hass)
    ask = lambda t: async_answer(hass, rt, t, None)  # noqa: E731
    assert await ask("Is the kitchen light on?") == "Yes, the Kitchen light is on."
    assert await ask("is the kitchen lamp on") == "No, the Kitchen lamp is off."
    assert await ask("Is the back door open?") == "No, the Back door is closed."
    assert await ask("What's the temperature outside?") == "It's 6°C outside and partly cloudy."
    assert await ask("How warm is it in the bedroom?") == "It's 18.5°C in the Bedroom."
    assert (await ask("What time is it?")).startswith("It's ")


async def test_anything_doubtful_goes_to_the_ai(hass: HomeAssistant) -> None:
    rt = await _house(hass)
    ask = lambda t: async_answer(hass, rt, t, None)  # noqa: E731
    assert await ask("Is the secret light on?") is None  # not exposed to voice
    assert await ask("Is the kitchen on?") is None  # not one device
    assert await ask("Turn the kitchen light on") is None  # a command, never answered here
    assert await ask("Is the kitchen light on and the lamp off?") is None
    assert await ask("Is it on?") is None
    rt.options["instant_answers"] = False
    assert await ask("Is the kitchen light on?") is None


async def test_the_agent_answers_without_the_ai_and_keeps_the_conversation(hass: HomeAssistant) -> None:
    rt_house = await _house(hass)
    obj, rt = agent(hass, {**rt_house.options})
    with patch.object(obj, "_async_process_legacy", AsyncMock(return_value=response("from the AI"))) as ai:
        result = await obj.async_process(request("Is the kitchen light on?", cid=None))
        assert result.response.speech["plain"]["speech"] == "Yes, the Kitchen light is on."
        ai.assert_not_awaited()
        from homeassistant.components.conversation.chat_log import DATA_CHAT_LOGS

        log = hass.data[DATA_CHAT_LOGS][result.conversation_id]
        assert [c.role for c in log.content][-2:] == ["user", "assistant"]
        assert log.content[-1].content == "Yes, the Kitchen light is on."
        await obj.async_process(request("Turn it off", cid=result.conversation_id))
        ai.assert_awaited_once()
