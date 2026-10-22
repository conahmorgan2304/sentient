"""Delivery (volume, ducking), shush, and safety alerts."""

from datetime import timedelta

from homeassistant.core import HomeAssistant
from homeassistant.helpers import device_registry as dr
from homeassistant.helpers import entity_registry as er
from homeassistant.helpers import llm
from pytest_homeassistant_custom_component.common import MockConfigEntry, async_fire_time_changed, async_mock_service

from custom_components.sentient.const import DOMAIN

OPTIONS = {"persona": "Nova", "speakers": ["assist_satellite.kitchen", "assist_satellite.landing"],
           "default_speaker": "assist_satellite.kitchen", "quiet_start": "00:00:00", "quiet_end": "00:00:00",
           "notify_targets": ["notify.phone"]}


async def _setup(hass, options=None):
    entry = MockConfigEntry(domain=DOMAIN, title="Nova", unique_id=DOMAIN, data={**OPTIONS, **(options or {})})
    entry.add_to_hass(hass)
    assert await hass.config_entries.async_setup(entry.entry_id)
    await hass.async_block_till_done()
    for s in OPTIONS["speakers"]:
        hass.states.async_set(s, "idle")
    hass.states.async_set("notify.phone", "unknown")
    return entry


async def test_volume_and_ducking(hass: HomeAssistant) -> None:
    other = MockConfigEntry(domain="esphome"); other.add_to_hass(hass)
    devs, ents, areas = dr.async_get(hass), er.async_get(hass), None
    from homeassistant.helpers import area_registry as ar
    kitchen = ar.async_get(hass).async_create("Kitchen")
    dev = devs.async_get_or_create(config_entry_id=other.entry_id, identifiers={("esphome", "k")})
    devs.async_update_device(dev.id, area_id=kitchen.id)
    ents.async_get_or_create("assist_satellite", "esphome", "sat", suggested_object_id="kitchen", device_id=dev.id)
    ents.async_get_or_create("number", "esphome", "vol", suggested_object_id="kitchen_volume", device_id=dev.id)
    radio = ents.async_get_or_create("media_player", "cast", "radio", suggested_object_id="kitchen_radio")
    ents.async_update_entity(radio.entity_id, area_id=kitchen.id)
    entry = await _setup(hass, {"speak_volume": 70})
    hass.states.async_set("number.kitchen_volume", "40", {"max": 100})
    hass.states.async_set("media_player.kitchen_radio", "playing", {"volume_level": 0.6})
    numbers = async_mock_service(hass, "number", "set_value")
    vols = async_mock_service(hass, "media_player", "volume_set")
    said = async_mock_service(hass, "assist_satellite", "announce")
    await hass.services.async_call(DOMAIN, "say", {"message": "Tea's ready", "speaker": "assist_satellite.kitchen"}, blocking=True)
    assert said
    assert [c.data["value"] for c in numbers] == [70, 40]
    assert [c.data["volume_level"] for c in vols] == [0.15, 0.6]


async def test_shush_words_and_entities(hass: HomeAssistant) -> None:
    entry = await _setup(hass)
    hass.states.async_set("sensor.washer", "idle", {"friendly_name": "Washing machine"})
    said = async_mock_service(hass, "assist_satellite", "announce")
    api = await llm.async_get_api(hass, "sentient", llm.LLMContext(platform="t", context=None, language="en", assistant=None, device_id=None))
    res = await api.async_call_tool(llm.ToolInput(tool_name="sentient_shush", tool_args={"what": "washing machine", "hours": 2}))
    data = getattr(res, "data", res)
    assert data["shushed"] == "Washing machine"
    assert entry.runtime_data.mutes.entity_muted("sensor.washer")
    r = await hass.services.async_call(DOMAIN, "say", {"message": "The washing machine has finished."}, blocking=True, return_response=True)
    assert r["reason"].startswith("muted") and not said
    r = await hass.services.async_call(DOMAIN, "say", {"message": "The washing machine is on fire!", "priority": "urgent"}, blocking=True, return_response=True)
    assert r["spoken"]
    await api.async_call_tool(llm.ToolInput(tool_name="sentient_shush", tool_args={"undo": True}))
    assert entry.runtime_data.mutes.listing() == []


async def test_smoke_alert_everywhere_and_repeats(hass: HomeAssistant, freezer) -> None:
    entry = await _setup(hass, {"quiet_start": "00:00:00", "quiet_end": "23:59:00"})
    said = async_mock_service(hass, "assist_satellite", "announce")
    sent = async_mock_service(hass, "notify", "send_message")
    hass.states.async_set("binary_sensor.kitchen_smoke", "off", {"device_class": "smoke", "friendly_name": "Kitchen smoke"})
    await hass.async_block_till_done()
    hass.states.async_set("binary_sensor.kitchen_smoke", "on", {"device_class": "smoke", "friendly_name": "Kitchen smoke"})
    await hass.async_block_till_done()
    assert said and set(said[0].data["entity_id"]) == set(OPTIONS["speakers"])
    assert said[0].data["message"] == "Smoke detected: Kitchen smoke!"
    assert sent and sent[0].data["title"] == "Nova · ALERT"
    freezer.tick(timedelta(seconds=125))
    async_fire_time_changed(hass)
    await hass.async_block_till_done()
    assert len(said) == 2
    hass.states.async_set("binary_sensor.kitchen_smoke", "off", {"device_class": "smoke", "friendly_name": "Kitchen smoke"})
    await hass.async_block_till_done()
    freezer.tick(timedelta(seconds=125))
    async_fire_time_changed(hass)
    await hass.async_block_till_done()
    assert len(said) == 2
    assert entry.runtime_data.timeline.items[0]["text"] == "Kitchen smoke has cleared"
