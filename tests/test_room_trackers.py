"""Room trackers (Bermuda and the like): say things where someone's phone is, else fall back to motion."""

from homeassistant.core import HomeAssistant
from homeassistant.helpers import area_registry as ar
from pytest_homeassistant_custom_component.common import async_mock_service

from custom_components.sentient.const import DOMAIN
from custom_components.sentient.room_trackers import describe, tracked_areas
from tests.test_init import OPTIONS, ROOMS, _Clock, _rooms, _setup

PHONE = "sensor.bermuda_phone_area"
BASE = {**OPTIONS, "speakers": ["assist_satellite.kitchen", "assist_satellite.living", "assist_satellite.hallway"],
        "motion_window": 10, "room_tracking": True, "room_trackers": [PHONE]}


async def _house(hass, freezer, **extra):
    clock = _Clock(freezer)
    entry = await _setup(hass, {**BASE, **extra})
    ids = await _rooms(hass, ROOMS)
    calls = async_mock_service(hass, "assist_satellite", "announce")
    # someone moving about in the living room, the phone's owner sitting still in the kitchen
    clock.at(4)
    hass.states.async_set("binary_sensor.living_motion", "on", {"device_class": "motion"})
    clock.back()
    return entry, ids, calls


_SAID = iter(range(1000))


async def _say(hass):
    # a different message each time: the same words to the same speaker straight after are held as a repeat
    res = await hass.services.async_call(DOMAIN, "say", {"message": f"Hello number {next(_SAID)}"}, blocking=True,
                                         return_response=True)
    return sorted(res["speakers"])


async def test_said_where_the_phone_is_not_where_the_motion_is(hass: HomeAssistant, freezer) -> None:
    entry, ids, calls = await _house(hass, freezer)
    hass.states.async_set(PHONE, "Kitchen", {"area_id": ids["Kitchen"], "friendly_name": "My phone Area"})
    assert await _say(hass) == ["assist_satellite.kitchen"]
    assert [e for c in calls for e in c.data["entity_id"]] == ["assist_satellite.kitchen"]
    assert entry.runtime_data.speech.tracked_route
    assert describe(hass, entry.runtime_data.options) == [
        {"tracker": "My phone Area", "entity_id": PHONE, "room": "Kitchen"}]
    # it moves with the phone
    hass.states.async_set(PHONE, "Hallway", {"area_id": ids["Hallway"]})
    assert await _say(hass) == ["assist_satellite.hallway"]


async def test_falls_back_to_motion_when_no_tracker_knows(hass: HomeAssistant, freezer) -> None:
    entry, ids, _ = await _house(hass, freezer)
    for state in ("unknown", "unavailable", "not_home", ""):
        hass.states.async_set(PHONE, state)
        assert await _say(hass) == ["assist_satellite.living"], state  # motion, as before
        assert not entry.runtime_data.speech.tracked_route
    # a room without a speaker (the bathroom): the phone can't be spoken to there, so motion decides
    bathroom = ar.async_get(hass).async_create("Bathroom")
    hass.states.async_set(PHONE, "Bathroom", {"area_id": bathroom.id})
    assert await _say(hass) == ["assist_satellite.living"]
    # a tracker that names a room that isn't an area
    hass.states.async_set(PHONE, "Somewhere odd")
    assert await _say(hass) == ["assist_satellite.living"]


async def test_off_by_default_and_when_switched_off(hass: HomeAssistant, freezer) -> None:
    entry, ids, _ = await _house(hass, freezer, room_tracking=False)
    hass.states.async_set(PHONE, "Kitchen", {"area_id": ids["Kitchen"]})
    assert await _say(hass) == ["assist_satellite.living"]
    assert tracked_areas(hass, {"room_trackers": [PHONE]}) == []  # not set: off
    assert describe(hass, {"room_trackers": [PHONE]}) == []


async def test_matches_by_area_name_or_alias_without_an_area_id(hass: HomeAssistant, freezer) -> None:
    _, ids, _ = await _house(hass, freezer)
    hass.states.async_set(PHONE, "kitchen")  # any case
    assert await _say(hass) == ["assist_satellite.kitchen"]
    ar.async_get(hass).async_update(ids["Living Room"], aliases={"Lounge"})
    hass.states.async_set(PHONE, "Lounge")
    assert await _say(hass) == ["assist_satellite.living"]


async def test_two_people_in_two_rooms_both_hear(hass: HomeAssistant, freezer) -> None:
    other = "sensor.bermuda_tag_area"
    _, ids, _ = await _house(hass, freezer, room_trackers=[PHONE, other])
    hass.states.async_set(PHONE, "Kitchen", {"area_id": ids["Kitchen"]})
    hass.states.async_set(other, "Hallway", {"area_id": ids["Hallway"]})
    assert await _say(hass) == ["assist_satellite.hallway", "assist_satellite.kitchen"]
    hass.states.async_set(other, "Kitchen", {"area_id": ids["Kitchen"]})  # same room: said once
    assert await _say(hass) == ["assist_satellite.kitchen"]


async def test_a_named_speaker_still_wins(hass: HomeAssistant, freezer) -> None:
    _, ids, calls = await _house(hass, freezer)
    hass.states.async_set(PHONE, "Kitchen", {"area_id": ids["Kitchen"]})
    res = await hass.services.async_call(DOMAIN, "say", {"message": "Hi", "speaker": "assist_satellite.living"},
                                         blocking=True, return_response=True)
    assert res["speakers"] == ["assist_satellite.living"]


async def test_a_phone_doesnt_make_the_house_think_someone_is_up(hass: HomeAssistant, freezer) -> None:
    """Only for choosing where to speak: a phone on a bedside table at 3am mustn't count as someone about."""
    clock = _Clock(freezer)
    entry = await _setup(hass, BASE)
    ids = await _rooms(hass, ROOMS)
    hass.states.async_set(PHONE, "Kitchen", {"area_id": ids["Kitchen"]})
    clock.back()
    assert entry.runtime_data.tracker.best_area() is None
    assert entry.runtime_data.tracker.occupied_areas() == []
