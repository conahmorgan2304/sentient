"""Rooms at a glance: each area's temperature, lights, openings, movement and media."""

from homeassistant.core import HomeAssistant
from homeassistant.helpers import area_registry as ar, entity_registry as er
from pytest_homeassistant_custom_component.common import MockConfigEntry

from custom_components.sentient.const import DOMAIN
from custom_components.sentient.glance import rooms, summary


def _put(hass, eid, state, attrs, area):
    reg = er.async_get(hass)
    domain, obj = eid.split(".")
    reg.async_get_or_create(domain, "test", obj, suggested_object_id=obj)
    reg.async_update_entity(eid, area_id=area)
    hass.states.async_set(eid, state, attrs)


async def _house(hass):
    areas = ar.async_get(hass)
    lounge = areas.async_create("Lounge").id
    kitchen = areas.async_create("Kitchen").id
    areas.async_create("Loft")  # nothing in it: not shown
    _put(hass, "sensor.lounge_temp", "20.5", {"device_class": "temperature"}, lounge)
    _put(hass, "sensor.lounge_hum", "48", {"device_class": "humidity"}, lounge)
    _put(hass, "light.lamp", "on", {"friendly_name": "Lamp"}, lounge)
    _put(hass, "light.big", "off", {"friendly_name": "Big light"}, lounge)
    _put(hass, "binary_sensor.lounge_window", "on", {"device_class": "window", "friendly_name": "Lounge window"}, lounge)
    _put(hass, "binary_sensor.lounge_pir", "on", {"device_class": "motion"}, lounge)
    _put(hass, "media_player.tv", "playing", {"friendly_name": "TV", "media_title": "News"}, lounge)
    _put(hass, "sensor.kitchen_temp", "17", {"device_class": "temperature"}, kitchen)
    _put(hass, "binary_sensor.kitchen_pir", "off", {"device_class": "motion"}, kitchen)
    _put(hass, "sensor.kitchen_dead", "unavailable", {"device_class": "temperature"}, kitchen)
    return lounge, kitchen


async def test_rooms_and_summary(hass: HomeAssistant):
    lounge, kitchen = await _house(hass)
    rows = {r["name"]: r for r in rooms(hass, likely=lounge)}
    assert set(rows) == {"Lounge", "Kitchen"}
    lo = rows["Lounge"]
    assert lo["temp"] == 20.5 and lo["humidity"] == 48
    assert lo["lights"] == 2 and lo["lights_on"] == ["Lamp"]
    assert lo["open"] == ["Lounge window"] and lo["moving"] and lo["likely"]
    assert lo["playing"] == ["News (TV)"]
    ki = rows["Kitchen"]
    assert ki["temp"] == 17 and not ki["moving"] and ki["moved_mins"] == 0 and not ki["likely"]
    s = summary(list(rows.values()))
    assert s["warmest"] == {"room": "Lounge", "temp": 20.5} and s["coolest"]["room"] == "Kitchen"
    assert s["lights_on"] == [{"room": "Lounge", "area_id": lounge, "count": 1}]
    assert s["open"] == [{"room": "Lounge", "what": "Lounge window"}]


async def test_a_sensor_that_never_rests_isnt_movement(hass: HomeAssistant):
    lounge, _ = await _house(hass)
    lo = next(r for r in rooms(hass, restless={"binary_sensor.lounge_pir"}) if r["area_id"] == lounge)
    assert not lo["moving"]


async def test_through_the_page_and_switched_off(hass: HomeAssistant, hass_ws_client, hass_admin_user):
    await _house(hass)
    entry = MockConfigEntry(domain=DOMAIN, title="Nova", unique_id=DOMAIN, data={"persona": "Nova"})
    entry.add_to_hass(hass)
    assert await hass.config_entries.async_setup(entry.entry_id)
    await hass.async_block_till_done(wait_background_tasks=True)
    ws = await hass_ws_client(hass)
    await ws.send_json({"id": 1, "type": "sentient/glance"})
    out = (await ws.receive_json())["result"]
    assert out["on"] and {r["name"] for r in out["rooms"]} >= {"Lounge", "Kitchen"}
    entry.runtime_data.options = {**entry.runtime_data.options, "rooms_view": False}
    await ws.send_json({"id": 2, "type": "sentient/glance"})
    assert (await ws.receive_json())["result"] == {"on": False, "rooms": [], "summary": None}


def test_on_share_spots_a_sensor_that_never_rests():
    from datetime import timedelta

    from homeassistant.core import State
    from homeassistant.util import dt as dt_util

    from custom_components.sentient.heating import on_share

    t = dt_util.utcnow() - timedelta(hours=10)

    def at(h, s):
        st = State("binary_sensor.x", s)
        st.last_changed = t + timedelta(hours=h)
        return st
    end = t + timedelta(hours=10)
    assert on_share([at(0, "on"), at(9.5, "off"), at(9.6, "on")], end) > 0.9
    assert on_share([at(0, "off"), at(2, "on"), at(3, "off")], end) == 0.1
    assert on_share([], end) == 0.0


async def test_what_it_knows_on_the_mind_tab(hass: HomeAssistant, hass_ws_client, hass_admin_user):
    entry = MockConfigEntry(domain=DOMAIN, title="Nova", unique_id=DOMAIN, data={"persona": "Nova"})
    entry.add_to_hass(hass)
    assert await hass.config_entries.async_setup(entry.entry_id)
    await hass.async_block_till_done(wait_background_tasks=True)
    ws = await hass_ws_client(hass)
    await ws.send_json({"id": 1, "type": "sentient/inner"})
    knows = (await ws.receive_json())["result"]["knows"]
    assert any("bed" in k["text"] for k in knows)  # still learning when the house sleeps
    entry.runtime_data.options = {**entry.runtime_data.options, "knowledge_card": False}
    await ws.send_json({"id": 2, "type": "sentient/inner"})
    assert "knows" not in (await ws.receive_json())["result"]


async def test_a_tablet_screen_isnt_a_light_left_on(hass: HomeAssistant):
    areas = ar.async_get(hass)
    hall = areas.async_create("Hall").id
    reg = er.async_get(hass)
    reg.async_get_or_create("light", "browser_mod", "screen1", suggested_object_id="hall_screen")
    reg.async_update_entity("light.hall_screen", area_id=hall)
    hass.states.async_set("light.hall_screen", "on")
    _put(hass, "light.hall", "on", {"friendly_name": "Hall light"}, hall)
    row = next(r for r in rooms(hass) if r["area_id"] == hall)
    assert row["lights"] == 1 and row["lights_on"] == ["Hall light"]
