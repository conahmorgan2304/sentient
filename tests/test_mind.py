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
    assert cur.wonder("What is the garage light for at 7pm?")
    assert not cur.wonder("What is the garage light for at 7pm?")
    out = await cur.async_ask(force=True)
    assert out["asked"]
    assert "sentient_remember" in convo[-1].data["extra_system_prompt"]
    assert not cur.wonder("What is the garage light for at 7pm?")  # asked: never again


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
    assert added == ["/sentient_frontend/sentient-shows-card.js?v=1.0.0", "/sentient_frontend/sentient-hub-card.js?v=1.0.0",
                     "/sentient_frontend/sentient-screen-card.js?v=1.0.0"]
    assert await async_register_card_resources(hass, "1.0.0") == []  # already there
    await async_register_card_resources(hass, "1.0.1")
    urls = [r["url"] for r in hass.data[LOVELACE_DATA].resources.async_items()]
    assert sorted(urls) == ["/sentient_frontend/sentient-hub-card.js?v=1.0.1", "/sentient_frontend/sentient-screen-card.js?v=1.0.1",
                            "/sentient_frontend/sentient-shows-card.js?v=1.0.1"]


async def test_speaks_through_an_echo_too(hass: HomeAssistant, monkeypatch) -> None:
    import custom_components.sentient.speech as speech_mod

    async def no_wait(_s):
        return None

    other = MockConfigEntry(domain="alexa_media")
    other.add_to_hass(hass)
    er.async_get(hass).async_get_or_create("media_player", "alexa_media", "echo1", suggested_object_id="bathroom_echo",
                                           config_entry=other)
    await _setup(hass, speakers=["assist_satellite.kitchen", "media_player.bathroom_echo"])
    hass.states.async_set("media_player.bathroom_echo", "idle", {"supported_features": 318399})
    sat = async_mock_service(hass, "assist_satellite", "announce")
    alexa = async_mock_service(hass, "notify", "alexa_media")
    start = async_mock_service(hass, "assist_satellite", "start_conversation")
    monkeypatch.setattr(speech_mod.asyncio, "sleep", no_wait)
    await hass.services.async_call(DOMAIN, "say", {"message": "Tea's up.", "speaker": "media_player.bathroom_echo"}, blocking=True)
    assert not sat and alexa[-1].data["target"] == ["media_player.bathroom_echo"]
    assert alexa[-1].data["data"]["type"] in ("announce", "tts")
    # an Echo can't listen for an answer: a question is just said
    await hass.services.async_call(DOMAIN, "ask", {"question": "Fancy a brew?", "speaker": "media_player.bathroom_echo"}, blocking=True)
    assert not start and alexa[-1].data["message"] == "Fancy a brew?"


async def test_routine_with_details(hass: HomeAssistant) -> None:
    entry = await _setup(hass)
    calls = async_mock_service(hass, "vacuum", "vacuum_clean_segment")
    rt = entry.runtime_data
    rt.shortcuts.upsert({
        "name": "Hoover rooms", "inputs": "rooms: which rooms — living room, kitchen, hallway, dining room",
        "say": "Hoovering the {rooms}.",
        "actions": [{"action": "vacuum.vacuum_clean_segment", "target": {"entity_id": "vacuum.robot"},
                     "data": {"segments": "{% set m = {'living room': 10, 'kitchen': 11, 'hallway': 12, 'dining room': 13} %}"
                                          "{% set ns = namespace(s=[]) %}{% for x in ([rooms] if rooms is string else rooms) %}"
                                          "{% if x | lower in m %}{% set ns.s = ns.s + [m[x | lower]] %}{% endif %}{% endfor %}{{ ns.s }}"}}],
    })
    out = await rt.shortcuts.async_run("hoover rooms")
    assert "needs" in out and not calls
    out = await rt.shortcuts.async_run("hoover rooms", details={"rooms": ["kitchen", "hallway"]})
    assert calls[-1].data["segments"] == [11, 12]
    assert out["said"].startswith("Hoovering the kitchen and hallway")
    api = await llm.async_get_api(hass, DOMAIN, llm.LLMContext(platform="t", context=None, language="en", assistant=None, device_id=None))
    assert "Hoover rooms (details: rooms = which rooms" in api.api_prompt


async def test_echos_can_be_switched_off(hass: HomeAssistant, monkeypatch) -> None:
    import custom_components.sentient.speech as speech_mod

    async def no_wait(_s):
        return None

    other = MockConfigEntry(domain="alexa_media")
    other.add_to_hass(hass)
    er.async_get(hass).async_get_or_create("media_player", "alexa_media", "echo1", suggested_object_id="bathroom_echo",
                                           config_entry=other)
    await _setup(hass, speakers=["assist_satellite.kitchen", "media_player.bathroom_echo"])
    hass.states.async_set("media_player.bathroom_echo", "idle")
    sat = async_mock_service(hass, "assist_satellite", "announce")
    alexa = async_mock_service(hass, "notify", "alexa_media")
    monkeypatch.setattr(speech_mod.asyncio, "sleep", no_wait)
    await hass.services.async_call("switch", "turn_off", {"entity_id": "switch.nova_echo_speakers"}, blocking=True)
    await hass.services.async_call(DOMAIN, "say", {"message": "Alarm.", "speaker": "media_player.bathroom_echo"}, blocking=True)
    assert not alexa and sat  # said on a voice speaker instead


async def test_echos_for_alarms_and_reminders_only(hass: HomeAssistant, monkeypatch) -> None:
    import custom_components.sentient.speech as speech_mod

    async def no_wait(_s):
        return None

    other = MockConfigEntry(domain="alexa_media")
    other.add_to_hass(hass)
    er.async_get(hass).async_get_or_create("media_player", "alexa_media", "echo1", suggested_object_id="bathroom_echo",
                                           config_entry=other)
    entry = await _setup(hass, speakers=["assist_satellite.kitchen", "media_player.bathroom_echo"])
    hass.states.async_set("media_player.bathroom_echo", "idle")
    sat = async_mock_service(hass, "assist_satellite", "announce")
    alexa = async_mock_service(hass, "notify", "alexa_media")
    monkeypatch.setattr(speech_mod.asyncio, "sleep", no_wait)
    await hass.services.async_call("switch", "turn_on", {"entity_id": "switch.nova_echos_alarms_and_reminders_only"},
                                   blocking=True)
    speech = entry.runtime_data.speech
    await speech.async_say("Someone's at the gate.", "normal", ["media_player.bathroom_echo"], source="announce:Gate")
    assert not alexa and sat  # an announcement: voice speakers only
    await speech.async_say("Take the bins out.", "urgent", ["media_player.bathroom_echo"], source="reminder:bins")
    await speech.async_say("Time to get up.", "urgent", ["media_player.bathroom_echo"], source="alarm:Wake")
    assert [c.data["message"] for c in alexa] == ["Take the bins out.", "Time to get up."]


async def test_why_did_that_happen(hass: HomeAssistant, freezer) -> None:
    from homeassistant.core import Context

    entry = await _setup(hass)
    hass.states.async_set("light.landing", "on", {"friendly_name": "Landing light"})
    entry.runtime_data.timeline.add("light", "Landing light off", "auto-off (Landing): no movement for 20 min")
    hass.states.async_set("light.landing", "off", {"friendly_name": "Landing light"}, context=Context(parent_id="x"))
    api = await llm.async_get_api(hass, DOMAIN, llm.LLMContext(platform="t", context=None, language="en", assistant=None, device_id=None))
    out = await api.async_call_tool(llm.ToolInput(tool_name="sentient_why", tool_args={"what": "landing light"}))
    assert out["by"].startswith("an automation") and "auto-off" in out["house_log"][0]
    hass.states.async_set("light.landing", "on", {"friendly_name": "Landing light"}, context=Context())
    out = await api.async_call_tool(llm.ToolInput(tool_name="sentient_why", tool_args={"what": "light.landing"}))
    assert out["by"].startswith("the device itself")


async def test_look_ahead_only_speaks_when_worth_it(hass: HomeAssistant) -> None:
    entry = await _setup(hass, briefing_calendars=[])
    hass.states.async_set("weather.home", "sunny")

    async def forecasts(call: ServiceCall):
        from datetime import timedelta as td

        tomorrow = (dt_util.now() + td(days=1)).replace(hour=12)
        return {"weather.home": {"forecast": [{"datetime": tomorrow.isoformat(), "condition": "snowy", "temperature": 1, "templow": -4}]}}

    hass.services.async_register("weather", "get_forecasts", forecasts, supports_response=SupportsResponse.ONLY)
    said = async_mock_service(hass, "assist_satellite", "announce")
    ai = _ai(hass, {"speak": True, "message": "Snow and frost tomorrow — the car will need defrosting."})
    out = await entry.runtime_data.foresight.async_look_ahead()
    assert out["facts"]["weather"]["condition"] == "snowy" and "snowy" in ai[0].data["instructions"]
    assert said and out["said"].startswith("Snow and frost")
    said.clear()
    _ai(hass, {"speak": False})
    out = await entry.runtime_data.foresight.async_look_ahead()
    assert not said and out["said"] is None


async def test_auto_off_learns_to_wait_longer(hass: HomeAssistant, freezer) -> None:
    from homeassistant.core import Context

    entry = await _setup(hass)
    hass.states.async_set("light.lounge", "on", {"friendly_name": "Lounge light"})
    hass.states.async_set("binary_sensor.lounge_motion", "off", {"device_class": "motion"})
    lists = entry.runtime_data.lists["autooff"]
    item = lists.upsert({"name": "Lounge", "entities": ["light.lounge"], "motion": ["binary_sensor.lounge_motion"], "empty_minutes": 20})
    async_mock_service(hass, "light", "turn_off")
    freezer.tick(timedelta(minutes=21))
    async_fire_time_changed(hass)
    await hass.async_block_till_done()
    hass.states.async_set("light.lounge", "off", {"friendly_name": "Lounge light"}, context=Context(parent_id="x"))
    freezer.tick(timedelta(seconds=40))
    hass.states.async_set("light.lounge", "on", {"friendly_name": "Lounge light"}, context=Context())  # someone's still there
    await hass.async_block_till_done()
    assert lists.items[item["id"]]["empty_minutes"] == 25
    assert entry.runtime_data.timeline.items[0]["kind"] == "learned"


async def test_house_goes_to_sleep_and_wakes(hass: HomeAssistant, freezer) -> None:
    freezer.move_to(dt_util.as_utc(dt_util.now().replace(hour=23, minute=0, second=0)))
    kitchen, _ = _kitchen(hass)
    ents = er.async_get(hass)
    hall = ar.async_get(hass).async_create("Hallway")
    for oid, area in (("kitchen_motion", kitchen.id), ("hall_motion", hall.id)):
        ents.async_get_or_create("binary_sensor", "test", oid, suggested_object_id=oid)
        ents.async_update_entity(f"binary_sensor.{oid}", area_id=area)
    entry = await _setup(hass, quiet_start="22:00:00", quiet_end="07:00:00",
                         inside_sensors=["binary_sensor.kitchen_motion", "binary_sensor.hall_motion"],
                         sleep_actions=[{"action": "switch.turn_off", "target": {"entity_id": "switch.screens"}}])
    off = async_mock_service(hass, "switch", "turn_off")
    for e in ("binary_sensor.kitchen_motion", "binary_sensor.hall_motion"):
        hass.states.async_set(e, "off", {"device_class": "motion"})
    freezer.tick(timedelta(minutes=21))
    async_fire_time_changed(hass)
    await hass.async_block_till_done()
    assert hass.states.get("sensor.nova_house").state == "asleep" and off
    freezer.move_to(dt_util.as_utc(dt_util.now().replace(hour=6, minute=50) + timedelta(days=1)))
    hass.states.async_set("binary_sensor.hall_motion", "on", {"device_class": "motion"})
    await hass.async_block_till_done()
    assert hass.states.get("sensor.nova_house").state == "asleep"  # one room: a trip to the loo
    hass.states.async_set("binary_sensor.kitchen_motion", "on", {"device_class": "motion"})
    await hass.async_block_till_done()
    assert hass.states.get("sensor.nova_house").state == "awake"


async def test_goodnight_routine_means_asleep(hass: HomeAssistant) -> None:
    entry = await _setup(hass)
    entry.runtime_data.shortcuts.upsert({"name": "Goodnight", "actions": []})
    await entry.runtime_data.shortcuts.async_run("goodnight")
    await hass.async_block_till_done()
    assert entry.runtime_data.sleep.asleep


def test_usual_bedtime_and_getting_up() -> None:
    from custom_components.sentient.sleep import Sleep

    s = Sleep.__new__(Sleep)
    tz = dt_util.get_default_time_zone()
    from datetime import datetime as D

    s.history = [
        {"slept": D(2026, 10, d, 23, 10, tzinfo=tz).isoformat(), "woke": D(2026, 10, d + 1, 6, 40, tzinfo=tz).isoformat()}
        for d in (1, 2, 3)
    ] + [{"slept": D(2026, 10, 5, 0, 30, tzinfo=tz).isoformat(), "woke": D(2026, 10, 5, 7, 0, tzinfo=tz).isoformat()}]
    usual = s.usual()
    assert usual["up"] in ("06:40", "06:50") and usual["bedtime"] in ("23:10", "23:50")


def test_card_addresses_follow_the_files() -> None:
    from custom_components.sentient import CARDS, _card_tags

    tags = _card_tags()
    assert set(tags) == set(CARDS) and all(len(t) == 10 for t in tags.values())
    assert len(_card_tags(("sentient-panel",))["sentient-panel"]) == 10


async def test_drive_only_asks_the_ai_when_the_picture_changes(hass: HomeAssistant, freezer) -> None:
    import io

    from PIL import Image, ImageDraw

    from custom_components.sentient.spots import Spots, changed_share

    def picture(car: bool, light: int) -> bytes:
        img = Image.new("RGB", (320, 240), (light, light, light))
        draw = ImageDraw.Draw(img)
        draw.rectangle((0, 160, 320, 240), fill=(light // 2,) * 3)  # the drive
        if car:
            draw.rectangle((60, 120, 220, 210), fill=(200, 30, 30))
        out = io.BytesIO()
        img.save(out, "JPEG")
        return out.getvalue()

    box = [0, 0, 1, 1]
    empty_day = Spots.fingerprint_of(picture(False, 180), box)
    empty_dusk = Spots.fingerprint_of(picture(False, 110), box)
    with_car = Spots.fingerprint_of(picture(True, 180), box)
    assert changed_share(empty_day, empty_dusk) < 0.06  # just darker: same drive
    assert changed_share(empty_day, with_car) > 0.06

    hass.states.async_set("camera.gate", "idle")
    entry = await _setup(hass)
    rt = entry.runtime_data
    rt.spots.set("camera.gate", "Drive", [0.1, 0.4, 0.6, 0.9])
    prints = iter([empty_day, empty_dusk, with_car])

    async def fake(_camera, _key):
        return next(prints)

    rt.spots.async_fingerprint = fake
    answers = iter([{"cars": 0}, {"cars": 1, "description": "red hatchback"}])
    ai = _ai(hass, lambda _c: next(answers))
    for _ in range(3):
        await rt.drive.async_check()
        freezer.tick(timedelta(minutes=10))
    assert len(ai) == 2 and rt.drive.skipped == 1
    assert rt.drive.describe()["cars"] == 1
