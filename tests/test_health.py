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
    issues = health.async_check()
    assert [i["name"] for i in issues if i["kind"] == "offline"] == ["Hallway plug"]
