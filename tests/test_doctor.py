"""The setup doctor says what's set up right and what to fix."""

from homeassistant.core import HomeAssistant
from pytest_homeassistant_custom_component.common import MockConfigEntry

from custom_components.sentient.const import DOMAIN


async def test_doctor_finds_what_to_fix(hass: HomeAssistant, hass_ws_client, tmp_path) -> None:
    hass.config.media_dirs = {"local": str(tmp_path)}
    hass.states.async_set("assist_satellite.kitchen", "idle", {"friendly_name": "Kitchen", "supported_features": 3})
    hass.states.async_set("media_player.lounge", "unavailable", {"friendly_name": "Lounge"})
    hass.states.async_set("camera.gate", "unavailable", {"friendly_name": "Gate"})
    entry = MockConfigEntry(domain=DOMAIN, title="Nova", unique_id=DOMAIN, data={
        "persona": "Nova", "speakers": ["assist_satellite.kitchen", "media_player.lounge", "media_player.gone"],
        "drive_camera": "camera.gate", "books": True})
    entry.add_to_hass(hass)
    assert await hass.config_entries.async_setup(entry.entry_id)
    await hass.async_block_till_done()
    ws = await hass_ws_client(hass)
    await ws.send_json({"id": 1, "type": "sentient/doctor"})
    out = (await ws.receive_json())["result"]
    by = {r["name"]: r for r in out["rows"]}
    assert out["status"] == "bad"
    assert by["Voice AI agent"]["status"] == "bad" and by["Voice AI agent"]["fix"]
    assert by["Kitchen"]["status"] == "ok" and by["Lounge"]["status"] == "bad" and by["media_player.gone"]["status"] == "bad"
    assert by["Gate"]["status"] == "warn" and "Sleepy cameras" in by["Gate"]["fix"]
    assert by["Default speaker"]["status"] == "warn"
    assert by["Books folder"]["status"] == "warn"
    (tmp_path / "books").mkdir()
    await ws.send_json({"id": 2, "type": "sentient/doctor"})
    by = {r["name"]: r for r in (await ws.receive_json())["result"]["rows"]}
    assert by["Books folder"]["status"] == "ok"


async def test_a_whole_camera_sensor_for_camera_security_is_flagged(hass: HomeAssistant) -> None:
    """Live 13 Nov: the gate camera's own person sensor (street in view) phoned about passers-by; the gate zone's
    sensor stayed off. The doctor names the zone sensor to use instead."""
    from homeassistant.helpers import device_registry as dr, entity_registry as er

    from custom_components.sentient.doctor import whole_camera_sensors

    frigate = MockConfigEntry(domain="frigate", entry_id="FRIG")
    frigate.add_to_hass(hass)
    dev = dr.async_get(hass).async_get_or_create(config_entry_id="FRIG", identifiers={("frigate", "gate")}, name="Gate")
    reg = er.async_get(hass)
    reg.async_get_or_create("camera", "frigate", "FRIG:camera:gate", device_id=dev.id, config_entry=frigate)
    whole = reg.async_get_or_create("binary_sensor", "frigate", "FRIG:occupancy_sensor:gate_person", device_id=dev.id,
                                    config_entry=frigate).entity_id
    zone = reg.async_get_or_create("binary_sensor", "frigate", "FRIG:occupancy_sensor:Gate_person", device_id=dev.id,
                                   config_entry=frigate).entity_id
    reg.async_get_or_create("binary_sensor", "frigate", "FRIG:occupancy_sensor:Gate_car", device_id=dev.id, config_entry=frigate)
    assert whole_camera_sensors(hass, [whole]) == [(whole, [zone])]
    assert whole_camera_sensors(hass, [zone]) == []  # a zone's own sensor is what's wanted
    from custom_components.sentient.doctor import street_facing_whole_camera

    # live 13 Nov (1.5.10): the garden and shed cameras were flagged, but they're behind the fence
    assert street_facing_whole_camera(hass, {"camera_security_sensors": [whole]}) == [(whole, [zone])]
    assert street_facing_whole_camera(hass, {"camera_security_sensors": [whole], "camera_security_inside": [whole]}) == []
