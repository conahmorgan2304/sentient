"""Spoken speaker names resolve forgivingly: 'living room', 'livingroom', 'the kitchen speaker'."""

from homeassistant.core import HomeAssistant
from homeassistant.helpers import area_registry as ar, entity_registry as er

from custom_components.sentient.speech import SpeechRouter

KITCHEN_ECHO = "media_player.kitchen_2"
LIVING_ECHO = "media_player.livingroom"
KITCHEN_SAT = "assist_satellite.kitchen_speaker"


def _setup(hass: HomeAssistant, speakers, with_areas=True) -> SpeechRouter:
    areas = ar.async_get(hass)
    reg = er.async_get(hass)
    kitchen = areas.async_create("Kitchen")
    living = areas.async_create("Living Room")
    for eid, area in ((KITCHEN_ECHO, kitchen), (LIVING_ECHO, living), (KITCHEN_SAT, kitchen)):
        domain, obj = eid.split(".")
        reg.async_get_or_create(domain, "test", obj, suggested_object_id=obj)
        if with_areas:
            reg.async_update_entity(eid, area_id=area.id)
    hass.states.async_set(KITCHEN_ECHO, "idle", {"friendly_name": "kitchen"})
    hass.states.async_set(LIVING_ECHO, "idle", {"friendly_name": "Livingroom"})
    hass.states.async_set(KITCHEN_SAT, "idle", {"friendly_name": "Kitchen speaker"})
    return SpeechRouter(hass, {"speakers": speakers})


async def test_spaced_and_squashed_room_names_match(hass: HomeAssistant) -> None:
    router = _setup(hass, [LIVING_ECHO, KITCHEN_ECHO])
    for spoken in ("living room", "livingroom", "Living Room", "the living room speaker", "living room echo"):
        assert router.find_speaker(spoken) == LIVING_ECHO, spoken


async def test_kitchen_phrasings_find_the_echo(hass: HomeAssistant) -> None:
    router = _setup(hass, [KITCHEN_ECHO, LIVING_ECHO])
    for spoken in ("kitchen", "the kitchen", "kitchen speaker", "Kitchen Echo", "kitchen_2"):
        assert router.find_speaker(spoken) == KITCHEN_ECHO, spoken


async def test_exact_name_beats_partial_match(hass: HomeAssistant) -> None:
    router = _setup(hass, [KITCHEN_SAT, KITCHEN_ECHO], with_areas=False)
    assert router.find_speaker("kitchen") == KITCHEN_ECHO  # friendly name is exactly "kitchen"
    assert router.find_speaker("kitchen speaker") == KITCHEN_SAT


async def test_unknown_room_is_not_found(hass: HomeAssistant) -> None:
    router = _setup(hass, [KITCHEN_ECHO])
    assert router.find_speaker("garage") is None
    assert router.find_speaker("") is None
    assert router.find_speaker(KITCHEN_ECHO) == KITCHEN_ECHO


async def test_named_echo_is_used_even_when_echos_are_important_only(hass: HomeAssistant) -> None:
    from unittest.mock import AsyncMock, patch

    from pytest_homeassistant_custom_component.common import async_mock_service

    router = SpeechRouter(hass, {"speakers": [KITCHEN_SAT, KITCHEN_ECHO], "media_important_only": True,
                                 "quiet_start": "00:00:00", "quiet_end": "00:00:00"})
    hass.states.async_set(KITCHEN_ECHO, "idle", {"friendly_name": "kitchen"})
    hass.states.async_set(KITCHEN_SAT, "idle", {"friendly_name": "Kitchen speaker"})
    calls = async_mock_service(hass, "notify", "alexa_media")
    with patch("custom_components.sentient.speech._platform", return_value="alexa_media"), patch(
        "custom_components.sentient.speech.asyncio.sleep", new_callable=AsyncMock
    ):
        await router.async_say("Dinner is ready", "normal", [KITCHEN_ECHO], source="conversation")
    assert [c.data["target"] for c in calls] == [KITCHEN_ECHO]
