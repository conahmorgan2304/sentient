"""Extra views of a camera (streams, screenshots, maps) aren't counted as cameras."""

from custom_components.sentient.cameras import cameras, hidden, is_duplicate_view
from custom_components.sentient.const import CONF_HIDE_SUBSTREAMS, CONF_IGNORE_CAMERAS

REAL = ["camera.garden", "camera.gate", "camera.shed", "camera.frontdoor", "camera.front_door_doorbell"]
EXTRA = ["camera.front_door_doorbell_hd_stream", "camera.front_door_doorbell_sd_stream",
         "camera.hallway_screenshot", "camera.upstairs_screenshot", "camera.vacuum_live_map"]
TABLETS = ["camera.hallway_camera", "camera.upstairs_camera"]


def _house(hass):
    for eid in REAL + EXTRA + TABLETS:
        hass.states.async_set(eid, "idle")


async def test_extra_views_are_left_out(hass):
    _house(hass)
    names = {s.entity_id for s in cameras(hass, {})}
    assert names == set(REAL + TABLETS)
    assert hidden(hass, {}) == sorted(EXTRA)


async def test_user_can_ignore_more(hass):
    _house(hass)
    names = {s.entity_id for s in cameras(hass, {CONF_IGNORE_CAMERAS: TABLETS})}
    assert names == set(REAL)


async def test_hiding_extra_views_can_be_switched_off(hass):
    _house(hass)
    assert len(cameras(hass, {CONF_HIDE_SUBSTREAMS: False})) == 12
    assert len(cameras(hass, {CONF_HIDE_SUBSTREAMS: False, CONF_IGNORE_CAMERAS: ["camera.garden"]})) == 11


async def test_ordinary_names_are_not_mistaken(hass):
    for eid in ("camera.main_street", "camera.high_gate", "camera.low_shed"):
        hass.states.async_set(eid, "idle")
        assert not is_duplicate_view(hass, eid)


async def test_self_test_conversation_is_dropped(hass):
    from custom_components.sentient.conversations import Conversations

    conv = Conversations(hass, "x", {})
    cid = "01JXXXXXXXXXXXXXXXXXXXXXXXX"
    conv._open[cid] = {"id": cid[-10:], "start": "2026-11-09T00:00:00+00:00", "turns": [{"who": "them", "text": "Is the alarm on?"}]}
    conv._ours[cid] = {"room": None}
    conv.episodes.append({"id": cid[-10:], "start": "2026-11-09T00:00:00+00:00", "turns": []})
    conv.drop(cid)
    assert not conv.episodes and cid not in conv._open and cid in conv.skip


async def test_a_tablets_own_camera_is_not_a_house_camera(hass):
    from homeassistant.helpers import device_registry as dr, entity_registry as er
    from pytest_homeassistant_custom_component.common import MockConfigEntry

    entry = MockConfigEntry(domain="esphome")
    entry.add_to_hass(hass)
    dev = dr.async_get(hass).async_get_or_create(config_entry_id=entry.entry_id, identifiers={("esphome", "tab")})
    reg = er.async_get(hass)
    reg.async_get_or_create("camera", "esphome", "tab_cam", suggested_object_id="hall_cam", device_id=dev.id, config_entry=entry)
    reg.async_get_or_create("assist_satellite", "esphome", "tab_sat", suggested_object_id="hall_sat", device_id=dev.id, config_entry=entry)
    door = dr.async_get(hass).async_get_or_create(config_entry_id=entry.entry_id, identifiers={("esphome", "door")})
    reg.async_get_or_create("camera", "esphome", "door_cam", suggested_object_id="door_cam", device_id=door.id, config_entry=entry)
    reg.async_get_or_create("media_player", "esphome", "door_spk", suggested_object_id="door_spk", device_id=door.id, config_entry=entry)
    for eid in ("camera.hall_cam", "camera.door_cam"):
        hass.states.async_set(eid, "idle")
    assert [s.entity_id for s in cameras(hass, {})] == ["camera.door_cam"]
