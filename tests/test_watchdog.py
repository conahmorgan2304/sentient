"""House check, the voice action log, 'why didn't you say', wear, fights and frozen cameras."""

from datetime import timedelta
from unittest.mock import patch

from homeassistant.core import Context, HomeAssistant
from homeassistant.helpers import llm
from homeassistant.util import dt as dt_util
from pytest_homeassistant_custom_component.common import MockConfigEntry

from custom_components.sentient.const import DOMAIN, EVENT_HEALTH_CHANGED
from custom_components.sentient.watchdog import drain_rate

OPTIONS = {"persona": "Nova", "speakers": ["assist_satellite.kitchen"], "default_speaker": "assist_satellite.kitchen",
           "quiet_start": "00:00:00", "quiet_end": "00:00:00"}


async def _setup(hass: HomeAssistant, **extra) -> MockConfigEntry:
    entry = MockConfigEntry(domain=DOMAIN, title="Nova", unique_id=DOMAIN, data={**OPTIONS, **extra})
    entry.add_to_hass(hass)
    assert await hass.config_entries.async_setup(entry.entry_id)
    await hass.async_block_till_done()
    return entry


async def _api(hass):
    return await llm.async_get_api(hass, DOMAIN, llm.LLMContext(platform="test", context=None, language="en",
                                                                 assistant="conversation", device_id=None))


async def test_house_check_gathers_everything(hass: HomeAssistant) -> None:
    hass.states.async_set("binary_sensor.back_door", "on", {"device_class": "door", "friendly_name": "Back door"})
    hass.states.async_set("light.kitchen", "on", {"friendly_name": "Kitchen light"})
    hass.states.async_set("climate.hall", "heat", {"temperature": 20, "current_temperature": 18, "friendly_name": "Heating"})
    await _setup(hass)
    out = await (await _api(hass)).async_call_tool(llm.ToolInput(tool_name="sentient_house_check", tool_args={}))
    assert out["security"]["open"] == ["Back door"]
    assert out["lights_and_fans_on"] == ["Kitchen light"]
    assert out["thermostats"] == ["Heating: heat, set to 20°, now 18°"]
    assert out["house"] == "awake"


async def test_voice_actions_go_on_the_timeline(hass: HomeAssistant) -> None:
    entry = await _setup(hass)
    conv = entry.runtime_data.conversations
    conv._chat("c1", "content_added", {"content": {"role": "tool_result", "tool_name": "HassTurnOff", "tool_result": {
        "response_type": "action_done", "data": {"success": [{"name": "Kitchen light", "type": "entity", "id": "light.k"}],
                                                 "failed": [{"name": "Landing light", "type": "entity", "id": "light.l"}]}}}})
    conv._chat("c1", "content_added", {"content": {"role": "tool_result", "tool_name": "GetLiveContext", "tool_result": {"x": 1}}})
    conv._chat("c1", "content_added", {"content": {"role": "tool_result", "tool_name": "sentient_remind",
                                                   "tool_args": {"what": "bins out"}, "tool_result": {"ok": True}}})
    voice = [i for i in entry.runtime_data.timeline.items if i["kind"] == "voice"]
    assert [i["text"] for i in voice] == ["Remind: bins out", "Turned off Kitchen light"]
    assert voice[1]["detail"] == "by voice; didn't work: Landing light"


async def test_why_didnt_you_say(hass: HomeAssistant) -> None:
    entry = await _setup(hass)
    entry.runtime_data.noticer.decisions.appendleft({"time": "2026-10-27T14:02:00", "changes": ["Washing machine: running → off"],
                                                     "result": "skipped: quiet_hours"})
    out = await (await _api(hass)).async_call_tool(llm.ToolInput(
        tool_name="sentient_why", tool_args={"what": "why didn't you tell me the washing machine finished"}))
    assert out["noticed_but_not_said"] == ["14:02 noticed Washing machine: running → off — skipped: quiet_hours"]


async def test_appliance_wearing_out(hass: HomeAssistant) -> None:
    entry = await _setup(hass)
    hass.states.async_set("vacuum.robot", "docked", {"friendly_name": "Robot"})
    rt = entry.runtime_data
    rt.appliances.runs["vacuum.robot"] = [40, 41, 39, 42, 40, 58, 60, 61]
    now = dt_util.utcnow()
    rt.appliances.errors["vacuum.robot"] = [(now - timedelta(days=d)).isoformat() for d in (1, 2, 3)]
    texts = [i["text"] for i in rt.watchdog.issues()]
    assert "Robot is taking longer to finish than it used to (about 60 min, was 40)" in texts
    assert "Robot has got stuck or not made it back 3 times this week" in texts
    rt.health.async_check()
    assert any(i["kind"] == "wear" for i in rt.health.issues)


async def test_battery_draining_faster(hass: HomeAssistant) -> None:
    today = dt_util.now()
    day = lambda n: (today - timedelta(days=n)).date().isoformat()  # noqa: E731
    rows = [[day(n), 100 - (45 - n) * 0.5] for n in range(45, 14, -1)]  # 0.5%/day back then
    level = rows[-1][1]
    rows += [[day(n), level - (15 - n) * 2] for n in range(14, -1, -1)]  # 2%/day lately
    assert round(drain_rate(rows, 0, 14, today), 1) == 2.0 and round(drain_rate(rows, 15, 45, today), 1) == 0.5
    hass.states.async_set("sensor.door_battery", "60", {"device_class": "battery", "friendly_name": "Door battery"})
    entry = await _setup(hass)
    entry.runtime_data.watchdog.batteries["sensor.door_battery"] = rows
    assert any("Door battery is running down about 4× faster" in i["text"] for i in entry.runtime_data.watchdog.issues())


async def test_device_keeps_dropping_offline(hass: HomeAssistant) -> None:
    entry = await _setup(hass)
    dog = entry.runtime_data.watchdog
    dog._offline = set()
    for _ in range(4):
        hass.bus.async_fire(EVENT_HEALTH_CHANGED, {"problems": {"offline:Hall plug": "Hall plug is offline"}})
        await hass.async_block_till_done()
        hass.bus.async_fire(EVENT_HEALTH_CHANGED, {"problems": {}})
        await hass.async_block_till_done()
    assert any(i["text"] == "Hall plug has dropped offline 4 times this week" for i in dog.issues())


async def test_automations_fighting(hass: HomeAssistant) -> None:
    hass.states.async_set("light.hall", "off", {"friendly_name": "Hall light"})
    entry = await _setup(hass)
    for n in range(3):
        a, b = Context(), Context()
        hass.bus.async_fire("automation_triggered", {"name": "Hall on at dusk", "entity_id": "automation.a"}, context=a)
        hass.states.async_set("light.hall", "on", {"friendly_name": "Hall light"}, context=a)
        hass.bus.async_fire("automation_triggered", {"name": "Lights off when empty", "entity_id": "automation.b"}, context=b)
        hass.states.async_set("light.hall", "off", {"friendly_name": "Hall light"}, context=b)
        await hass.async_block_till_done()
    texts = [i["text"] for i in entry.runtime_data.watchdog.issues()]
    assert any("Hall on at dusk and Lights off when empty keep switching Hall light back and forth" in t for t in texts)


async def test_a_person_switching_is_not_a_fight(hass: HomeAssistant) -> None:
    hass.states.async_set("light.hall", "off")
    entry = await _setup(hass)
    for _ in range(4):
        a = Context()
        hass.bus.async_fire("automation_triggered", {"name": "Hall on"}, context=a)
        hass.states.async_set("light.hall", "on", context=a)
        hass.states.async_set("light.hall", "off", context=Context(user_id="someone"))
        await hass.async_block_till_done()
    assert not entry.runtime_data.watchdog.issues()


async def test_frozen_camera(hass: HomeAssistant) -> None:
    from homeassistant.components.camera import Image

    hass.states.async_set("camera.gate", "streaming", {"supported_features": 2, "friendly_name": "Gate"})
    hass.states.async_set("camera.gate_person", "idle", {"supported_features": 0})
    entry = await _setup(hass)
    dog = entry.runtime_data.watchdog
    with patch("homeassistant.components.camera.async_get_image", return_value=Image("image/jpeg", b"same")) as get:
        await dog.async_check_cameras()
        dog.cams["camera.gate"]["since"] = (dt_util.utcnow() - timedelta(hours=4)).isoformat()
        await dog.async_check_cameras()
    assert [c.args[1] for c in get.call_args_list] == ["camera.gate", "camera.gate"]  # snapshot cameras skipped
    assert any(i["text"].startswith("Gate's picture hasn't changed for 4 hours") for i in dog.issues())
    with patch("homeassistant.components.camera.async_get_image", return_value=Image("image/jpeg", b"new")):
        await dog.async_check_cameras()
    assert not any(i["kind"] == "frozen" for i in dog.issues())


async def test_guard_puts_back_what_the_voice_switched_by_mistake(hass: HomeAssistant) -> None:
    from homeassistant.helpers import area_registry as ar, entity_registry as er
    from pytest_homeassistant_custom_component.common import async_mock_service

    areas, ents = ar.async_get(hass), er.async_get(hass)
    garden, girls = areas.async_create("Garden"), areas.async_create("Girls room")
    for obj, area in (("shed_light", garden.id), ("projector", girls.id), ("landing", None)):
        ents.async_get_or_create("light", "test", obj, suggested_object_id=obj)
        if area:
            ents.async_update_entity(f"light.{obj}", area_id=area)
    hass.states.async_set("light.shed_light", "on", {"friendly_name": "Shed light"})
    hass.states.async_set("light.projector", "on", {"friendly_name": "girls projector Background"})
    hass.states.async_set("light.landing", "off", {"friendly_name": "Landing light"})
    entry = await _setup(hass)
    calls = async_mock_service(hass, "homeassistant", "turn_on")
    # the voice turned off everything
    for e in ("light.shed_light", "light.projector"):
        hass.states.async_set(e, "off", hass.states.get(e).attributes)
    await hass.async_block_till_done()
    conv = entry.runtime_data.conversations
    conv._chat("c9", "content_added", {"content": {"role": "user", "content": "turn off the shed light"}})
    targets = [{"name": n, "type": "entity", "id": e} for e, n in
               (("light.shed_light", "Shed light"), ("light.projector", "girls projector Background"), ("light.landing", "Landing light"))]
    conv._chat("c9", "content_added", {"content": {"role": "tool_result", "tool_name": "assist__intent__HassTurnOff",
                                                   "tool_result": {"data": {"success": targets, "failed": []}}}})
    await hass.async_block_till_done()
    assert [c.data["entity_id"] for c in calls] == ["light.projector"]  # the landing was already off; the shed was asked for
    texts = [i["text"] for i in entry.runtime_data.timeline.items if i["kind"] == "voice"]
    assert texts[0] == "Put back girls projector Background"
    assert texts[1].startswith("Turned off Shed light")


async def test_guard_leaves_whole_house_requests_alone(hass: HomeAssistant) -> None:
    entry = await _setup(hass)
    targets = [{"name": f"L{n}", "type": "entity", "id": f"light.l{n}"} for n in range(5)]
    assert entry.runtime_data.conversations.guard.mistakes("turn off all the lights", targets) == []
    assert entry.runtime_data.conversations.guard.mistakes("lights off please", targets) == []  # nothing named: leave it


async def test_drive_backs_off_when_nothing_changes(hass: HomeAssistant) -> None:
    entry = await _setup(hass)
    drive = entry.runtime_data.drive
    assert drive.cooldown() == timedelta(minutes=3)
    drive._same = 3
    assert drive.cooldown() == timedelta(minutes=24)
    drive._same = 10
    assert drive.cooldown() == timedelta(hours=1)


async def test_phone_batteries_are_not_house_problems(hass: HomeAssistant) -> None:
    from homeassistant.helpers import entity_registry as er

    er.async_get(hass).async_get_or_create("sensor", "mobile_app", "p1", suggested_object_id="phone_battery")
    hass.states.async_set("sensor.phone_battery", "9", {"device_class": "battery"})
    hass.states.async_set("sensor.door_battery", "5", {"device_class": "battery", "friendly_name": "Door battery"})
    entry = await _setup(hass)
    entry.runtime_data.health.async_check()
    assert [i["name"] for i in entry.runtime_data.health.issues if i["kind"] == "battery"] == ["Door battery"]


async def test_curiosity_never_asks_about_people(hass: HomeAssistant) -> None:
    entry = await _setup(hass)
    cur = entry.runtime_data.curiosity
    assert not cur.wonder("Could you tell me who the regular visitors were yesterday?")
    assert cur.wonder("What's the garage light for at 7pm?")


async def test_no_heating_offer_when_it_is_already_warming(hass: HomeAssistant) -> None:
    hass.states.async_set("sensor.hall_temp", "17")
    hass.states.async_set("climate.hall", "auto", {"temperature": 20, "current_temperature": 18.9})
    entry = await _setup(hass)
    offers = entry.runtime_data.offers
    offer = {"id": "o1", "name": "Heating on", "below_entity": "sensor.hall_temp", "below": 19,
             "actions": [{"action": "climate.set_temperature", "target": {"entity_id": "climate.hall"}, "data": {"temperature": 21}}]}
    assert not offers.ready(offer)
    hass.states.async_set("climate.hall", "auto", {"temperature": 18, "current_temperature": 18.9})
    assert offers.ready(offer)


async def test_no_heating_offer_when_already_set_that_high(hass: HomeAssistant) -> None:
    hass.states.async_set("sensor.hall_temp", "17")
    hass.states.async_set("climate.hall", "auto", {"temperature": 20, "current_temperature": 19.9})
    entry = await _setup(hass)
    offers = entry.runtime_data.offers
    offer = {"id": "o1", "name": "Heating on", "below_entity": "sensor.hall_temp", "below": 19,
             "actions": [{"action": "climate.set_temperature", "target": {"entity_id": "climate.hall"}, "data": {"temperature": 20}}]}
    assert not offers.ready(offer)
    hass.states.async_set("climate.hall", "auto", {"temperature": 18, "current_temperature": 19.9})
    assert offers.ready(offer)


async def test_a_tool_with_missing_arguments_does_not_crash_the_voice(hass: HomeAssistant) -> None:
    from homeassistant.helpers import llm

    from custom_components.sentient.const import DOMAIN

    await _setup(hass)
    api = await llm.async_get_api(hass, DOMAIN, llm.LLMContext(platform="t", context=None, language="en", assistant=None, device_id=None))
    out = await api.async_call_tool(llm.ToolInput(tool_name="sentient_look", tool_args={"camera": "garden"}))
    assert "wrong arguments" in out["error"]


async def test_announce_to_several_rooms_or_everywhere(hass: HomeAssistant) -> None:
    from homeassistant.helpers import area_registry as ar, entity_registry as er
    from pytest_homeassistant_custom_component.common import async_mock_service

    areas = ar.async_get(hass)
    ents = er.async_get(hass)
    for oid, room in (("kitchen", "Kitchen"), ("landing", "Upstairs"), ("lounge", "Living room")):
        ents.async_get_or_create("assist_satellite", "test", oid, suggested_object_id=oid)
        ents.async_update_entity(f"assist_satellite.{oid}", area_id=areas.async_create(room).id)
        hass.states.async_set(f"assist_satellite.{oid}", "idle", {"supported_features": 3})
    await _setup(hass, speakers=["assist_satellite.kitchen", "assist_satellite.landing", "assist_satellite.lounge"],
                 quiet_start="23:59:00", quiet_end="00:00:00")
    said = async_mock_service(hass, "assist_satellite", "announce")
    api = await llm.async_get_api(hass, DOMAIN, llm.LLMContext(platform="t", context=None, language="en", assistant=None, device_id=None))
    out = await api.async_call_tool(llm.ToolInput(tool_name="sentient_announce", tool_args={
        "message": "Dinner's ready.", "speaker": "kitchen and upstairs"}))
    assert out["spoken"] and sorted(said[-1].data["entity_id"]) == ["assist_satellite.kitchen", "assist_satellite.landing"]
    out = await api.async_call_tool(llm.ToolInput(tool_name="sentient_announce", tool_args={
        "message": "Film's starting.", "speaker": "everywhere"}))
    assert out["spoken"] and len(said[-1].data["entity_id"]) == 3
