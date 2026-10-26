"""Noticing changes and speaking up — cheaply."""

from datetime import timedelta

from homeassistant.core import HomeAssistant, ServiceCall, SupportsResponse
from homeassistant.helpers import llm
from pytest_homeassistant_custom_component.common import (
    MockConfigEntry,
    async_fire_time_changed,
    async_mock_service,
)

from custom_components.sentient.const import DOMAIN

OPTIONS = {
    "persona": "Nova",
    "speakers": ["assist_satellite.kitchen"],
    "default_speaker": "assist_satellite.kitchen",
    "quiet_start": "00:00:00",
    "quiet_end": "00:00:00",
    "banned_words": "sir",
    "notice_entities": ["sensor.washer", "binary_sensor.back_door", "sensor.outside_temp"],
    "chattiness": "quiet",
    "house_rules": "Never say the house is empty.",
}


async def _setup(hass: HomeAssistant, **extra) -> MockConfigEntry:
    entry = MockConfigEntry(domain=DOMAIN, title="Nova", data={**OPTIONS, **extra}, unique_id=DOMAIN)
    entry.add_to_hass(hass)
    assert await hass.config_entries.async_setup(entry.entry_id)
    await hass.async_block_till_done()
    hass.states.async_set("assist_satellite.kitchen", "idle", {"supported_features": 3})
    hass.states.async_set("sensor.washer", "running", {"friendly_name": "Washing machine"})
    hass.states.async_set("binary_sensor.back_door", "off", {"friendly_name": "Back door"})
    hass.states.async_set("sensor.outside_temp", "12", {"friendly_name": "Outside", "unit_of_measurement": "°C"})
    await hass.async_block_till_done()
    return entry


def _ai(hass: HomeAssistant, reply: dict):
    calls: list[ServiceCall] = []

    async def generate(call: ServiceCall):
        calls.append(call)
        return {"conversation_id": "x", "data": reply}

    hass.services.async_register("ai_task", "generate_data", generate, supports_response=SupportsResponse.ONLY)
    return calls


async def _settle(hass: HomeAssistant, freezer) -> None:
    freezer.tick(timedelta(seconds=21))
    async_fire_time_changed(hass)
    await hass.async_block_till_done()


async def test_change_batched_then_spoken(hass: HomeAssistant, freezer) -> None:
    await _setup(hass)
    said = async_mock_service(hass, "assist_satellite", "announce")
    ai = _ai(hass, {"speak": True, "message": "The washing's done, and the back door's open."})
    hass.states.async_set("sensor.washer", "finished", {"friendly_name": "Washing machine"})
    hass.states.async_set("binary_sensor.back_door", "on", {"friendly_name": "Back door"})
    await hass.async_block_till_done()
    assert not ai  # waits to batch
    await _settle(hass, freezer)
    assert len(ai) == 1 and said
    prompt = ai[0].data["instructions"]
    assert "Washing machine changed from running to finished" in prompt
    assert "Back door changed from off to on" in prompt
    assert "Never say the house is empty." in prompt
    assert said[-1].data["message"] == "The washing's done, and the back door's open."
    noticed = hass.states.get("sensor.nova_last_spoken").attributes["noticed"]
    assert noticed[0]["result"] == "spoken"


async def test_ai_says_nothing_worth_saying(hass: HomeAssistant, freezer) -> None:
    await _setup(hass)
    said = async_mock_service(hass, "assist_satellite", "announce")
    _ai(hass, {"speak": False})
    hass.states.async_set("binary_sensor.back_door", "on", {"friendly_name": "Back door"})
    await _settle(hass, freezer)
    assert not said


async def test_question_opens_the_mic(hass: HomeAssistant, freezer) -> None:
    await _setup(hass)
    convo = async_mock_service(hass, "assist_satellite", "start_conversation")
    _ai(hass, {"speak": True, "question": True, "message": "It's turned chilly. Shall I put the heating on?"})
    hass.states.async_set("sensor.outside_temp", "6", {"friendly_name": "Outside", "unit_of_measurement": "°C"})
    await _settle(hass, freezer)
    assert convo[-1].data["start_message"] == "It's turned chilly. Shall I put the heating on?"
    assert "shall i put the heating on" in convo[-1].data["extra_system_prompt"].lower()


async def test_free_rules_run_before_ai(hass: HomeAssistant, freezer) -> None:
    await _setup(hass, quiet_start="00:00:00", quiet_end="23:59:59")
    ai = _ai(hass, {"speak": True, "message": "x"})
    hass.states.async_set("binary_sensor.back_door", "on", {"friendly_name": "Back door"})
    await _settle(hass, freezer)
    assert not ai
    noticed = hass.states.get("sensor.nova_last_spoken").attributes["noticed"]
    assert noticed[0]["result"] == "skipped: quiet_hours"


async def test_hourly_limit_and_jitter(hass: HomeAssistant, freezer) -> None:
    await _setup(hass)
    async_mock_service(hass, "assist_satellite", "announce")
    ai = _ai(hass, {"speak": True, "message": "Something happened."})
    hass.states.async_set("sensor.outside_temp", "12.2", {"unit_of_measurement": "°C"})  # jitter
    await _settle(hass, freezer)
    assert not ai
    for i, state in enumerate(["on", "off", "on"]):
        hass.states.async_set("binary_sensor.back_door", state, {"friendly_name": "Back door"})
        await hass.async_block_till_done()
        _ = i
        await _settle(hass, freezer)
        freezer.tick(timedelta(seconds=31))
    # quiet = two remarks an hour; the third change never reaches the AI
    assert len(ai) == 2


async def test_ask_service_and_house_rules_in_prompt(hass: HomeAssistant) -> None:
    await _setup(hass)
    convo = async_mock_service(hass, "assist_satellite", "start_conversation")
    res = await hass.services.async_call(
        DOMAIN, "ask", {"question": "Sir, fancy a brew?"}, blocking=True, return_response=True
    )
    assert res["reason"] == "asked" and convo[-1].data["start_message"] == "Fancy a brew?"
    # a speaker that can't listen gets a plain announcement instead
    said = async_mock_service(hass, "assist_satellite", "announce")
    hass.states.async_set("assist_satellite.kitchen", "idle", {"supported_features": 1})
    res = await hass.services.async_call(
        DOMAIN, "ask", {"question": "Shall I put the lights on?"}, blocking=True, return_response=True
    )
    assert res["reason"] == "spoken" and said
    ctx = llm.LLMContext(platform="test", context=None, language="en", assistant="conversation", device_id=None)
    api = await llm.async_get_api(hass, "sentient", ctx)
    assert "Never say the house is empty." in api.api_prompt


def test_finished_remark_contradiction() -> None:
    from datetime import datetime

    from custom_components.sentient.notice import Change, contradicts

    start = Change(entity_id="sensor.kettle_status", name="Kettle Status", area="Kitchen", old="standby",
                   new="heating", unit="", when=datetime.now())
    done = Change(entity_id="sensor.kettle_status", name="Kettle Status", area="Kitchen", old="heating",
                  new="standby", unit="", when=datetime.now())
    assert contradicts("The kettle has finished boiling.", [start])
    assert not contradicts("The kettle has finished boiling.", [done])
    assert not contradicts("The kettle is heating up.", [start])


async def test_something_someone_just_started_is_not_news(hass: HomeAssistant, freezer) -> None:
    from homeassistant.helpers import device_registry as dr, entity_registry as er

    entry = await _setup(hass, notice_entities=["sensor.kettle_status"])
    device = dr.async_get(hass).async_get_or_create(config_entry_id=entry.entry_id, identifiers={("test", "kettle")})
    ents = er.async_get(hass)
    for domain, oid in (("sensor", "kettle_status"), ("switch", "kettle_start")):
        ents.async_get_or_create(domain, "test", oid, suggested_object_id=oid, device_id=device.id)
    hass.states.async_set("sensor.kettle_status", "standby", {"friendly_name": "Kettle"})
    hass.states.async_set("switch.kettle_start", "off")
    await hass.async_block_till_done()
    said = async_mock_service(hass, "assist_satellite", "announce")
    ai = _ai(hass, {"speak": True, "message": "The kettle has finished boiling."})
    freezer.tick(timedelta(minutes=5))
    # the kettle's switch is pressed, then it starts heating: they know, so no AI and nothing said
    hass.states.async_set("switch.kettle_start", "on")
    hass.states.async_set("sensor.kettle_status", "heating", {"friendly_name": "Kettle"})
    await _settle(hass, freezer)
    assert not ai
    # finishing is news
    freezer.tick(timedelta(minutes=4))
    hass.states.async_set("sensor.kettle_status", "standby", {"friendly_name": "Kettle"})
    await _settle(hass, freezer)
    assert len(ai) == 1 and said
