"""House health: offline devices, low batteries, updates."""

from datetime import timedelta

from homeassistant.core import HomeAssistant
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
    assert [i["kind"] for i in health.async_check()] == ["battery"]  # tablet not offline long enough yet
    freezer.tick(timedelta(minutes=11))
    issues = health.async_check()
    assert [i["name"] for i in issues] == ["Hallway tablet", "Back door battery+"]
    assert health.describe() == ["Hallway tablet is offline", "Back door battery+ is at 14%"]
    await hass.async_block_till_done()
    st = hass.states.get("sensor.nova_house_health")
    assert st.state == "2" and st.attributes["updates"] == ["Frigate update"]
    hass.config_entries.async_update_entry(entry, options={"health_ignore": ["sensor.tab_motion"]})
    await hass.async_block_till_done()
    assert health.describe() == ["Back door battery+ is at 14%"]
