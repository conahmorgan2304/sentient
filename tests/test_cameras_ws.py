"""The Security tab's camera list: real cameras only, with today's detections."""

from homeassistant.core import HomeAssistant
from pytest_homeassistant_custom_component.common import MockConfigEntry

from custom_components.sentient.const import DOMAIN


async def test_cameras_command_lists_only_real_cameras(hass: HomeAssistant, hass_ws_client) -> None:
    for eid, name in (("camera.gate", "Gate Camera"), ("camera.garden", "Garden Camera"),
                      ("camera.front_door_doorbell_sd_stream", "front door doorbell SD Stream"),
                      ("camera.hallway_screenshot", "hallway Screenshot")):
        hass.states.async_set(eid, "recording", {"friendly_name": name})
    entry = MockConfigEntry(domain=DOMAIN, title="Nova", unique_id=DOMAIN, data={"persona": "Nova", "security_incidents": True})
    entry.add_to_hass(hass)
    assert await hass.config_entries.async_setup(entry.entry_id)
    await hass.async_block_till_done()
    entry.runtime_data.incidents.add("camera.gate", "Frigate reported person", "event", event_id="e1")
    ws = await hass_ws_client(hass)
    await ws.send_json({"id": 1, "type": "sentient/cameras"})
    out = (await ws.receive_json())["result"]
    rows = {r["entity_id"]: r for r in out["cameras"]}
    assert set(rows) == {"camera.gate", "camera.garden"}
    assert rows["camera.gate"]["today"] == 1 and rows["camera.garden"]["today"] == 0
    assert out["hidden"] == 2


def test_settings_dependencies_point_at_real_settings_in_the_same_group():
    from custom_components.sentient.websocket import DEPENDS, GROUPS

    for child, parent in DEPENDS.items():
        assert any(child in [str(k) for k in g[4]] and parent in [str(k) for k in g[4]] for g in GROUPS), (child, parent)
