"""15 Nov: every camera was hidden from Assist, so each camera question failed and the voice said "nobody" anyway.

The camera tool now says why it couldn't look and not to guess, House health names each hidden camera, and the
nightly self-test fails an answer whose tools failed (it passed "nobody" before) or that offers to arm the alarm.
"""

from unittest.mock import patch

from homeassistant.components.homeassistant.exposed_entities import async_expose_entity
from homeassistant.core import HomeAssistant
from homeassistant.helpers import llm
from homeassistant.setup import async_setup_component

from custom_components.sentient.selftest import SECURITY_OFFER, failed_tools
from custom_components.sentient.wiring import unexposed_cameras
from tests.test_reliability import _setup
from tests.test_security_events import context, setup


async def test_a_camera_hidden_from_assist_says_so_and_not_to_guess(hass: HomeAssistant) -> None:
    from custom_components.sentient.security_voice import CameraActivityTool

    _events, _cache, rt = setup(hass)
    tool = CameraActivityTool()
    with patch("custom_components.sentient.security_voice._runtime", return_value=rt), \
            patch("custom_components.sentient.security_voice.allowed", return_value=False):
        out = await tool.async_call(hass, llm.ToolInput(tool_name=tool.name, tool_args={"camera": "garden"}), context())
        assert "isn't exposed to Assist" in out["error"] and "Garden" in out["error"]
        assert "don't say nobody" in out["note"]
        # a name it can't place, with no camera it may read: says that, not "which camera?" with nothing to pick
        out = await tool.async_call(hass, llm.ToolInput(tool_name=tool.name, tool_args={"camera": "moon"}), context())
        assert "None of the cameras" in out["error"] and "note" in out
    with patch("custom_components.sentient.security_voice._runtime", return_value=rt), \
            patch("custom_components.sentient.security_voice.allowed", return_value=True):
        out = await tool.async_call(hass, llm.ToolInput(tool_name=tool.name, tool_args={"camera": "moon"}), context())
        assert out["cameras"] == ["camera.garden"] and "note" in out


async def test_house_health_names_a_recorded_camera_assist_cant_see(hass: HomeAssistant) -> None:
    assert await async_setup_component(hass, "homeassistant", {})
    _events, _cache, rt = setup(hass)
    found = unexposed_cameras(hass, rt)
    assert [i["entity_id"] for i in found] == ["camera.garden"] and "isn't exposed to Assist" in found[0]["text"]
    assert unexposed_cameras(hass, rt, {"camera.garden"}) == []  # on the ignore list
    async_expose_entity(hass, "conversation", "camera.garden", True)
    assert unexposed_cameras(hass, rt) == []


def test_a_failed_tool_not_put_right_fails_the_answer() -> None:
    failing = {"tool_name": "sentient_camera_activity", "tool_result": {"error": "Which camera?"}}
    working = {"tool_name": "sentient_camera_activity", "tool_result": {"visits": []}}
    assert failed_tools([failing]) == ["sentient_camera_activity"]
    assert failed_tools([failing, working]) == []  # put right on a retry
    assert failed_tools([working]) == []


def test_offering_to_arm_the_alarm_is_caught() -> None:
    assert SECURITY_OFFER.search("The alarm is currently disarmed. Shall I arm it in away mode?")
    assert SECURITY_OFFER.search("It's armed. Would you like me to disarm it?")
    assert not SECURITY_OFFER.search("The alarm is currently disarmed.")
    assert not SECURITY_OFFER.search("It's armed away.")


async def test_the_self_test_is_never_counted_as_a_person_and_its_tools_are_kept(hass: HomeAssistant) -> None:
    entry = await _setup(hass)
    conv = entry.runtime_data.conversations
    conv.testing = "Has anyone been seen on the Garden camera in the last two hours?"
    conv.attach("t1", None)
    from custom_components.sentient.selftest import ACTIVE

    token = ACTIVE.set(True)
    try:
        conv._chat("t1", "content_added", {"content": {"role": "user", "content": conv.testing}})
        conv._chat("t1", "content_added", {"content": {"role": "tool_result", "tool_name": "sentient_camera_activity",
                                                       "tool_result": {"error": "Which camera?"}}})
        conv._chat("t1", "content_added", {"content": {"role": "assistant", "content": "No, nobody's been seen."}})
    finally:
        ACTIVE.reset(token)
    conv.testing = None
    assert [c["tool_name"] for c in conv.test_tools["t1"]] == ["sentient_camera_activity"]
    summary = entry.runtime_data.reliability.summary()
    assert summary["turns"] == 0 and summary["recent"] == []


async def test_movement_in_an_outdoor_area_isnt_someone_in_the_house(hass: HomeAssistant) -> None:
    """15 Nov: the doorbell camera's motion made "Front door" the likeliest room in the house."""
    from homeassistant.helpers import area_registry as ar, entity_registry as er

    from custom_components.sentient.presence import ActivityTracker

    areas, ents = ar.async_get(hass), er.async_get(hass)
    front = areas.async_create("Front door")
    kitchen = areas.async_create("Kitchen")
    for obj, area in (("doorbell_motion", front.id), ("kitchen_motion", kitchen.id)):
        entry = ents.async_get_or_create("binary_sensor", "test", obj, suggested_object_id=obj)
        ents.async_update_entity(entry.entity_id, area_id=area)
        hass.states.async_set(entry.entity_id, "on", {"device_class": "motion"})
    tracker = ActivityTracker(hass, {})
    tracker._seed_from_states()
    assert set(tracker.scores()) == {front.id, kitchen.id}
    tracker.options = {"outdoor_areas": [front.id]}
    assert set(tracker.scores()) == {kitchen.id} and tracker.best_area() in (kitchen.id, None)
