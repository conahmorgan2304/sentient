"""Briefings and the weekly memory tidy."""

from homeassistant.core import HomeAssistant, ServiceCall, SupportsResponse
from homeassistant.helpers import llm
from pytest_homeassistant_custom_component.common import MockConfigEntry, async_mock_service

from custom_components.sentient.const import DOMAIN

OPTIONS = {
    "persona": "Nova",
    "speakers": ["assist_satellite.kitchen"],
    "default_speaker": "assist_satellite.kitchen",
    "quiet_start": "00:00:00",
    "quiet_end": "00:00:00",
    "notice_entities": ["binary_sensor.bedroom_window", "binary_sensor.back_door"],
}


async def _setup(hass: HomeAssistant) -> MockConfigEntry:
    entry = MockConfigEntry(domain=DOMAIN, title="Nova", data=OPTIONS, unique_id=DOMAIN)
    entry.add_to_hass(hass)
    assert await hass.config_entries.async_setup(entry.entry_id)
    await hass.async_block_till_done()
    hass.states.async_set("assist_satellite.kitchen", "idle")
    hass.states.async_set("binary_sensor.bedroom_window", "on", {"device_class": "window", "friendly_name": "Bedroom window"})
    hass.states.async_set("binary_sensor.back_door", "off", {"device_class": "door", "friendly_name": "Back door"})
    hass.states.async_set("weather.home", "rainy", {"temperature": 9, "temperature_unit": "°C"})
    return entry


def _ai(hass: HomeAssistant, reply):
    calls: list[ServiceCall] = []

    async def generate(call: ServiceCall):
        calls.append(call)
        return {"conversation_id": "x", "data": reply}

    hass.services.async_register("ai_task", "generate_data", generate, supports_response=SupportsResponse.ONLY)
    return calls


async def test_briefing_facts_and_tool(hass: HomeAssistant) -> None:
    await _setup(hass)
    await hass.services.async_call(DOMAIN, "remind", {"message": "Put the bins out.", "minutes": 30}, blocking=True)
    facts = await hass.services.async_call(DOMAIN, "briefing", {"speak": False}, blocking=True, return_response=True)
    assert facts["weather"] == "rainy, 9°C"
    assert facts["open_now"] == ["Bedroom window"]
    assert facts["reminders_today"][0].startswith("Put the bins out. at ")
    ctx = llm.LLMContext(platform="test", context=None, language="en", assistant="conversation", device_id=None)
    api = await llm.async_get_api(hass, "sentient", ctx)
    out = await api.async_call_tool(llm.ToolInput(tool_name="sentient_briefing", tool_args={}))
    assert out["open_now"] == ["Bedroom window"]


async def test_spoken_briefing(hass: HomeAssistant) -> None:
    await _setup(hass)
    said = async_mock_service(hass, "assist_satellite", "announce")
    ai = _ai(hass, "It's raining and the bedroom window is open. Nothing else to report.")
    res = await hass.services.async_call(DOMAIN, "briefing", {}, blocking=True, return_response=True)
    assert res["spoken"] and said[-1].data["message"].startswith("It's raining")
    assert "Bedroom window" in ai[0].data["instructions"]


async def test_tidy_merges_and_removes_within_limits(hass: HomeAssistant) -> None:
    entry = await _setup(hass)
    mem = entry.runtime_data.memory
    texts = [
        "The bins go out on Thursday nights.", "Bins: Thursday evening, put them out.",
        "There are two cats.", "The cats are called Milo and Luna.", "object in the shed has all its portions",
        "The kitchen light is best at 60 percent in the evening.", "Conah has a Yamaha FZ6.",
        "The rabbits live in the hutch by the shed.", "Dinner is usually at six.",
    ]
    ids = [mem.add(t).id for t in texts]
    _ai(hass, {
        "remove": f"{ids[4]}, nonsense-id",
        "merge": f"{ids[2]},{ids[3]} => There are two cats, Milo and Luna.\n{ids[0]},{ids[1]} => The bins go out on Thursday nights.",
    })
    res = await hass.services.async_call(DOMAIN, "tidy_memories", {}, blocking=True, return_response=True)
    assert res["result"] == "merged 2, removed 3"
    remaining = {m.text for m in mem.items}
    assert "There are two cats, Milo and Luna." in remaining
    assert "object in the shed has all its portions" not in remaining
    assert len(remaining) == 6


async def test_tidy_never_removes_too_much(hass: HomeAssistant) -> None:
    entry = await _setup(hass)
    mem = entry.runtime_data.memory
    ids = [mem.add(f"Fact number {n} about {w}").id for n, w in enumerate(
        ["apples", "boats", "clocks", "doors", "eggs", "fields", "gates", "hills", "inks"])]
    _ai(hass, {"remove": ",".join(ids)})
    res = await hass.services.async_call(DOMAIN, "tidy_memories", {}, blocking=True, return_response=True)
    assert res["result"] == "merged 0, removed 3"  # a third of nine at most
    assert len(mem.items) == 6
