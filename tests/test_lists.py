"""Offers, announcements, alarms and camera checks."""

from datetime import datetime, timedelta

from homeassistant.core import HomeAssistant, ServiceCall, SupportsResponse
from homeassistant.helpers import area_registry as ar, entity_registry as er, llm
from homeassistant.util import dt as dt_util
from pytest_homeassistant_custom_component.common import (
    MockConfigEntry,
    async_fire_time_changed,
    async_mock_service,
)

from custom_components.sentient.const import DOMAIN


def _t(day: int, h: int, m: int) -> datetime:
    return datetime(2026, 10, day, h, m, tzinfo=dt_util.get_default_time_zone())


async def _setup(hass: HomeAssistant, **extra) -> MockConfigEntry:
    kitchen = ar.async_get(hass).async_create("Kitchen")
    ents = er.async_get(hass)
    ents.async_get_or_create("binary_sensor", "test", "km", suggested_object_id="kitchen_motion")
    ents.async_update_entity("binary_sensor.kitchen_motion", area_id=kitchen.id)
    ents.async_get_or_create("assist_satellite", "test", "ks", suggested_object_id="kitchen")
    ents.async_update_entity("assist_satellite.kitchen", area_id=kitchen.id)
    hass.states.async_set("binary_sensor.kitchen_motion", "off", {"device_class": "motion"})
    hass.states.async_set("assist_satellite.kitchen", "idle", {"supported_features": 3})
    entry = MockConfigEntry(domain=DOMAIN, title="Nova", unique_id=DOMAIN, data={
        "persona": "Nova", "speakers": ["assist_satellite.kitchen"], "default_speaker": "assist_satellite.kitchen",
        "quiet_start": "21:00:00", "quiet_end": "07:00:00", **extra,
    })
    entry.add_to_hass(hass)
    assert await hass.config_entries.async_setup(entry.entry_id)
    await hass.async_block_till_done()
    entry.kitchen = kitchen.id
    return entry


async def test_brew_offer_asks_in_the_kitchen_and_yes_puts_the_kettle_on(hass: HomeAssistant, freezer) -> None:
    freezer.move_to(dt_util.as_utc(_t(21, 9, 0)))
    hass.states.async_set("switch.kettle", "off")
    entry = await _setup(hass)
    freezer.tick(timedelta(hours=1))  # kettle idle for an hour
    rt = entry.runtime_data
    offer = rt.lists["offers"].upsert({
        "name": "Brew", "areas": [entry.kitchen], "times": "08:00-11:00", "questions": "Fancy a brew?",
        "idle_entity": "switch.kettle", "idle_minutes": 45,
        "actions": [{"action": "switch.turn_on", "target": {"entity_id": "switch.kettle"}}],
    })
    asked = async_mock_service(hass, "assist_satellite", "start_conversation")
    on = async_mock_service(hass, "switch", "turn_on")
    hass.states.async_set("binary_sensor.kitchen_motion", "on", {"device_class": "motion"})
    await hass.async_block_till_done()
    assert len(asked) == 1 and asked[0].data["start_message"] == "Fancy a brew?"
    assert offer["id"] in asked[0].data["extra_system_prompt"]
    # cooldown: walking in again doesn't ask again
    hass.states.async_set("binary_sensor.kitchen_motion", "off", {"device_class": "motion"})
    hass.states.async_set("binary_sensor.kitchen_motion", "on", {"device_class": "motion"})
    await hass.async_block_till_done()
    assert len(asked) == 1
    ctx = llm.LLMContext(platform="test", context=None, language="en", assistant="conversation", device_id=None)
    api = await llm.async_get_api(hass, "sentient", ctx)
    out = await api.async_call_tool(llm.ToolInput(tool_name="sentient_accept_offer", tool_args={"offer": "Brew"}))
    await hass.async_block_till_done()
    assert out["done"] is True and on
    out = await api.async_call_tool(llm.ToolInput(tool_name="sentient_accept_offer", tool_args={"offer": "Brew", "accept": False}))
    assert out["done"] is False and rt.feedback.score(f"offer:{offer['id']}", "offers") > 0


async def test_gate_announcement_through_quiet_hours_but_not_after_the_back_door(hass: HomeAssistant, freezer) -> None:
    freezer.move_to(dt_util.as_utc(_t(21, 23, 0)))
    hass.states.async_set("binary_sensor.gate", "off")
    hass.states.async_set("binary_sensor.back_door", "off")
    entry = await _setup(hass)
    entry.runtime_data.lists["announce"].upsert({
        "name": "Gate", "entity": "binary_sensor.gate", "messages": "The back gate has opened.",
        "through_quiet": True, "skip_entity": "binary_sensor.back_door", "skip_seconds": 30,
    })
    said = async_mock_service(hass, "assist_satellite", "announce")
    freezer.tick(timedelta(minutes=5))
    hass.states.async_set("binary_sensor.gate", "on")
    await hass.async_block_till_done()
    assert [c.data["message"] for c in said] == ["The back gate has opened."]
    hass.states.async_set("binary_sensor.gate", "off")
    freezer.tick(timedelta(minutes=5))
    hass.states.async_set("binary_sensor.back_door", "on")  # someone going out
    freezer.tick(timedelta(seconds=10))
    hass.states.async_set("binary_sensor.gate", "on")
    await hass.async_block_till_done()
    assert len(said) == 1


async def test_alarm_rings_runs_actions_then_room_actions(hass: HomeAssistant, freezer) -> None:
    freezer.move_to(dt_util.as_utc(_t(21, 5, 40)))  # a Wednesday, in quiet hours
    entry = await _setup(hass)
    rt = entry.runtime_data
    rt.lists["alarms"].upsert({
        "name": "Work", "time": "05:45:00", "days": ["mon", "tue", "wed", "thu", "fri"],
        "actions": [{"action": "light.turn_on", "target": {"entity_id": "light.downstairs"}}],
        "room_area": entry.kitchen, "room_actions": [{"action": "switch.turn_on", "target": {"entity_id": "switch.kettle"}}],
    })
    said = async_mock_service(hass, "assist_satellite", "announce")
    lights = async_mock_service(hass, "light", "turn_on")
    kettle = async_mock_service(hass, "switch", "turn_on")
    when = dt_util.as_utc(_t(21, 5, 45))
    freezer.move_to(when)
    async_fire_time_changed(hass, when)
    await hass.async_block_till_done()
    assert said and said[0].data["message"].startswith("It's 5:45. Work.")
    assert lights and not kettle
    hass.states.async_set("binary_sensor.kitchen_motion", "on", {"device_class": "motion"})
    await hass.async_block_till_done()
    assert kettle
    assert rt.lists["alarms"].find("Work")["enabled"] is True  # repeating: stays on


async def test_alarm_by_voice(hass: HomeAssistant, freezer) -> None:
    freezer.move_to(dt_util.as_utc(_t(21, 12, 0)))
    entry = await _setup(hass)
    ctx = llm.LLMContext(platform="test", context=None, language="en", assistant="conversation", device_id=None)
    api = await llm.async_get_api(hass, "sentient", ctx)
    out = await api.async_call_tool(llm.ToolInput(tool_name="sentient_alarm", tool_args={
        "action": "set", "name": "Doctor's", "time": "09:30", "date": "2026-10-23", "description": "at the surgery"}))
    assert out["set"] == "Doctor's" and out["next"].startswith("2026-10-23T09:30")
    out = await api.async_call_tool(llm.ToolInput(tool_name="sentient_alarm", tool_args={
        "action": "set", "name": "Weekday", "time": "05:45", "days": "weekdays"}))
    assert out["days"] == ["mon", "tue", "wed", "thu", "fri"]
    out = await api.async_call_tool(llm.ToolInput(tool_name="sentient_alarm", tool_args={"action": "cancel", "name": "doctor"}))
    assert out["cancelled"] == "Doctor's"


async def test_camera_check_delivery_van(hass: HomeAssistant) -> None:
    entry = await _setup(hass)
    hass.states.async_set("camera.back", "idle", {"friendly_name": "Back"})
    hass.states.async_set("binary_sensor.back_car", "off")
    entry.runtime_data.lists["checks"].upsert({
        "name": "Van on the drive", "camera": "camera.back", "triggers": ["binary_sensor.back_car"],
        "question": "Is there a delivery van?",
    })

    async def generate(call: ServiceCall):
        return {"conversation_id": "x", "data": {"match": True, "description": "A white van parked on the drive."}}

    hass.services.async_register("ai_task", "generate_data", generate, supports_response=SupportsResponse.ONLY)
    hass.states.async_set("binary_sensor.back_car", "on")
    await hass.async_block_till_done()
    items = list(entry.runtime_data.timeline.items)
    assert items[0]["text"] == "Van on the drive: A white van parked on the drive"


async def test_bin_alarm_says_tomorrows_bin_or_stays_quiet(hass: HomeAssistant, freezer) -> None:
    from homeassistant.core import SupportsResponse

    freezer.move_to(dt_util.as_utc(_t(22, 18, 0)))  # Thursday
    entry = await _setup(hass)
    entry.runtime_data.lists["alarms"].upsert({
        "name": "Bins", "time": "19:00:00", "days": ["thu"], "message": "Bins tonight: {tomorrow:calendar.bins}.",
    })
    events = {"calendar.bins": {"events": [{"summary": "Recycling"}]}}

    async def get_events(call):
        return events

    hass.services.async_register("calendar", "get_events", get_events, supports_response=SupportsResponse.ONLY)
    said = async_mock_service(hass, "assist_satellite", "announce")
    when = dt_util.as_utc(_t(22, 19, 0))
    freezer.move_to(when)
    async_fire_time_changed(hass, when)
    await hass.async_block_till_done()
    assert [c.data["message"] for c in said] == ["Bins tonight: Recycling."]
    events["calendar.bins"]["events"] = []
    when = dt_util.as_utc(_t(29, 19, 0))
    freezer.move_to(when)
    async_fire_time_changed(hass, when)
    await hass.async_block_till_done()
    assert len(said) == 1


async def test_announcement_on_an_event(hass: HomeAssistant, freezer) -> None:
    freezer.move_to(dt_util.as_utc(_t(21, 23, 30)))
    entry = await _setup(hass)
    entry.runtime_data.lists["announce"].upsert({
        "name": "Doorbell", "event_type": "doorbell_pressed", "messages": "Someone is at the front door.",
        "through_quiet": True,
    })
    said = async_mock_service(hass, "assist_satellite", "announce")
    hass.bus.async_fire("doorbell_pressed", {"doorbell": "front_door"})
    await hass.async_block_till_done()
    assert [c.data["message"] for c in said] == ["Someone is at the front door."]
