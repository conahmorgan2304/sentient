"""The AI meter and daily cap, plain words when the AI can't help, the self-check,
learning from corrections, and appliances."""

from datetime import timedelta

from homeassistant.core import HomeAssistant, ServiceCall, SupportsResponse
from homeassistant.exceptions import HomeAssistantError
from homeassistant.helpers import llm
from homeassistant.util import dt as dt_util
from pytest_homeassistant_custom_component.common import MockConfigEntry, async_fire_time_changed, async_mock_service

from custom_components.sentient.ai import AIUnavailable, async_generate
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


def _ai(hass: HomeAssistant, reply):
    calls: list[ServiceCall] = []

    async def generate(call: ServiceCall):
        calls.append(call)
        if isinstance(reply, Exception):
            raise reply
        return {"conversation_id": "x", "data": reply}

    hass.services.async_register("ai_task", "generate_data", generate, supports_response=SupportsResponse.ONLY)
    return calls


async def test_daily_cap_counts_by_purpose_and_stops(hass: HomeAssistant) -> None:
    entry = await _setup(hass, daily_ai_limit=2)
    gate = entry.runtime_data.ai
    assert gate.spend("doorbell") and gate.spend("briefing")
    assert not gate.spend("doorbell")  # used up
    assert gate.spend("question", force=True)  # someone asked: always allowed, still counted
    info = gate.describe()
    assert info["today"] == 3 and info["capped"] and info["by_purpose"]["doorbell"] == 1
    entry.runtime_data.health.async_check()
    assert any(i["kind"] == "limit" for i in entry.runtime_data.health.issues)
    assert "Today's AI calls are used up" in " ".join(entry.runtime_data.health.describe())


async def test_no_limit_when_zero(hass: HomeAssistant) -> None:
    entry = await _setup(hass, daily_ai_limit=0)
    assert all(entry.runtime_data.ai.spend("x") for _ in range(300))


async def test_briefing_in_plain_words_when_capped(hass: HomeAssistant) -> None:
    await _setup(hass, daily_ai_limit=1)
    said = async_mock_service(hass, "assist_satellite", "announce")
    calls = _ai(hass, "AI words")
    hass.states.async_set("weather.home", "rainy", {"temperature": 9, "temperature_unit": "°C"})
    entry = hass.config_entries.async_entries(DOMAIN)[0]
    entry.runtime_data.ai.spend("something")  # the day's one call is gone
    res = await hass.services.async_call(DOMAIN, "briefing", {}, blocking=True, return_response=True)
    assert not calls and res["spoken"]
    assert said[-1].data["message"].startswith("It's rainy, 9°C out.")


async def test_failing_ai_is_noticed_then_recovers(hass: HomeAssistant) -> None:
    entry = await _setup(hass)
    _ai(hass, HomeAssistantError("quota exceeded"))
    for _ in range(3):
        try:
            await async_generate(hass, {"task_name": "t", "instructions": "x"})
        except AIUnavailable:
            pass
    gate = entry.runtime_data.ai
    assert gate.failing and "quota" in gate.describe()["last_error"]
    entry.runtime_data.health.async_check()
    assert "The AI hasn't answered" in " ".join(entry.runtime_data.health.describe())
    hass.services.async_remove("ai_task", "generate_data")
    _ai(hass, "fine")
    await async_generate(hass, {"task_name": "t", "instructions": "x"})
    assert not gate.failing
    entry.runtime_data.health.async_check()
    assert not any(i["kind"] == "ai" for i in entry.runtime_data.health.issues)


async def test_briefing_in_plain_words_when_ai_fails(hass: HomeAssistant) -> None:
    await _setup(hass)
    said = async_mock_service(hass, "assist_satellite", "announce")
    _ai(hass, HomeAssistantError("down"))
    hass.states.async_set("weather.home", "sunny", {"temperature": 14, "temperature_unit": "°C"})
    res = await hass.services.async_call(DOMAIN, "briefing", {}, blocking=True, return_response=True)
    assert res["spoken"] and "sunny" in said[-1].data["message"]


async def test_speaker_failure_shows_in_self_check(hass: HomeAssistant) -> None:
    entry = await _setup(hass)

    async def broken(call: ServiceCall):
        raise HomeAssistantError("satellite offline")

    hass.services.async_register("assist_satellite", "announce", broken)
    try:
        await entry.runtime_data.speech.async_say("Test", "urgent", None, source="test")
    except HomeAssistantError:
        pass
    entry.runtime_data.health.async_check()
    assert "Couldn't speak on the voice speakers" in entry.runtime_data.health.describe()
    hass.services.async_remove("assist_satellite", "announce")
    async_mock_service(hass, "assist_satellite", "announce")
    await entry.runtime_data.speech.async_say("Test again", "urgent", None, source="test")
    entry.runtime_data.health.async_check()
    assert not any(i["kind"] == "speaker" for i in entry.runtime_data.health.issues)


async def test_stuck_motion_sensor(hass: HomeAssistant) -> None:
    entry = await _setup(hass, inside_sensors=["binary_sensor.hall_motion", "binary_sensor.landing_motion"])
    long_ago = dt_util.utcnow() - timedelta(days=5)
    hass.states.async_set("binary_sensor.hall_motion", "off", {"device_class": "motion", "friendly_name": "Hall motion"})
    hass.states.async_set("binary_sensor.landing_motion", "off", {"device_class": "motion"})
    hass.states.get("binary_sensor.hall_motion").last_changed = long_ago  # type: ignore[misc]
    entry.runtime_data.health.async_check()
    lines = entry.runtime_data.health.describe()
    assert any(line.startswith("Hall motion hasn't seen anything for 5 days") for line in lines)


async def test_correction_is_learned_and_in_every_prompt(hass: HomeAssistant) -> None:
    entry = await _setup(hass)
    api = await llm.async_get_api(hass, DOMAIN, llm.LLMContext(platform="test", context=None, language="en",
                                                                 assistant="conversation", device_id=None))
    out = await api.async_call_tool(llm.ToolInput(
        tool_name="sentient_correction", tool_args={"lesson": 'In the living room, "the light" means the lamp.'}))
    assert out["learned"].startswith("In the living room")
    mem = entry.runtime_data.memory.items[-1]
    assert mem.category == "correction"
    api2 = await llm.async_get_api(hass, DOMAIN, llm.LLMContext(platform="test", context=None, language="en",
                                                                  assistant="conversation", device_id=None))
    prompt = api2.api_prompt
    assert "corrected on these before" in prompt and '"the light" means the lamp' in prompt
    assert prompt.count('"the light" means the lamp') == 1  # not listed again among ordinary memories


async def test_reflection_keeps_lessons(hass: HomeAssistant) -> None:
    entry = await _setup(hass)
    entry.runtime_data.timeline.add("light", "Living room light on")
    _ai(hass, {"memories": [], "thoughts": [], "question": "", "lessons": ['"The big light" means the ceiling light.']})
    result = await entry.runtime_data.reflection.async_reflect()
    assert result["lessons"] == ['"The big light" means the ceiling light.']
    assert any(m.category == "correction" for m in entry.runtime_data.memory.items)


async def test_appliance_learns_says_finished_and_flags_stuck(hass: HomeAssistant) -> None:
    entry = await _setup(hass, appliances=["vacuum.robot"])
    said = async_mock_service(hass, "assist_satellite", "announce")
    app = entry.runtime_data.appliances
    hass.states.async_set("vacuum.robot", "docked", {"friendly_name": "Robot"})
    await hass.async_block_till_done()
    for minutes in (40, 44, 42):
        hass.states.async_set("vacuum.robot", "cleaning", {"friendly_name": "Robot"})
        await hass.async_block_till_done()
        app.running["vacuum.robot"] = (dt_util.utcnow() - timedelta(minutes=minutes)).isoformat()
        hass.states.async_set("vacuum.robot", "docked", {"friendly_name": "Robot"})
        await hass.async_block_till_done()
    assert app.usual("vacuum.robot") == 42
    assert said[-1].data["message"] == "Robot's finished."
    assert any(i["kind"] == "appliance" and i["text"] == "Robot finished" for i in entry.runtime_data.timeline.items)
    # a run going on far longer than usual
    hass.states.async_set("vacuum.robot", "cleaning", {"friendly_name": "Robot"})
    await hass.async_block_till_done()
    app.running["vacuum.robot"] = (dt_util.utcnow() - timedelta(minutes=70)).isoformat()
    async_fire_time_changed(hass, dt_util.utcnow() + timedelta(minutes=1))
    await hass.async_block_till_done()
    assert "It may be stuck" in said[-1].data["message"]
    listing = app.listing()[0]
    assert listing["running"] and listing["usual_minutes"] == 42 and listing["done_about"]
    assert "Running now: Robot" in (app.prompt_line() or "")


async def test_power_sensor_finishes_after_settling(hass: HomeAssistant) -> None:
    entry = await _setup(hass)
    said = async_mock_service(hass, "assist_satellite", "announce")
    app = entry.runtime_data.appliances
    attrs = {"device_class": "power", "unit_of_measurement": "W", "friendly_name": "Washing machine power"}
    hass.states.async_set("sensor.washing_machine_power", "0", attrs)
    assert "sensor.washing_machine_power" in app.watched()  # picked up by name
    hass.states.async_set("sensor.washing_machine_power", "1800", attrs)
    await hass.async_block_till_done()
    app.running["sensor.washing_machine_power"] = (dt_util.utcnow() - timedelta(minutes=90)).isoformat()
    hass.states.async_set("sensor.washing_machine_power", "1", attrs)  # a pause, or the end
    await hass.async_block_till_done()
    assert "sensor.washing_machine_power" in app.running
    app._low["sensor.washing_machine_power"] -= timedelta(minutes=6)
    async_fire_time_changed(hass, dt_util.utcnow() + timedelta(minutes=6))
    await hass.async_block_till_done()
    assert "sensor.washing_machine_power" not in app.running
    assert said[-1].data["message"] == "Washing machine's finished."


async def test_noticer_leaves_appliances_alone(hass: HomeAssistant) -> None:
    entry = await _setup(hass, appliances=["vacuum.robot"], notice_entities=["vacuum.robot"])
    hass.states.async_set("vacuum.robot", "docked")
    await hass.async_block_till_done()
    hass.states.async_set("vacuum.robot", "cleaning")
    await hass.async_block_till_done()
    assert not entry.runtime_data.noticer._pending


async def test_visitor_still_announced_when_capped(hass: HomeAssistant) -> None:
    entry = await _setup(hass, daily_ai_limit=1, door_camera="camera.doorbell", announce_visitors=True)
    said = async_mock_service(hass, "assist_satellite", "announce")
    calls = _ai(hass, {"kind": "delivery", "description": "x"})
    hass.states.async_set("camera.doorbell", "idle")
    entry.runtime_data.ai.spend("used")
    from unittest.mock import patch

    with patch("custom_components.sentient.visitors.asyncio.sleep"):
        visit = await entry.runtime_data.visitors.async_visit("test")
    assert not calls and visit is not None
    assert said[-1].data["message"] == "Someone's at the door."


# ----- the voice backup --------------------------------------------------------------------
from homeassistant.components import conversation  # noqa: E402
from homeassistant.helpers import intent  # noqa: E402


class _Agent(conversation.AbstractConversationAgent):
    def __init__(self, reply=None, fail=False) -> None:
        self.reply, self.fail, self.seen = reply, fail, []

    @property
    def supported_languages(self):
        return "*"

    async def async_process(self, user_input):
        self.seen.append(user_input.text)
        if self.fail:
            raise HomeAssistantError("API quota exceeded")
        response = intent.IntentResponse(language=user_input.language)
        response.async_set_speech(self.reply)
        return conversation.ConversationResult(response=response, conversation_id=user_input.conversation_id)


async def _voice_setup(hass: HomeAssistant, main: _Agent, backup: _Agent):
    from homeassistant.setup import async_setup_component

    assert await async_setup_component(hass, "homeassistant", {})
    assert await async_setup_component(hass, "conversation", {})
    main_entry = MockConfigEntry(domain="test_main"); main_entry.add_to_hass(hass)
    backup_entry = MockConfigEntry(domain="test_backup"); backup_entry.add_to_hass(hass)
    conversation.async_set_agent(hass, main_entry, main)
    conversation.async_set_agent(hass, backup_entry, backup)
    entry = await _setup(hass, voice_agent=main_entry.entry_id, backup_agent=backup_entry.entry_id)
    agent = next(s.entity_id for s in hass.states.async_all("conversation") if s.entity_id.startswith("conversation.nova"))
    return entry, agent


async def test_voice_goes_to_the_main_agent(hass: HomeAssistant) -> None:
    from homeassistant.core import Context

    main, backup = _Agent("Lights off."), _Agent("Backup here.")
    _entry, agent = await _voice_setup(hass, main, backup)
    assert agent == "conversation.nova_with_backup"
    result = await conversation.async_converse(hass, "lights off", None, Context(), agent_id=agent)
    assert result.response.speech["plain"]["speech"] == "Lights off." and not backup.seen


async def test_voice_falls_back_when_the_ai_fails(hass: HomeAssistant) -> None:
    from homeassistant.core import Context

    main, backup = _Agent(fail=True), _Agent("Turned off the light.")
    entry, agent = await _voice_setup(hass, main, backup)
    result = await conversation.async_converse(hass, "lights off", None, Context(), agent_id=agent)
    assert main.seen and backup.seen
    assert result.response.speech["plain"]["speech"] == "Turned off the light."
    assert entry.runtime_data.ai.fails_in_row == 1
    assert any("backup agent" in i["text"] for i in entry.runtime_data.timeline.items)


async def test_voice_says_plainly_when_both_fail(hass: HomeAssistant) -> None:
    from homeassistant.core import Context

    _entry, agent = await _voice_setup(hass, _Agent(fail=True), _Agent(fail=True))
    result = await conversation.async_converse(hass, "what's the news", None, Context(), agent_id=agent)
    assert "isn't answering" in result.response.speech["plain"]["speech"]


# ----- animals ---------------------------------------------------------------------------------
async def test_hutch_too_cold_then_back(hass: HomeAssistant) -> None:
    attrs = {"device_class": "temperature", "unit_of_measurement": "°C", "friendly_name": "Rabbit hutch Temperature"}
    hass.states.async_set("sensor.hutch", "10", attrs)
    entry = await _setup(hass, animal_sensors=["sensor.hutch"], animal_low=3, animal_high=27)
    said = async_mock_service(hass, "assist_satellite", "announce")
    hass.states.async_set("sensor.hutch", "2.4", attrs)
    await hass.async_block_till_done()
    assert said[-1].data["message"] == "It's down to 2 degrees in the rabbit hutch — worth checking the bedding."
    hass.states.async_set("sensor.hutch", "1.8", attrs)
    hass.states.async_set("sensor.hutch", "3.5", attrs)  # within the degree's grace: still cold
    await hass.async_block_till_done()
    assert len(said) == 1
    hass.states.async_set("sensor.hutch", "8", attrs)
    hass.states.async_set("sensor.hutch", "29", attrs)
    await hass.async_block_till_done()
    assert "up to 29 degrees in the rabbit hutch" in said[-1].data["message"]
    assert entry.runtime_data.animals.out_of_range() == ["the rabbit hutch 29° (too hot)"]


async def test_frost_warning_mentions_the_animals(hass: HomeAssistant) -> None:
    from custom_components.sentient.foresight import plain_heads_up

    text = plain_heads_up({"weather": {"templow": -1}, "animals_living_outside_now": ["the rabbit hutch 4°"]})
    assert text == "Frost's forecast for tomorrow morning, so the car may need defrosting and the water in the rabbit hutch may freeze."
