"""The door camera's zone starts a visit when nobody rings; whichever comes first wins; an opening door cancels the zone."""

from datetime import timedelta
from unittest.mock import patch

from homeassistant.core import HomeAssistant, ServiceCall, SupportsResponse
from pytest_homeassistant_custom_component.common import async_fire_time_changed, async_mock_service

from tests.test_visitors import _setup

ZONE = "binary_sensor.frontdoor_zone_person_occupancy"
DOOR = "binary_sensor.frontdoor_sensor"
REPLY = {"kind": "visitor", "description": "A man in a red coat.", "parcel": False}


def _ai(hass: HomeAssistant):
    calls: list[ServiceCall] = []

    async def generate(call: ServiceCall):
        calls.append(call)
        return {"conversation_id": "x", "data": REPLY}

    hass.services.async_register("ai_task", "generate_data", generate, supports_response=SupportsResponse.ONLY)
    return calls


async def _house(hass, **extra):
    entry = await _setup(hass, doorstep_sensors=[ZONE], zone_door_sensors=[DOOR], zone_visits=True,
                         announce_visitors=True, **extra)
    hass.states.async_set(ZONE, "off")
    hass.states.async_set(DOOR, "off")
    await hass.async_block_till_done()
    return entry


async def _flip(hass, entity, state):
    hass.states.async_set(entity, state)
    await hass.async_block_till_done()


async def test_zone_alone_starts_a_visit(hass: HomeAssistant) -> None:
    await _house(hass)
    ai = _ai(hass)
    said = async_mock_service(hass, "assist_satellite", "announce")
    with patch("custom_components.sentient.visitors.asyncio.sleep"):
        await _flip(hass, ZONE, "on")
    assert len(ai) == 1 and len(said) == 1


async def test_zone_first_means_the_bell_is_ignored(hass: HomeAssistant) -> None:
    await _house(hass)
    ai = _ai(hass)
    async_mock_service(hass, "assist_satellite", "announce")
    with patch("custom_components.sentient.visitors.asyncio.sleep"):
        await _flip(hass, ZONE, "on")
        await _flip(hass, "binary_sensor.doorbell", "on")
    assert len(ai) == 1


async def test_bell_first_means_the_zone_is_ignored(hass: HomeAssistant) -> None:
    await _house(hass)
    ai = _ai(hass)
    async_mock_service(hass, "assist_satellite", "announce")
    with patch("custom_components.sentient.visitors.asyncio.sleep"):
        await _flip(hass, "binary_sensor.doorbell", "on")
        await _flip(hass, ZONE, "on")
    assert len(ai) == 1


async def test_door_opened_first_ignores_the_zone(hass: HomeAssistant) -> None:
    await _house(hass)
    ai = _ai(hass)
    async_mock_service(hass, "assist_satellite", "announce")
    with patch("custom_components.sentient.visitors.asyncio.sleep"):
        await _flip(hass, DOOR, "on")
        await _flip(hass, DOOR, "off")
        await _flip(hass, ZONE, "on")
    assert ai == []


async def test_a_door_opened_long_ago_does_not_count(hass: HomeAssistant, freezer) -> None:
    await _house(hass)
    ai = _ai(hass)
    async_mock_service(hass, "assist_satellite", "announce")
    with patch("custom_components.sentient.visitors.asyncio.sleep"):
        await _flip(hass, DOOR, "on")
        await _flip(hass, DOOR, "off")
        freezer.tick(timedelta(minutes=3))
        async_fire_time_changed(hass)
        await _flip(hass, ZONE, "on")
    assert len(ai) == 1


async def test_switched_off_the_zone_does_nothing(hass: HomeAssistant) -> None:
    await _setup(hass, doorstep_sensors=[ZONE], zone_visits=False)
    ai = _ai(hass)
    hass.states.async_set(ZONE, "off")
    with patch("custom_components.sentient.visitors.asyncio.sleep"):
        await _flip(hass, ZONE, "on")
    assert ai == []


async def test_a_visit_joins_the_security_timeline(hass: HomeAssistant) -> None:
    entry = await _house(hass)
    _ai(hass)
    async_mock_service(hass, "assist_satellite", "announce")
    seen = []
    entry.runtime_data.visitors.on_visit = lambda camera, text: seen.append((camera, text))
    with patch("custom_components.sentient.visitors.asyncio.sleep"):
        await _flip(hass, ZONE, "on")
    assert seen == [("camera.doorbell", "Someone reached the front door: A man in a red coat")]


async def test_door_opening_just_after_the_zone_is_someone_from_the_house(hass: HomeAssistant) -> None:
    """20 Nov 07:53: the camera saw them 0.2 s before the door opened; the briefing then said someone was in the yard."""
    entry = await _house(hass)
    rt = entry.runtime_data
    _ai(hass)
    async_mock_service(hass, "assist_satellite", "announce")
    with patch("custom_components.sentient.visitors.asyncio.sleep"):
        await _flip(hass, ZONE, "on")
        await _flip(hass, DOOR, "on")
    visit = rt.visitors.log[0]
    assert visit["household"] and visit["kind"] == "household"
    assert rt.visitors.today() == [] and rt.visitors.summary() == []
    assert not [i for i in rt.timeline.since(1, ["visitor"], 10)]
    assert any("Someone from the house went out" in i["text"] for i in rt.timeline.since(1, ["noticed"], 10))


async def test_door_opening_a_while_later_is_still_a_visitor(hass: HomeAssistant, freezer) -> None:
    entry = await _house(hass)
    rt = entry.runtime_data
    _ai(hass)
    async_mock_service(hass, "assist_satellite", "announce")
    with patch("custom_components.sentient.visitors.asyncio.sleep"):
        await _flip(hass, ZONE, "on")
        freezer.tick(timedelta(seconds=40))  # someone answering the door to a caller
        await _flip(hass, DOOR, "on")
    assert not rt.visitors.log[0].get("household") and len(rt.visitors.today()) == 1


def test_the_look_says_front_door_not_yard() -> None:
    import inspect

    from custom_components.sentient import visitors

    assert "never 'yard'" in inspect.getsource(visitors.Visitors.async_visit)
