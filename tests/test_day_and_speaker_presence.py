"""17 Nov fixes: the day chart's "said", one held warning logged once, and a speaker's "Is speaking" isn't a person."""

from datetime import timedelta

from homeassistant.core import HomeAssistant
from homeassistant.helpers import device_registry as dr, entity_registry as er
from homeassistant.util import dt as dt_util
from pytest_homeassistant_custom_component.common import MockConfigEntry

from custom_components.sentient.const import EVENT_HELD, EVENT_SPOKE
from custom_components.sentient.glance import day
from custom_components.sentient.presence import ActivityTracker, refresh_speaker_sensors
from custom_components.sentient.timeline import Timeline


def test_held_back_isnt_counted_as_said() -> None:
    now = dt_util.utcnow()
    items = [{"time": now.isoformat(), "kind": "said", "text": "a"},
             *({"time": now.isoformat(), "kind": "held", "text": "rain"} for _ in range(48))]
    assert day(items, now)["totals"]["spoke"] == 1


async def test_the_same_thing_held_again_is_one_entry(hass: HomeAssistant, freezer) -> None:
    tl = Timeline(hass, "t")
    await tl.async_start()
    for _ in range(3):
        hass.bus.async_fire(EVENT_HELD, {"message": "Rain's on the way.", "reason": "quiet_hours", "source": "rain"})
        await hass.async_block_till_done()
        freezer.tick(timedelta(minutes=10))
    held = [i for i in tl.items if i["kind"] == "held"]
    assert len(held) == 1 and held[0]["times"] == 3 and "3 times" in held[0]["detail"]
    # once it's said, the next hold is a new entry
    hass.bus.async_fire(EVENT_SPOKE, {"message": "Rain's on the way.", "speakers": []})
    hass.bus.async_fire(EVENT_HELD, {"message": "Rain's on the way.", "reason": "quiet_hours"})
    await hass.async_block_till_done()
    assert len([i for i in tl.items if i["kind"] == "held"]) == 2
    # a different reason is a different entry
    hass.bus.async_fire(EVENT_HELD, {"message": "Rain's on the way.", "reason": "hushed"})
    await hass.async_block_till_done()
    assert len([i for i in tl.items if i["kind"] == "held"]) == 3
    tl.async_stop()


async def test_a_speakers_is_speaking_sensor_isnt_a_person(hass: HomeAssistant) -> None:
    owner = MockConfigEntry(domain="esphome")
    owner.add_to_hass(hass)
    dev = dr.async_get(hass).async_get_or_create(config_entry_id=owner.entry_id, identifiers={("esphome", "spk")})
    reg = er.async_get(hass)
    reg.async_get_or_create("assist_satellite", "esphome", "sat", device_id=dev.id, suggested_object_id="kitchen")
    speaking = reg.async_get_or_create("binary_sensor", "esphome", "speaking", device_id=dev.id,
                                       suggested_object_id="kitchen_is_speaking", original_device_class="occupancy")
    camera = reg.async_get_or_create("binary_sensor", "esphome", "cam", device_id=dev.id,
                                     suggested_object_id="kitchen_camera_motion", original_device_class="motion")
    room = reg.async_get_or_create("binary_sensor", "zha", "pir", suggested_object_id="kitchen_motion",
                                   original_device_class="occupancy")
    for e, cls in ((speaking, "occupancy"), (camera, "motion"), (room, "occupancy")):
        hass.states.async_set(e.entity_id, "on", {"device_class": cls})
    refresh_speaker_sensors(hass)
    assert not ActivityTracker.is_presence(hass.states.get(speaking.entity_id))
    assert ActivityTracker.is_presence(hass.states.get(camera.entity_id))  # a camera's motion is still movement
    assert ActivityTracker.is_presence(hass.states.get(room.entity_id))
