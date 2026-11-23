"""Grade its own answers: plain statements about devices checked against what the house recorded."""

from datetime import timedelta
from types import SimpleNamespace
from unittest.mock import Mock, patch

from homeassistant.components.homeassistant.exposed_entities import async_expose_entity
from homeassistant.core import HomeAssistant
from homeassistant.setup import async_setup_component
from homeassistant.util import dt as dt_util
import pytest

from custom_components.sentient.grading import async_grade, claims
from custom_components.sentient.memory import MemoryStore
from custom_components.sentient.reliability import Reliability


async def _house(hass: HomeAssistant) -> None:
    assert await async_setup_component(hass, "homeassistant", {})
    hass.states.async_set("binary_sensor.back_door", "off", {"friendly_name": "Back door sensor", "device_class": "door"})
    hass.states.async_set("light.kitchen", "on", {"friendly_name": "Kitchen light"})
    hass.states.async_set("light.kitchen_lamp", "off", {"friendly_name": "Kitchen lamp"})
    hass.states.async_set("binary_sensor.hall_motion", "off", {"friendly_name": "Hall motion", "device_class": "motion"})
    for eid in ("binary_sensor.back_door", "light.kitchen", "light.kitchen_lamp", "binary_sensor.hall_motion"):
        async_expose_entity(hass, "conversation", eid, True)


@pytest.mark.parametrize("text,found", [
    ("The back door's closed.", [("binary_sensor.back_door", "off")]),
    ("The back door is open, and it's raining.", [("binary_sensor.back_door", "on")]),
    ("The kitchen light is on. The kitchen lamp is off.", [("light.kitchen", "on"), ("light.kitchen_lamp", "off")]),
    ("Is the kitchen light on?", []),  # a question
    ("If the back door is open, I'll tell you.", []),  # a condition
    ("The back door isn't open.", []),  # a negation: left alone
    ("I've turned the kitchen light off.", []),  # an action report, checked when it happens
    ("The kitchen light and the kitchen lamp are on.", []),  # two devices: not plain enough
    ("The back door was open earlier.", []),  # the past
    ("Hall motion is on.", []),  # not a door, light, switch or lock
])
async def test_what_counts_as_a_plain_statement(hass: HomeAssistant, text, found) -> None:
    await _house(hass)
    assert claims(hass, text) == found


@pytest.fixture
async def rt(hass: HomeAssistant):
    await _house(hass)
    memory = MemoryStore(hass, "grading")
    memory._save = Mock()
    reliability = Reliability(hass, "grading", {})
    reliability._save = Mock()
    at = (dt_util.utcnow() - timedelta(hours=2)).isoformat()
    episodes = [{"room": "Kitchen", "start": at, "turns": [
        {"who": "them", "text": "Is the back door shut?", "at": at},
        {"who": "me", "text": "Yes, the back door's closed.", "at": at},
        {"who": "them", "text": "And the kitchen light?", "at": at},
        {"who": "me", "text": "The kitchen light is on.", "at": at},
        {"who": "me", "text": "The kitchen lamp is off.", "text_without_time": True},  # kept before turns had a time
    ]}]
    return SimpleNamespace(options={"self_grading": True}, memory=memory, reliability=reliability, timeline=Mock(),
                           conversations=SimpleNamespace(recent=Mock(return_value=episodes)))


async def test_a_wrong_answer_is_logged_and_becomes_a_lesson(hass: HomeAssistant, rt) -> None:
    history = {"binary_sensor.back_door": ("on", "on"), "light.kitchen": ("on", "on")}

    async def states_at(hass, entity_ids, when):
        return {e: history[e] for e in entity_ids}

    with patch("custom_components.sentient.grading._states_at", side_effect=states_at):
        result = await async_grade(hass, rt)
    assert result["checked"] == 2
    assert [w["entity_id"] for w in result["wrong"]] == ["binary_sensor.back_door"]
    record = rt.reliability.records[0]
    assert record["problems"] == [{"kind": "wrong_state", "detail": "Back door sensor was on, not off"}]
    assert record["asked"] == "Is the back door shut?" and record["room"] == "Kitchen"
    assert result["lessons"] == ["Check the back door sensor on the device before saying whether it's open or closed."]
    assert rt.memory.items[0].category == "correction"
    summary = rt.reliability.summary()
    assert {"kind": "wrong_state", "label": "Said something was on, off, open or closed when it wasn't", "count": 1} in summary["counts"]
    with patch("custom_components.sentient.grading._states_at", side_effect=states_at):
        again = await async_grade(hass, rt)
    assert again["wrong"] == [] and len(rt.reliability.records) == 1  # the same night checked twice counts once


async def test_a_device_still_catching_up_is_not_a_mistake(hass: HomeAssistant, rt) -> None:
    history = {"binary_sensor.back_door": ("on", "off"), "light.kitchen": ("unavailable", None)}

    async def states_at(hass, entity_ids, when):
        return {e: history[e] for e in entity_ids}

    with patch("custom_components.sentient.grading._states_at", side_effect=states_at):
        result = await async_grade(hass, rt)
    assert result["wrong"] == [] and not rt.reliability.records


async def test_nothing_when_off(hass: HomeAssistant, rt) -> None:
    rt.options["self_grading"] = False
    assert "skipped" in await async_grade(hass, rt)
    rt.options["self_grading"] = True
    rt.reliability.options = {"reliability_log": False}
    assert "skipped" in await async_grade(hass, rt)
