"""Setup, services, memory and the shared-brain LLM API."""

from homeassistant.core import HomeAssistant, ServiceCall
from homeassistant.helpers import area_registry as ar
from homeassistant.helpers import entity_registry as er
from homeassistant.helpers import llm
from pytest_homeassistant_custom_component.common import MockConfigEntry, async_mock_service

from custom_components.sentient.const import DOMAIN

OPTIONS = {
    "persona": "Nova",
    "speakers": ["assist_satellite.kitchen", "assist_satellite.hallway"],
    "default_speaker": "assist_satellite.hallway",
    "quiet_start": "00:00:00",
    "quiet_end": "00:00:00",  # quiet hours disabled for tests
    "motion_window": 3,
    "banned_words": "sir",
    "suppress_entities": [],
    "suppress_seconds": 60,
    "prompt_memories": 10,
}


async def _setup(hass: HomeAssistant, options=None) -> MockConfigEntry:
    entry = MockConfigEntry(domain=DOMAIN, title="Nova", data=options or OPTIONS, unique_id=DOMAIN)
    entry.add_to_hass(hass)
    assert await hass.config_entries.async_setup(entry.entry_id)
    await hass.async_block_till_done()
    return entry


async def test_setup_creates_sensors(hass: HomeAssistant) -> None:
    await _setup(hass)
    assert hass.states.get("sensor.nova_memories").state == "0"
    assert hass.states.get("sensor.nova_last_spoken") is not None


async def test_remember_recall_forget(hass: HomeAssistant) -> None:
    entry = await _setup(hass)
    res = await hass.services.async_call(
        DOMAIN, "remember", {"text": "The rabbits live in the hutch by the shed"},
        blocking=True, return_response=True,
    )
    assert res["saved"].startswith("The rabbits")
    # near-duplicate replaces rather than piles up
    await hass.services.async_call(
        DOMAIN, "remember", {"text": "The rabbits live in the hutch by the shed."}, blocking=True
    )
    await hass.async_block_till_done()
    mem = entry.runtime_data.memory
    assert len(mem.items) == 1
    assert hass.states.get("sensor.nova_memories").state == "1"
    assert mem.search("where are the rabbit")[0].text.startswith("The rabbits")
    res = await hass.services.async_call(
        DOMAIN, "forget", {"what": "rabbits hutch"}, blocking=True, return_response=True
    )
    assert res["forgotten"]
    assert not mem.items


async def test_say_follows_motion_and_cleans(hass: HomeAssistant) -> None:
    await _setup(hass)
    calls: list[ServiceCall] = async_mock_service(hass, "assist_satellite", "announce")
    areas = ar.async_get(hass)
    kitchen = areas.async_create("Kitchen")
    ents = er.async_get(hass)
    for eid, uid in (("assist_satellite.kitchen", "k"), ("binary_sensor.kitchen_motion", "km")):
        domain, obj = eid.split(".")
        entry = ents.async_get_or_create(domain, "test", uid, suggested_object_id=obj)
        ents.async_update_entity(entry.entity_id, area_id=kitchen.id)
    hass.states.async_set("assist_satellite.kitchen", "idle")
    hass.states.async_set("assist_satellite.hallway", "idle")
    hass.states.async_set("binary_sensor.kitchen_motion", "on", {"device_class": "motion"})

    res = await hass.services.async_call(
        DOMAIN, "say", {"message": "Sir, the washing has finished."}, blocking=True, return_response=True
    )
    assert res["spoken"] is True
    assert res["speakers"] == ["assist_satellite.kitchen"]
    assert calls[-1].data["message"] == "The washing has finished."

    # same line again inside two minutes is dropped
    res = await hass.services.async_call(
        DOMAIN, "say", {"message": "The washing has finished."}, blocking=True, return_response=True
    )
    assert res == {"spoken": False, "reason": "duplicate", "message": "The washing has finished.", "speakers": []}

    # no motion anywhere -> default speaker
    hass.states.async_set("binary_sensor.kitchen_motion", "off", {"device_class": "motion"})
    from datetime import timedelta
    from homeassistant.util import dt as dt_util
    from unittest.mock import patch
    later = dt_util.utcnow() + timedelta(minutes=10)
    with patch("homeassistant.util.dt.utcnow", return_value=later):
        res = await hass.services.async_call(
            DOMAIN, "say", {"message": "Bins go out tonight."}, blocking=True, return_response=True
        )
    assert res["speakers"] == ["assist_satellite.hallway"]


async def test_quiet_hours_hold_unless_urgent(hass: HomeAssistant) -> None:
    await _setup(hass, {**OPTIONS, "quiet_start": "00:00:00", "quiet_end": "23:59:59"})
    calls = async_mock_service(hass, "assist_satellite", "announce")
    hass.states.async_set("assist_satellite.hallway", "idle")
    res = await hass.services.async_call(DOMAIN, "say", {"message": "Hello"}, blocking=True, return_response=True)
    assert res["reason"] == "quiet_hours" and not calls
    res = await hass.services.async_call(
        DOMAIN, "say", {"message": "Smoke alarm in the kitchen", "priority": "urgent"},
        blocking=True, return_response=True,
    )
    assert res["spoken"] and len(calls) == 1
    log = hass.states.get("sensor.nova_last_spoken").attributes["log"]
    assert log[0]["said"] == "Smoke alarm in the kitchen" and log[1]["reason"] == "quiet_hours"


async def test_llm_api_shares_memory(hass: HomeAssistant) -> None:
    entry = await _setup(hass)
    entry.runtime_data.memory.add("Conah prefers the kitchen light at 60 percent in the evening", "preference")
    ctx = llm.LLMContext(platform="test", context=None, language="en", assistant="conversation", device_id=None)
    api = await llm.async_get_api(hass, "sentient", ctx)
    assert "Nova" in api.api_prompt
    assert "kitchen light at 60 percent" in api.api_prompt
    assert '"sir"' in api.api_prompt
    names = {t.name for t in api.tools}
    assert {"sentient_remember", "sentient_recall", "sentient_forget", "sentient_announce"} <= names
    out = await api.async_call_tool(llm.ToolInput(tool_name="sentient_remember", tool_args={"text": "The boys go to bed at 9"}))
    assert out["saved"] == "The boys go to bed at 9"
    out = await api.async_call_tool(llm.ToolInput(tool_name="sentient_recall", tool_args={"query": "boys bed"}))
    assert out["memories"][0]["text"] == "The boys go to bed at 9"
