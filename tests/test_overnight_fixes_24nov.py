"""24 Nov self-test: the right answer to "can you lock the front door?" was marked wrong, and "which room is
Conah's phone in?" got "it isn't showing up" while the room tracker said the hallway."""

from types import SimpleNamespace
from unittest.mock import AsyncMock, patch

from homeassistant.core import Context, HomeAssistant
from homeassistant.helpers import area_registry as ar
from homeassistant.setup import async_setup_component

from custom_components.sentient.house_context import CONF, HouseContext, prompt
from custom_components.sentient.room_trackers import prompt_line
from custom_components.sentient.selftest import SelfTest
from custom_components.sentient.sessions import Sessions


async def test_no_smart_lock_is_a_right_answer(hass: HomeAssistant) -> None:
    assert await async_setup_component(hass, "homeassistant", {})
    hass.states.async_set("binary_sensor.front_door", "off", {"friendly_name": "Front door", "device_class": "door"})
    runner = SelfTest(hass, "test", {"self_test_household": True}, lambda: None)
    question = next(q for q in runner.questions() if q.key == "no lock")
    right, _ = question.check("There's no smart lock I can use, so that one needs doing by hand — the Front door is shut.")
    assert right
    assert question.check("I don't have a lock I can use for that.")[0]
    assert not question.check("Locking the front door now.")[0]


async def test_the_tracker_line_says_to_answer_from_it(hass: HomeAssistant) -> None:
    ar.async_get(hass).async_create("hallway")
    hass.states.async_set("sensor.phone_area", "hallway", {"friendly_name": "Conah's phone Area", "area_id": "hallway"})
    line = prompt_line(hass, {"room_tracking": True, "room_trackers": ["sensor.phone_area"]})
    assert "Conah's phone: hallway" in line and "won't appear in the device list or the house context" in line


def _context(hass, options):
    rt = SimpleNamespace(options={CONF: True, **options}, speech=SimpleNamespace(guest_mode=False, async_say=AsyncMock()),
                         sessions=Sessions({}), profiles=None)
    return HouseContext(hass, lambda: rt)


async def test_a_chosen_room_tracker_is_in_the_house_context_when_asked_about(hass: HomeAssistant) -> None:
    hass.states.async_set("sensor.phone_area", "hallway", {"friendly_name": "Conah's phone Area"})
    obj = _context(hass, {"room_tracking": True, "room_trackers": ["sensor.phone_area"]})
    with patch("custom_components.sentient.house_context.async_should_expose", return_value=False):
        data = await obj.snapshot(Context(), request="Which room is Conah's phone in?")
        assert [r["entity_id"] for r in data["current_readings"]] == ["sensor.phone_area"]
        assert '"Conah\'s phone Area" (sensor.phone_area): "hallway"' in prompt(data)
        data = await obj.snapshot(Context(), request="Where's the vacuum?")  # not about the phone
        assert data["current_readings"] == []
    off = _context(hass, {"room_tracking": False, "room_trackers": ["sensor.phone_area"]})
    with patch("custom_components.sentient.house_context.async_should_expose", return_value=False):
        assert (await off.snapshot(Context(), request="Which room is Conah's phone in?"))["current_readings"] == []


async def test_a_camera_watch_works_out_what_to_look_for(hass: HomeAssistant) -> None:
    from unittest.mock import Mock

    from homeassistant.helpers import entity_registry as er

    from custom_components.sentient.watches import WatchManager

    drive = ar.async_get(hass).async_create("Drive")
    ents = er.async_get(hass)
    for domain, uid in (("camera", "gate_cam"), ("binary_sensor", "gate_zone")):
        entry = ents.async_get_or_create(domain, "test", uid, suggested_object_id=uid)
        ents.async_update_entity(entry.entity_id, area_id=drive.id)
    hass.states.async_set("camera.gate_cam", "idle", {"friendly_name": "Gate"})
    hass.states.async_set("binary_sensor.gate_zone", "off", {"friendly_name": "Drive zone"})
    manager = WatchManager(hass, "t", Mock(), {})
    manager._save = Mock()
    # the voice named the drive's zone sensor, with nothing to wait for: it's about seeing cars, so the camera
    watch = manager.add("any more cars appearing on the drive", "drive zone")
    assert watch.entity_id == "camera.gate_cam" and watch.question == "Can you see a car?"
    # the camera, with no question: worked out from the request
    watch = manager.add("watch for anyone at the gate", "camera.gate_cam")
    assert watch.question == "Can you see a person?"
