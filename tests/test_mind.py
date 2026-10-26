"""Room-aware volume, messages that follow people, follow-ups, conversations, curiosity,
overnight reflection and the drive."""

from datetime import timedelta

from homeassistant.core import HomeAssistant, ServiceCall, SupportsResponse
from homeassistant.helpers import area_registry as ar, device_registry as dr, entity_registry as er, llm
from homeassistant.util import dt as dt_util
from pytest_homeassistant_custom_component.common import MockConfigEntry, async_fire_time_changed, async_mock_service

from custom_components.sentient import delivery
from custom_components.sentient.const import DOMAIN

OPTIONS = {"persona": "Nova", "speakers": ["assist_satellite.kitchen"], "default_speaker": "assist_satellite.kitchen",
           "quiet_start": "00:00:00", "quiet_end": "00:00:00"}


async def _setup(hass: HomeAssistant, **extra) -> MockConfigEntry:
    entry = MockConfigEntry(domain=DOMAIN, title="Nova", unique_id=DOMAIN, data={**OPTIONS, **extra})
    entry.add_to_hass(hass)
    assert await hass.config_entries.async_setup(entry.entry_id)
    await hass.async_block_till_done()
    hass.states.async_set("assist_satellite.kitchen", "idle", {"supported_features": 3})
    return entry


def _kitchen(hass: HomeAssistant):
    other = MockConfigEntry(domain="esphome")
    other.add_to_hass(hass)
    kitchen = ar.async_get(hass).async_create("Kitchen")
    dev = dr.async_get(hass).async_get_or_create(config_entry_id=other.entry_id, identifiers={("esphome", "k")})
    dr.async_get(hass).async_update_device(dev.id, area_id=kitchen.id)
    ents = er.async_get(hass)
    ents.async_get_or_create("assist_satellite", "esphome", "sat", suggested_object_id="kitchen", device_id=dev.id)
    return kitchen, dev


def _ai(hass: HomeAssistant, reply):
    calls: list[ServiceCall] = []

    async def generate(call: ServiceCall):
        calls.append(call)
        return {"conversation_id": "x", "data": reply(call) if callable(reply) else reply}

    hass.services.async_register("ai_task", "generate_data", generate, supports_response=SupportsResponse.ONLY)
    return calls


async def test_speaks_up_in_a_noisy_room(hass: HomeAssistant, monkeypatch) -> None:
    monkeypatch.setattr(delivery, "LISTEN_SECONDS", 0.01)
    _, dev = _kitchen(hass)
    ents = er.async_get(hass)
    ents.async_get_or_create("number", "esphome", "vol", suggested_object_id="kitchen_volume", device_id=dev.id)
    ents.async_get_or_create("sensor", "esphome", "loud", suggested_object_id="kitchen_room_loudness", device_id=dev.id)
    ents.async_get_or_create("switch", "esphome", "rep", suggested_object_id="kitchen_report_room_loudness", device_id=dev.id)
    await _setup(hass, speak_volume=50)
    hass.states.async_set("number.kitchen_volume", "40", {"max": 100})
    hass.states.async_set("switch.kitchen_report_room_loudness", "off")
    room = {"db": -45.0}

    async def report(call: ServiceCall):
        hass.states.async_set("sensor.kitchen_room_loudness", str(room["db"]))  # the mic reports while on

    hass.services.async_register("switch", "turn_on", report)
    off = async_mock_service(hass, "switch", "turn_off")
    numbers = async_mock_service(hass, "number", "set_value")
    said = async_mock_service(hass, "assist_satellite", "announce")
    # first listen: just learns how the room usually sounds
    await hass.services.async_call(DOMAIN, "say", {"message": "Tea's ready", "speaker": "assist_satellite.kitchen"}, blocking=True)
    assert said and numbers[0].data["value"] == 50
    assert off  # stops reporting again straight after
    numbers.clear()
    room["db"] = -25.0  # TV on: 20 dB louder than usual
    await hass.services.async_call(DOMAIN, "say", {"message": "Dinner's ready", "speaker": "assist_satellite.kitchen"}, blocking=True)
    assert numbers[0].data["value"] == 80  # 50% + 20 dB × 1.5%
    assert numbers[-1].data["value"] == 40  # put back afterwards


async def test_message_follows_someone_into_a_room(hass: HomeAssistant, freezer) -> None:
    kitchen, _ = _kitchen(hass)
    ents = er.async_get(hass)
    ents.async_get_or_create("binary_sensor", "test", "pir", suggested_object_id="kitchen_motion")
    ents.async_update_entity("binary_sensor.kitchen_motion", area_id=kitchen.id)
    entry = await _setup(hass)
    hass.states.async_set("binary_sensor.kitchen_motion", "off", {"device_class": "motion"})
    said = async_mock_service(hass, "assist_satellite", "announce")
    await hass.services.async_call(DOMAIN, "say", {"message": "The parcel has arrived."}, blocking=True)
    assert said and entry.runtime_data.followyou.queue  # nobody about: kept for later
    said.clear()
    freezer.tick(timedelta(minutes=5))
    hass.states.async_set("binary_sensor.kitchen_motion", "on", {"device_class": "motion"})
    await hass.async_block_till_done()
    freezer.tick(timedelta(seconds=9))
    async_fire_time_changed(hass)
    await hass.async_block_till_done()
    assert said[-1].data["message"] == "Earlier: The parcel has arrived."
    assert not entry.runtime_data.followyou.queue


async def test_follow_up_checks_back(hass: HomeAssistant, freezer) -> None:
    entry = await _setup(hass)
    hass.states.async_set("sensor.lounge_temp", "18.5", {"friendly_name": "Lounge", "unit_of_measurement": "°C"})
    said = async_mock_service(hass, "assist_satellite", "announce")
    api = await llm.async_get_api(hass, DOMAIN, llm.LLMContext(platform="t", context=None, language="en", assistant=None, device_id=None))
    out = await api.async_call_tool(llm.ToolInput(tool_name="sentient_follow_up", tool_args={
        "minutes": 60, "about": "the lounge warming up", "entity": "sensor.lounge_temp", "above": 20,
        "if_done": "The lounge is nice and warm now.",
    }))
    assert out["follow_up"]["about"] == "the lounge warming up"
    api = await llm.async_get_api(hass, DOMAIN, llm.LLMContext(platform="t", context=None, language="en", assistant=None, device_id=None))
    assert "Follow-ups you've set" in api.api_prompt
    freezer.tick(timedelta(minutes=61))
    async_fire_time_changed(hass)
    await hass.async_block_till_done()
    assert said[-1].data["message"] == "The lounge warming up: not yet — Lounge is 18.5 °C."
    assert any(i["kind"] == "loop" for i in entry.runtime_data.timeline.items)


async def test_watch_that_runs_out_is_closed_off(hass: HomeAssistant, freezer) -> None:
    await _setup(hass)
    hass.states.async_set("binary_sensor.letterbox", "off", {"friendly_name": "Letterbox"})
    said = async_mock_service(hass, "assist_satellite", "announce")
    await hass.services.async_call(DOMAIN, "watch", {"what": "the post", "entity": "binary_sensor.letterbox",
                                                     "to_state": "on", "minutes": 30}, blocking=True)
    freezer.tick(timedelta(minutes=32))
    async_fire_time_changed(hass)
    await hass.async_block_till_done()
    assert said[-1].data["message"] == "No sign of the post — I've stopped keeping an eye out."


async def test_conversations_are_remembered_across_speakers(hass: HomeAssistant, freezer) -> None:
    from homeassistant.components.conversation.chat_log import _async_notify_subscribers
    from homeassistant.components.conversation.const import ChatLogEventType

    kitchen, dev = _kitchen(hass)
    entry = await _setup(hass)
    convs = entry.runtime_data.conversations
    _async_notify_subscribers(hass, "abc", ChatLogEventType.CONTENT_ADDED,
                              {"content": {"role": "user", "content": "What time is the bin collection on Thursday?"}})
    convs.attach("abc", dev.id, "conversation")
    _async_notify_subscribers(hass, "abc", ChatLogEventType.CONTENT_ADDED,
                              {"content": {"role": "assistant", "content": "It's around 7am."}})
    # a background AI job never asked for Sentient: not kept
    _async_notify_subscribers(hass, "job", ChatLogEventType.CONTENT_ADDED, {"content": {"role": "user", "content": "Describe this image"}})
    freezer.tick(timedelta(minutes=4))
    async_fire_time_changed(hass)
    await hass.async_block_till_done()
    assert len(convs.episodes) == 1 and convs.episodes[0]["room"] == "Kitchen"
    api = await llm.async_get_api(hass, DOMAIN, llm.LLMContext(platform="t", context=None, language="en", assistant=None, device_id=None))
    assert 'Kitchen: asked "What time is the bin collection on Thursday?"' in api.api_prompt
    out = await api.async_call_tool(llm.ToolInput(tool_name="sentient_conversations", tool_args={"query": "bins collection"}))
    assert out["conversations"][0]["conversation"][1] == "You: It's around 7am."
    entry.runtime_data.speech.guest_mode = True
    api = await llm.async_get_api(hass, DOMAIN, llm.LLMContext(platform="t", context=None, language="en", assistant=None, device_id=None))
    assert "bin collection" not in api.api_prompt


async def test_overnight_reflection(hass: HomeAssistant, freezer) -> None:
    freezer.move_to(dt_util.as_utc(dt_util.now().replace(hour=4, minute=29, second=0)))
    entry = await _setup(hass, quiet_start="22:00:00", quiet_end="08:00:00")
    rt = entry.runtime_data
    rt.timeline.add("said", "The boiler engineer is booked for Thursday.")
    ai = _ai(hass, {"memories": ["The boiler engineer is coming on Thursday.", "The bank card PIN is 1234."],
                    "thoughts": ["The gate was left open twice yesterday evening."],
                    "question": "Who drives the small red car that parks outside on Fridays?"})
    freezer.tick(timedelta(minutes=1, seconds=10))
    async_fire_time_changed(hass)
    await hass.async_block_till_done()
    assert len(ai) == 1 and "Thursday" in ai[0].data["instructions"]
    texts = [m.text for m in rt.memory.items]
    assert "The boiler engineer is coming on Thursday." in texts
    assert not any("PIN" in t for t in texts)  # private things are never kept
    assert rt.curiosity.queue[0]["question"].startswith("Who drives")
    assert rt.followyou.queue[0]["source"] == "thought"
    assert rt.followyou.pending_thoughts() == []  # not till quiet hours end
    freezer.tick(timedelta(hours=4))
    assert rt.followyou.pending_thoughts() == ["The gate was left open twice yesterday evening."]
    assert hass.states.get("sensor.nova_mind").attributes["thoughts"]


async def test_curiosity_asks_once_and_says_to_remember(hass: HomeAssistant) -> None:
    entry = await _setup(hass)
    convo = async_mock_service(hass, "assist_satellite", "start_conversation")
    cur = entry.runtime_data.curiosity
    assert cur.wonder("Who is the man who comes to the door on Tuesdays?")
    assert not cur.wonder("Who is the man who comes to the door on Tuesdays?")
    out = await cur.async_ask(force=True)
    assert out["asked"]
    assert "sentient_remember" in convo[-1].data["extra_system_prompt"]
    assert not cur.wonder("Who is the man who comes to the door on Tuesdays?")  # asked: never again


async def test_cars_on_the_drive(hass: HomeAssistant, freezer) -> None:
    ents = er.async_get(hass)
    other = MockConfigEntry(domain="frigate")
    other.add_to_hass(hass)
    dev = dr.async_get(hass).async_get_or_create(config_entry_id=other.entry_id, identifiers={("frigate", "gate")})
    ents.async_get_or_create("camera", "frigate", "gate", suggested_object_id="gate", device_id=dev.id)
    ents.async_get_or_create("binary_sensor", "frigate", "gm", suggested_object_id="gate_motion", device_id=dev.id)
    ents.async_get_or_create("binary_sensor", "frigate", "gp", suggested_object_id="gate_person_occupancy", device_id=dev.id)
    hass.states.async_set("camera.gate", "idle")
    entry = await _setup(hass)
    rt = entry.runtime_data
    rt.spots.set("camera.gate", "Drive", [0.1, 0.4, 0.6, 0.9])
    rt.drive.async_setup_triggers()
    assert rt.drive.triggers() == ["binary_sensor.gate_motion"]
    answers = iter([{"cars": 1, "description": "silver estate"}, {"cars": 2, "description": "silver estate, white van"}])
    ai = _ai(hass, lambda _c: next(answers))
    for _ in range(2):
        hass.states.async_set("binary_sensor.gate_motion", "on")
        await hass.async_block_till_done()
        hass.states.async_set("binary_sensor.gate_motion", "off")
        await hass.async_block_till_done()
        freezer.tick(timedelta(seconds=26))
        async_fire_time_changed(hass)
        await hass.async_block_till_done()
        freezer.tick(timedelta(minutes=4))
    assert len(ai) == 2
    assert hass.states.get("sensor.nova_drive").state == "2"
    assert rt.timeline.items[0]["text"] == "A car arrived on the drive — now silver estate, white van"
    api = await llm.async_get_api(hass, DOMAIN, llm.LLMContext(platform="t", context=None, language="en", assistant=None, device_id=None))
    assert "On the drive" in api.api_prompt and "white van" in api.api_prompt


async def test_full_volume_speakers(hass: HomeAssistant) -> None:
    _, dev = _kitchen(hass)
    ents = er.async_get(hass)
    ents.async_get_or_create("number", "esphome", "vol", suggested_object_id="kitchen_volume", device_id=dev.id)
    ents.async_get_or_create("number", "esphome", "avol", suggested_object_id="kitchen_assistant_volume", device_id=dev.id)
    await _setup(hass, full_volume_speakers=["assist_satellite.kitchen"])
    hass.states.async_set("number.kitchen_volume", "53", {"max": 100})
    hass.states.async_set("number.kitchen_assistant_volume", "100", {"max": 100})
    numbers = async_mock_service(hass, "number", "set_value")
    async_mock_service(hass, "assist_satellite", "announce")
    await hass.services.async_call(DOMAIN, "say", {"message": "Someone's opened the gate."}, blocking=True)
    assert [(c.data["entity_id"], c.data["value"]) for c in numbers] == [
        ("number.kitchen_volume", 100), ("number.kitchen_volume", 53)]


async def test_auto_off_by_time_and_empty_room(hass: HomeAssistant, freezer) -> None:
    kitchen, _ = _kitchen(hass)
    ents = er.async_get(hass)
    ents.async_get_or_create("light", "test", "kl", suggested_object_id="kitchen_light")
    ents.async_update_entity("light.kitchen_light", area_id=kitchen.id)
    ents.async_get_or_create("light", "test", "ks", suggested_object_id="kitchen_screen")
    ents.async_update_entity("light.kitchen_screen", area_id=kitchen.id)
    entry = await _setup(hass)
    hass.states.async_set("switch.coffee", "on", {"friendly_name": "Coffee machine"})
    hass.states.async_set("light.kitchen_light", "on")
    hass.states.async_set("light.kitchen_screen", "on")
    hass.states.async_set("binary_sensor.kitchen_motion", "on", {"device_class": "motion"})
    lists = entry.runtime_data.lists["autooff"]
    lists.upsert({"name": "Coffee machine", "entities": ["switch.coffee"], "on_minutes": 10})
    lists.upsert({"name": "Kitchen", "areas": [kitchen.id], "motion": ["binary_sensor.kitchen_motion"], "empty_minutes": 20})
    sw = async_mock_service(hass, "switch", "turn_off")
    lights = async_mock_service(hass, "light", "turn_off")
    freezer.tick(timedelta(minutes=11))
    async_fire_time_changed(hass)
    await hass.async_block_till_done()
    assert [c.data["entity_id"] for c in sw] == ["switch.coffee"]
    assert not lights  # someone's still moving about
    hass.states.async_set("binary_sensor.kitchen_motion", "off", {"device_class": "motion"})
    freezer.tick(timedelta(minutes=21))
    async_fire_time_changed(hass)
    await hass.async_block_till_done()
    assert [c.data["entity_id"] for c in lights] == ["light.kitchen_light"]  # not the tablet screen
    assert all(c.context.parent_id for c in lights)


async def test_silent_alarm_just_does_it(hass: HomeAssistant, freezer) -> None:
    freezer.move_to(dt_util.as_utc(dt_util.now().replace(hour=20, minute=59, second=30)))
    entry = await _setup(hass)
    said = async_mock_service(hass, "assist_satellite", "announce")
    on = async_mock_service(hass, "switch", "turn_on")
    entry.runtime_data.lists["alarms"].upsert({
        "name": "Heated blanket on", "time": "21:00:00", "days": ["mon", "tue", "wed", "thu", "fri", "sat", "sun"],
        "silent": True, "actions": [{"action": "switch.turn_on", "target": {"entity_id": "switch.blanket"}}],
    })
    freezer.tick(timedelta(seconds=40))
    async_fire_time_changed(hass)
    await hass.async_block_till_done()
    assert on and not said


async def test_cards_become_dashboard_resources(hass: HomeAssistant) -> None:
    from homeassistant.setup import async_setup_component

    from custom_components.sentient import async_register_card_resources
    from homeassistant.components.lovelace.const import LOVELACE_DATA

    assert await async_setup_component(hass, "lovelace", {})
    added = await async_register_card_resources(hass, "1.0.0")
    assert added == ["/sentient_frontend/sentient-shows-card.js?v=1.0.0", "/sentient_frontend/sentient-hub-card.js?v=1.0.0"]
    assert await async_register_card_resources(hass, "1.0.0") == []  # already there
    await async_register_card_resources(hass, "1.0.1")
    urls = [r["url"] for r in hass.data[LOVELACE_DATA].resources.async_items()]
    assert sorted(urls) == ["/sentient_frontend/sentient-hub-card.js?v=1.0.1", "/sentient_frontend/sentient-shows-card.js?v=1.0.1"]
