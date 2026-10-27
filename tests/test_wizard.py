"""The set-up wizard and thumbs up/down on remarks."""

from homeassistant.core import HomeAssistant
from homeassistant.helpers import area_registry as ar, device_registry as dr, entity_registry as er
from pytest_homeassistant_custom_component.common import MockConfigEntry, async_mock_service

from custom_components.sentient.const import DOMAIN


async def _setup(hass: HomeAssistant, **data) -> MockConfigEntry:
    entry = MockConfigEntry(domain=DOMAIN, title="Nova", unique_id=DOMAIN, data={"persona": "Nova", **data})
    entry.add_to_hass(hass)
    assert await hass.config_entries.async_setup(entry.entry_id)
    await hass.async_block_till_done()
    return entry


def _house(hass: HomeAssistant) -> None:
    other = MockConfigEntry(domain="esphome")
    other.add_to_hass(hass)
    kitchen = ar.async_get(hass).async_create("Kitchen")
    devs, ents = dr.async_get(hass), er.async_get(hass)
    dev = devs.async_get_or_create(config_entry_id=other.entry_id, identifiers={("esphome", "k")})
    devs.async_update_device(dev.id, area_id=kitchen.id)
    ents.async_get_or_create("assist_satellite", "esphome", "sat", suggested_object_id="kitchen", device_id=dev.id)
    ents.async_get_or_create("binary_sensor", "esphome", "pir", suggested_object_id="kitchen_motion", device_id=dev.id)
    hass.states.async_set("assist_satellite.kitchen", "idle")
    hass.states.async_set("binary_sensor.kitchen_motion", "off", {"device_class": "motion"})
    hass.states.async_set("ai_task.gemini", "unknown")
    hass.states.async_set("camera.front_doorbell", "idle", {"friendly_name": "Front doorbell"})
    hass.states.async_set("vacuum.robot", "docked")
    hass.states.async_set("sensor.washing_machine_power", "0", {"device_class": "power", "unit_of_measurement": "W"})
    hass.states.async_set("sensor.rabbit_hutch_temperature", "12", {"device_class": "temperature"})
    hass.states.async_set("sensor.lounge_temperature", "20", {"device_class": "temperature"})


async def test_wizard_suggests_from_the_house(hass: HomeAssistant, hass_ws_client) -> None:
    from homeassistant.setup import async_setup_component

    assert await async_setup_component(hass, "homeassistant", {})
    _house(hass)
    await _setup(hass)
    ws = await hass_ws_client(hass)
    await ws.send_json({"id": 1, "type": "sentient/wizard"})
    res = (await ws.receive_json())["result"]
    assert res["show"] and not res["done"]
    assert [s["id"] for s in res["steps"]][:2] == ["name", "speakers"]
    data = res["data"]
    assert data["speakers"] == ["assist_satellite.kitchen"]
    assert data["inside_sensors"] == ["binary_sensor.kitchen_motion"]
    assert data["ai_task_entity"] == "ai_task.gemini"
    assert data["door_camera"] == "camera.front_doorbell"
    assert data["appliances"] == ["vacuum.robot", "sensor.washing_machine_power"]
    assert data["animal_sensors"] == ["sensor.rabbit_hutch_temperature"]
    assert data["backup_agent"] == "conversation.home_assistant"
    assert data["chat_agent"] == res["own_agent"] == "conversation.nova_with_backup"
    assert "speakers" in res["suggested"] and res["labels"]["animal_sensors"]


async def test_wizard_never_overwrites_choices_and_saves_one_step(hass: HomeAssistant, hass_ws_client) -> None:
    _house(hass)
    entry = await _setup(hass, speakers=["assist_satellite.other"], chattiness="chatty")
    ws = await hass_ws_client(hass)
    await ws.send_json({"id": 1, "type": "sentient/wizard"})
    res = (await ws.receive_json())["result"]
    assert not res["show"]  # already set up: not shown by itself
    assert res["data"]["speakers"] == ["assist_satellite.other"] and "speakers" not in res["suggested"]
    await ws.send_json({"id": 2, "type": "sentient/wizard/save", "step": "name",
                        "data": {"persona": " Orbit ", "personality": "cheeky"}})
    out = await ws.receive_json()
    assert out["success"], out
    await hass.async_block_till_done()
    assert entry.options["persona"] == "Orbit" and entry.options["personality"] == "cheeky"
    assert entry.options["chattiness"] == "chatty" and entry.options["speakers"] == ["assist_satellite.other"]


async def test_wizard_test_line_and_finish(hass: HomeAssistant, hass_ws_client) -> None:
    _house(hass)
    entry = await _setup(hass, speakers=["assist_satellite.kitchen"], default_speaker="assist_satellite.kitchen")
    said = async_mock_service(hass, "assist_satellite", "announce")
    ws = await hass_ws_client(hass)
    await ws.send_json({"id": 1, "type": "sentient/wizard/finish", "test": True})
    res = (await ws.receive_json())["result"]
    assert res["spoken"] and said[-1].data["message"].startswith("Hello, I'm Nova.")
    await ws.send_json({"id": 2, "type": "sentient/wizard/finish"})
    assert (await ws.receive_json())["result"]["done"]
    await hass.async_block_till_done()
    assert entry.options["wizard_done"]


async def test_thumbs_down_quietens_a_kind_of_remark(hass: HomeAssistant, hass_ws_client) -> None:
    entry = await _setup(hass, speakers=["assist_satellite.kitchen"], default_speaker="assist_satellite.kitchen",
                         quiet_start="00:00:00", quiet_end="00:00:00")
    hass.states.async_set("assist_satellite.kitchen", "idle")
    said = async_mock_service(hass, "assist_satellite", "announce")
    ws = await hass_ws_client(hass)
    for n in range(3):
        await ws.send_json({"id": n + 1, "type": "sentient/feedback/thumb", "text": "Robot's finished.",
                            "source": "appliance:finished", "up": False})
        assert (await ws.receive_json())["success"]
    result = await entry.runtime_data.speech.async_say("Robot's finished.", "normal", None, source="appliance:finished")
    assert not result.spoken and result.reason == "thumbs_down" and not said
    # urgent things and the doorbell are never quietened this way
    await ws.send_json({"id": 9, "type": "sentient/feedback/thumb", "text": "Someone's at the door.", "source": "visitors", "up": False})
    assert (await ws.receive_json())["result"]["about"] == []


async def test_thumbs_on_a_noticed_remark_count_against_what_it_was_about(hass: HomeAssistant, hass_ws_client) -> None:
    entry = await _setup(hass)
    fb = entry.runtime_data.feedback
    fb.remarked(["switch.kettle"], "The kettle's boiled.")
    ws = await hass_ws_client(hass)
    await ws.send_json({"id": 1, "type": "sentient/feedback/thumb", "text": "The kettle's boiled.", "source": "notice", "up": False})
    assert (await ws.receive_json())["result"]["about"] == ["switch.kettle"]
    assert round(fb.score("switch.kettle"), 3) == 1.0
    await ws.send_json({"id": 2, "type": "sentient/feedback/thumb", "text": "The kettle's boiled.", "source": "notice", "up": True})
    await ws.receive_json()
    assert round(fb.score("switch.kettle"), 3) == 0 and fb.liked == ["The kettle's boiled."]


async def test_thumbs_down_on_an_old_remark_finds_what_it_was_about(hass: HomeAssistant, hass_ws_client) -> None:
    hass.states.async_set("sensor.kettle_status", "standby", {"friendly_name": "Kettle Status"})
    entry = await _setup(hass, notice_entities=["sensor.kettle_status", "binary_sensor.bedroom_window"])
    ws = await hass_ws_client(hass)
    await ws.send_json({"id": 1, "type": "sentient/feedback/thumb", "text": "The kettle has started heating up.",
                        "source": "notice", "up": False})
    assert (await ws.receive_json())["result"]["about"] == ["Kettle Status"]
    fb = entry.runtime_data.feedback
    assert round(fb.score("sensor.kettle_status"), 2) == 1.0 and fb.score("source:notice") == 0
