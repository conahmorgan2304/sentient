"""House health: offline devices, low batteries, updates."""

from datetime import timedelta

from homeassistant.core import HomeAssistant
from homeassistant.util import dt as dt_util
from homeassistant.helpers import device_registry as dr
from homeassistant.helpers import entity_registry as er
from pytest_homeassistant_custom_component.common import MockConfigEntry

from custom_components.sentient.const import DOMAIN


async def test_offline_battery_updates(hass: HomeAssistant, freezer) -> None:
    other = MockConfigEntry(domain="zha"); other.add_to_hass(hass)
    devs, ents = dr.async_get(hass), er.async_get(hass)
    tablet = devs.async_get_or_create(config_entry_id=other.entry_id, identifiers={("zha", "tab")}, name="Hallway tablet")
    sensor = devs.async_get_or_create(config_entry_id=other.entry_id, identifiers={("zha", "door")}, name="Back door")
    for obj, dev in (("tab_screen", tablet), ("tab_motion", tablet), ("door_battery", sensor), ("door_battery_plus", sensor)):
        ents.async_get_or_create("sensor", "zha", obj, suggested_object_id=obj, device_id=dev.id)
    hass.states.async_set("sensor.tab_screen", "unavailable")
    hass.states.async_set("sensor.tab_motion", "unavailable")
    hass.states.async_set("sensor.door_battery", "15", {"device_class": "battery", "friendly_name": "Back door battery"})
    hass.states.async_set("sensor.door_battery_plus", "14", {"device_class": "battery", "friendly_name": "Back door battery+"})
    hass.states.async_set("update.frigate", "on", {"friendly_name": "Frigate update"})
    entry = MockConfigEntry(domain=DOMAIN, title="Nova", unique_id=DOMAIN, data={"persona": "Nova", "speakers": []})
    entry.add_to_hass(hass)
    assert await hass.config_entries.async_setup(entry.entry_id)
    await hass.async_block_till_done()
    health = entry.runtime_data.health
    health._never.clear()
    from unittest.mock import patch as _p

    async def _had_readings(self, entity_ids):  # the tablet had readings earlier in the week
        for e in entity_ids:
            self._checked[e] = dt_util.utcnow()
        self._checking = False
        self.async_check()

    _p.object(type(health), "_async_verify", _had_readings).start()
    assert [i["kind"] for i in health.async_check()] == ["battery"]  # tablet not offline long enough yet
    freezer.tick(timedelta(minutes=11))
    health.async_check()  # looks at the tablet's history first, then reports it
    await hass.async_block_till_done()
    issues = health.issues
    assert [i["name"] for i in issues] == ["Hallway tablet", "Back door battery+"]
    assert health.describe() == ["Hallway tablet is offline", "Back door battery+ is at 14%"]
    await hass.async_block_till_done()
    st = hass.states.get("sensor.nova_house_health")
    assert st.state == "2" and st.attributes["updates"] == ["Frigate update"]
    hass.config_entries.async_update_entry(entry, options={"health_ignore": ["sensor.tab_motion"]})
    await hass.async_block_till_done()
    assert health.describe() == ["Back door battery+ is at 14%"]


async def test_connectivity_sensor_marks_device_offline(hass: HomeAssistant, freezer) -> None:
    other = MockConfigEntry(domain="kiosk"); other.add_to_hass(hass)
    devs, ents = dr.async_get(hass), er.async_get(hass)
    tab = devs.async_get_or_create(config_entry_id=other.entry_id, identifiers={("kiosk", "t")}, name="Landing tablet")
    ents.async_get_or_create("light", "kiosk", "screen", suggested_object_id="landing_screen", device_id=tab.id)
    ents.async_get_or_create(
        "binary_sensor", "kiosk", "link", suggested_object_id="landing_link", device_id=tab.id,
        entity_category=er.EntityCategory.DIAGNOSTIC,
    )
    hass.states.async_set("light.landing_screen", "on")
    hass.states.async_set("binary_sensor.landing_link", "off", {"device_class": "connectivity"})
    entry = MockConfigEntry(domain=DOMAIN, title="Nova", unique_id=DOMAIN, data={"persona": "Nova", "speakers": []})
    entry.add_to_hass(hass)
    assert await hass.config_entries.async_setup(entry.entry_id)
    await hass.async_block_till_done()
    freezer.tick(timedelta(minutes=11))
    assert [i["name"] for i in entry.runtime_data.health.async_check()] == ["Landing tablet"]



async def test_entities_that_never_have_a_reading_are_not_offline(hass: HomeAssistant, freezer) -> None:
    other = MockConfigEntry(domain="frigate"); other.add_to_hass(hass)
    devs, ents = dr.async_get(hass), er.async_get(hass)
    hub = devs.async_get_or_create(config_entry_id=other.entry_id, identifiers={("frigate", "hub")}, name="Frigate")
    ents.async_get_or_create("sensor", "frigate", "last_cam", suggested_object_id="frigate_last_camera", device_id=hub.id)
    ents.async_get_or_create("update", "frigate", "server", suggested_object_id="frigate_server", device_id=hub.id)
    hass.states.async_set("sensor.frigate_last_camera", "unavailable")
    hass.states.async_set("update.frigate_server", "off")
    entry = MockConfigEntry(domain=DOMAIN, title="Nova", unique_id=DOMAIN, data={"persona": "Nova", "speakers": []})
    entry.add_to_hass(hass)
    assert await hass.config_entries.async_setup(entry.entry_id)
    await hass.async_block_till_done()
    health = entry.runtime_data.health
    health._never["sensor.frigate_last_camera"] = dt_util.utcnow() + timedelta(hours=6)  # what the history check finds
    freezer.tick(timedelta(minutes=11))
    health.async_check()
    await hass.async_block_till_done()
    assert not [i for i in health.issues if i["kind"] == "offline"]


async def test_offline_before_a_restart_is_reported_straight_away(hass: HomeAssistant) -> None:
    other = MockConfigEntry(domain="zha"); other.add_to_hass(hass)
    devs, ents = dr.async_get(hass), er.async_get(hass)
    plug = devs.async_get_or_create(config_entry_id=other.entry_id, identifiers={("zha", "plug")}, name="Hallway plug")
    ents.async_get_or_create("switch", "zha", "plug", suggested_object_id="hallway_plug", device_id=plug.id)
    hass.states.async_set("switch.hallway_plug", "unavailable")  # "changed" just now, at start-up
    entry = MockConfigEntry(domain=DOMAIN, title="Nova", unique_id=DOMAIN, data={"persona": "Nova", "speakers": []})
    entry.add_to_hass(hass)
    assert await hass.config_entries.async_setup(entry.entry_id)
    await hass.async_block_till_done()
    health = entry.runtime_data.health
    health._never.clear()
    health._checked["switch.hallway_plug"] = dt_util.utcnow()
    health._went_off["switch.hallway_plug"] = dt_util.utcnow() - timedelta(hours=2)  # what history says
    health.was_offline_before = lambda: {"Hallway plug"}  # the timeline had it offline before the restart
    issues = health.async_check()
    assert [i["name"] for i in issues if i["kind"] == "offline"] == ["Hallway plug"]


async def test_still_reconnecting_after_a_restart_is_not_offline(hass: HomeAssistant, freezer) -> None:
    other = MockConfigEntry(domain="tuya_local"); other.add_to_hass(hass)
    devs, ents = dr.async_get(hass), er.async_get(hass)
    plug = devs.async_get_or_create(config_entry_id=other.entry_id, identifiers={("tuya", "coffee")}, name="Coffee machine")
    ents.async_get_or_create("switch", "tuya_local", "coffee", suggested_object_id="coffee", device_id=plug.id)
    hass.states.async_set("switch.coffee", "unavailable")  # its integration hasn't reconnected yet
    entry = MockConfigEntry(domain=DOMAIN, title="Nova", unique_id=DOMAIN, data={"persona": "Nova", "speakers": []})
    entry.add_to_hass(hass)
    assert await hass.config_entries.async_setup(entry.entry_id)
    await hass.async_block_till_done()
    health = entry.runtime_data.health
    health._never.clear()
    health._checked["switch.coffee"] = dt_util.utcnow()
    health._went_off["switch.coffee"] = dt_util.utcnow() - timedelta(minutes=45)  # history: since the last restart
    assert not [i for i in health.async_check() if i["kind"] == "offline"]
    freezer.tick(timedelta(minutes=11))  # still gone ten minutes after start-up: that's real
    health._checked["switch.coffee"] = dt_util.utcnow()
    assert [i["name"] for i in health.async_check() if i["kind"] == "offline"] == ["Coffee machine"]


async def test_offline_device_stays_reported_while_its_history_is_rechecked(hass: HomeAssistant, freezer) -> None:
    other = MockConfigEntry(domain="zha"); other.add_to_hass(hass)
    devs, ents = dr.async_get(hass), er.async_get(hass)
    plug = devs.async_get_or_create(config_entry_id=other.entry_id, identifiers={("zha", "plug")}, name="Hallway plug")
    ents.async_get_or_create("switch", "zha", "plug", suggested_object_id="hallway_plug", device_id=plug.id)
    hass.states.async_set("switch.hallway_plug", "unavailable")
    entry = MockConfigEntry(domain=DOMAIN, title="Nova", unique_id=DOMAIN, data={"persona": "Nova", "speakers": []})
    entry.add_to_hass(hass)
    assert await hass.config_entries.async_setup(entry.entry_id)
    await hass.async_block_till_done()
    health = entry.runtime_data.health
    health._never.clear()
    health._checked["switch.hallway_plug"] = dt_util.utcnow()
    freezer.tick(timedelta(minutes=11))
    assert [i["name"] for i in health.async_check() if i["kind"] == "offline"] == ["Hallway plug"]
    seen = []
    hass.bus.async_listen("sentient_health_changed", lambda e: seen.append(e))
    freezer.tick(timedelta(hours=7))  # due a fresh look at its history
    health._checking = True  # that look is still running
    assert [i["name"] for i in health.async_check() if i["kind"] == "offline"] == ["Hallway plug"]
    await hass.async_block_till_done()
    assert not seen  # no "back to normal" then "offline" again


async def test_hub_offline_is_one_line(hass: HomeAssistant, freezer) -> None:
    other = MockConfigEntry(domain="frigate"); other.add_to_hass(hass)
    devs, ents = dr.async_get(hass), er.async_get(hass)
    hub = devs.async_get_or_create(config_entry_id=other.entry_id, identifiers={("frigate", "hub")}, name="Frigate")
    ents.async_get_or_create("sensor", "frigate", "fps", suggested_object_id="frigate_fps", device_id=hub.id)
    hass.states.async_set("sensor.frigate_fps", "unavailable")
    for n in ("Garden", "Gate", "Zone1"):
        dev = devs.async_get_or_create(config_entry_id=other.entry_id, identifiers={("frigate", n)}, name=n,
                                       via_device=("frigate", "hub"))
        ents.async_get_or_create("sensor", "frigate", n, suggested_object_id=f"{n.lower()}_count", device_id=dev.id)
        hass.states.async_set(f"sensor.{n.lower()}_count", "unavailable")
    entry = MockConfigEntry(domain=DOMAIN, title="Nova", unique_id=DOMAIN, data={"persona": "Nova", "speakers": []})
    entry.add_to_hass(hass)
    assert await hass.config_entries.async_setup(entry.entry_id)
    await hass.async_block_till_done()
    health = entry.runtime_data.health
    health._never.clear()
    for e in ("sensor.frigate_fps", "sensor.garden_count", "sensor.gate_count", "sensor.zone1_count"):
        health._checked[e] = dt_util.utcnow()
    freezer.tick(timedelta(minutes=11))
    health.async_check()
    assert health.describe() == ["Frigate is offline (and 3 of its devices)"]


async def test_why_devices_are_offline(hass: HomeAssistant, freezer) -> None:
    from homeassistant.config_entries import ConfigEntryState

    tuya = MockConfigEntry(domain="tuya_local", title="Tuya Local"); tuya.add_to_hass(hass)
    tuya.mock_state(hass, ConfigEntryState.SETUP_RETRY)
    devs, ents = dr.async_get(hass), er.async_get(hass)
    for n in ("Coffee", "Fountain"):
        d = devs.async_get_or_create(config_entry_id=tuya.entry_id, identifiers={("tuya", n)}, name=n)
        ents.async_get_or_create("switch", "tuya_local", n, suggested_object_id=n.lower(), device_id=d.id)
        hass.states.async_set(f"switch.{n.lower()}", "unavailable")
    entry = MockConfigEntry(domain=DOMAIN, title="Nova", unique_id=DOMAIN, data={"persona": "Nova", "speakers": []})
    entry.add_to_hass(hass)
    assert await hass.config_entries.async_setup(entry.entry_id)
    await hass.async_block_till_done()
    health = entry.runtime_data.health
    health._never.clear()
    for e in ("switch.coffee", "switch.fountain"):
        health._checked[e] = dt_util.utcnow()
    freezer.tick(timedelta(minutes=11))
    health.async_check()
    assert "Coffee is offline — its integration, Tuya Local, keeps retrying to connect" in health.describe()


async def _purifier(hass: HomeAssistant, fan_state: str, extra_state: str = "available"):
    other = MockConfigEntry(domain="dreo"); other.add_to_hass(hass)
    devs, ents = dr.async_get(hass), er.async_get(hass)
    dev = devs.async_get_or_create(config_entry_id=other.entry_id, identifiers={("dreo", "air")}, name="Boys Air")
    for domain, obj in (("fan", "boys_air"), ("sensor", "boys_air_quality"), ("sensor", "boys_air_mode")):
        ents.async_get_or_create(domain, "dreo", obj, suggested_object_id=obj, device_id=dev.id)
    ents.async_get_or_create(
        "binary_sensor", "dreo", "link", suggested_object_id="boys_air_connectivity", device_id=dev.id,
        entity_category=er.EntityCategory.DIAGNOSTIC,
    )
    hass.states.async_set("fan.boys_air", fan_state)
    hass.states.async_set("sensor.boys_air_quality", fan_state)
    hass.states.async_set("sensor.boys_air_mode", "cloud")  # a cloud mode sensor that keeps a value
    hass.states.async_set("binary_sensor.boys_air_connectivity", "on", {"device_class": "connectivity"})
    entry = MockConfigEntry(domain=DOMAIN, title="Nova", unique_id=DOMAIN, data={"persona": "Nova", "speakers": []})
    entry.add_to_hass(hass)
    assert await hass.config_entries.async_setup(entry.entry_id)
    await hass.async_block_till_done()
    health = entry.runtime_data.health
    health._never.clear()
    for e in ("fan.boys_air", "sensor.boys_air_quality", "sensor.boys_air_mode"):
        health._checked[e] = dt_util.utcnow()  # history already looked at
    return health


async def test_device_is_offline_when_its_main_entity_is_gone_even_if_a_cloud_sensor_keeps_a_value(
    hass: HomeAssistant, freezer
) -> None:
    health = await _purifier(hass, "unavailable")
    freezer.tick(timedelta(minutes=11))
    assert [i["name"] for i in health.async_check()] == ["Boys Air"]


async def test_working_main_entity_keeps_device_online(hass: HomeAssistant, freezer) -> None:
    health = await _purifier(hass, "on")
    hass.states.async_set("sensor.boys_air_quality", "unavailable")  # one flaky reading is not an outage
    freezer.tick(timedelta(minutes=11))
    assert health.async_check() == []


async def test_a_device_listed_twice_with_an_empty_copy_is_flagged_and_can_be_disabled(hass: HomeAssistant, hass_ws_client) -> None:
    from custom_components.sentient.health import duplicates

    local = MockConfigEntry(domain="tuya_local", title="Tuya Local")
    cloud = MockConfigEntry(domain="tuya", title="Tuya")
    local.add_to_hass(hass)
    cloud.add_to_hass(hass)
    devs, ents = dr.async_get(hass), er.async_get(hass)
    real = devs.async_get_or_create(config_entry_id=local.entry_id, identifiers={("tuya_local", "a")}, name="Bedroom waxmelt")
    empty = devs.async_get_or_create(config_entry_id=cloud.entry_id, identifiers={("tuya", "b")}, name="Bedroom waxmelt")
    ents.async_get_or_create("switch", "tuya_local", "a1", device_id=real.id, config_entry=local)
    found = duplicates(hass)
    assert [(f["device_id"], f["kind"]) for f in found] == [(empty.id, "duplicate")]
    assert "from Tuya is empty" in found[0]["text"]
    assert duplicates(hass, {empty.id}) == []

    entry = MockConfigEntry(domain=DOMAIN, title="Nova", unique_id=DOMAIN, data={"persona": "Nova"})
    entry.add_to_hass(hass)
    assert await hass.config_entries.async_setup(entry.entry_id)
    await hass.async_block_till_done()
    client = await hass_ws_client(hass)
    await client.send_json({"id": 1, "type": "sentient/health/disable_copy", "device_id": real.id})
    assert not (await client.receive_json())["success"]  # the real one is never disabled
    await client.send_json({"id": 2, "type": "sentient/health/disable_copy", "device_id": empty.id})
    msg = await client.receive_json()
    assert msg["success"] and msg["result"]["disabled"] == "Bedroom waxmelt"
    assert devs.async_get(empty.id).disabled_by is dr.DeviceEntryDisabler.USER
    assert duplicates(hass) == []
