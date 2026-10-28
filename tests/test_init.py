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


async def _rooms(hass: HomeAssistant, rooms) -> dict[str, str]:
    """Create areas with a satellite and a motion sensor each; returns name -> area id."""
    areas = ar.async_get(hass)
    ents = er.async_get(hass)
    ids = {}
    for room, sat, sensor in rooms:
        area = areas.async_create(room)
        ids[room] = area.id
        for eid in (sat, sensor):
            domain, obj = eid.split(".")
            entry = ents.async_get_or_create(domain, "test", obj, suggested_object_id=obj)
            ents.async_update_entity(entry.entity_id, area_id=area.id)
        hass.states.async_set(sat, "idle")
        hass.states.async_set(sensor, "off", {"device_class": "motion"})
    return ids


ROOMS = (
    ("Kitchen", "assist_satellite.kitchen", "binary_sensor.kitchen_motion"),
    ("Hallway", "assist_satellite.hallway", "binary_sensor.hallway_motion"),
    ("Living Room", "assist_satellite.living", "binary_sensor.living_motion"),
)


class _Clock:
    """Set states 'n minutes ago', then come back to the present."""

    def __init__(self, freezer) -> None:
        from homeassistant.util import dt as dt_util

        self.freezer = freezer
        self.now = dt_util.utcnow()
        freezer.move_to(self.now)

    def at(self, minutes_ago: float) -> None:
        from datetime import timedelta

        self.freezer.move_to(self.now - timedelta(minutes=minutes_ago))

    def back(self) -> None:
        self.freezer.move_to(self.now)


async def test_sitting_still_beats_passer_by(hass: HomeAssistant, freezer) -> None:
    """Kitchen had steady motion that has now cleared; someone just walked down the hall."""
    clock = _Clock(freezer)
    entry = await _setup(hass, {**OPTIONS, "speakers": ["assist_satellite.kitchen", "assist_satellite.hallway"],
                                "default_speaker": "assist_satellite.hallway", "motion_window": 10})
    ids = await _rooms(hass, ROOMS[:2])
    hass.config_entries.async_update_entry(entry, options={**OPTIONS, "motion_window": 10,
        "speakers": ["assist_satellite.kitchen", "assist_satellite.hallway"],
        "default_speaker": "assist_satellite.hallway", "pass_through_areas": [ids["Hallway"]]})
    await hass.async_block_till_done()
    async_mock_service(hass, "assist_satellite", "announce")
    clock.at(6)
    hass.states.async_set("binary_sensor.kitchen_motion", "on", {"device_class": "motion"})
    clock.at(2)
    hass.states.async_set("binary_sensor.kitchen_motion", "off", {"device_class": "motion"})
    clock.at(0.5)
    hass.states.async_set("binary_sensor.hallway_motion", "on", {"device_class": "motion"})
    clock.back()
    res = await hass.services.async_call(DOMAIN, "say", {"message": "Hello"}, blocking=True, return_response=True)
    assert res["speakers"] == ["assist_satellite.kitchen"]
    likely = hass.states.get("sensor.nova_likely_room")
    assert likely is not None


async def test_talking_to_a_speaker_marks_the_room(hass: HomeAssistant, freezer) -> None:
    """Living room motion is busier, but someone just spoke to the kitchen speaker."""
    clock = _Clock(freezer)
    await _setup(hass, {**OPTIONS, "speakers": ["assist_satellite.kitchen", "assist_satellite.living"],
                        "motion_window": 10, "all_occupied": False})
    await _rooms(hass, (ROOMS[0], ROOMS[2]))
    async_mock_service(hass, "assist_satellite", "announce")
    clock.at(4)
    hass.states.async_set("binary_sensor.living_motion", "on", {"device_class": "motion"})
    clock.at(1)
    hass.states.async_set("assist_satellite.kitchen", "listening")
    hass.states.async_set("assist_satellite.kitchen", "idle")
    clock.back()
    res = await hass.services.async_call(DOMAIN, "say", {"message": "Hello"}, blocking=True, return_response=True)
    assert res["speakers"] == ["assist_satellite.kitchen"]


async def test_two_occupied_rooms_both_hear(hass: HomeAssistant, freezer) -> None:
    """Someone in the kitchen and someone in the living room: say it in both."""
    clock = _Clock(freezer)
    await _setup(hass, {**OPTIONS, "speakers": ["assist_satellite.kitchen", "assist_satellite.living"],
                        "motion_window": 10})
    await _rooms(hass, (ROOMS[0], ROOMS[2]))
    calls = async_mock_service(hass, "assist_satellite", "announce")
    clock.at(5)
    hass.states.async_set("binary_sensor.living_motion", "on", {"device_class": "motion"})
    clock.at(4)
    hass.states.async_set("binary_sensor.kitchen_motion", "on", {"device_class": "motion"})
    clock.back()
    res = await hass.services.async_call(DOMAIN, "say", {"message": "Hello"}, blocking=True, return_response=True)
    assert sorted(res["speakers"]) == ["assist_satellite.kitchen", "assist_satellite.living"]
    assert sorted(calls[-1].data["entity_id"]) == ["assist_satellite.kitchen", "assist_satellite.living"]


async def test_backup_speaker_only_when_main_rooms_empty(hass: HomeAssistant, freezer) -> None:
    """Living room is backup-only: used when the kitchen is empty, skipped when it isn't."""
    clock = _Clock(freezer)
    entry = await _setup(hass, {**OPTIONS, "speakers": ["assist_satellite.kitchen", "assist_satellite.living"],
                                "fallback_speakers": ["assist_satellite.living"], "motion_window": 10,
                                "default_speaker": "assist_satellite.kitchen"})
    await _rooms(hass, (ROOMS[0], ROOMS[2]))
    async_mock_service(hass, "assist_satellite", "announce")
    clock.at(5)
    hass.states.async_set("binary_sensor.living_motion", "on", {"device_class": "motion"})
    clock.back()
    assert entry.runtime_data.speech.pick_speaker() == ["assist_satellite.living"]
    clock.at(3)
    hass.states.async_set("binary_sensor.kitchen_motion", "on", {"device_class": "motion"})
    clock.back()
    assert entry.runtime_data.speech.pick_speaker() == ["assist_satellite.kitchen"]


async def test_nobody_anywhere_uses_default(hass: HomeAssistant, freezer) -> None:
    clock = _Clock(freezer)
    await _setup(hass, {**OPTIONS, "speakers": ["assist_satellite.kitchen", "assist_satellite.living"],
                        "default_speaker": "assist_satellite.living", "motion_window": 10})
    await _rooms(hass, (ROOMS[0], ROOMS[2]))
    async_mock_service(hass, "assist_satellite", "announce")
    clock.at(9)
    hass.states.async_set("binary_sensor.kitchen_motion", "on", {"device_class": "motion"})
    clock.at(8.9)
    hass.states.async_set("binary_sensor.kitchen_motion", "off", {"device_class": "motion"})
    clock.back()
    res = await hass.services.async_call(DOMAIN, "say", {"message": "Hello"}, blocking=True, return_response=True)
    assert res["speakers"] == ["assist_satellite.living"]


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
    entry.runtime_data.memory.add("Alex prefers the kitchen light at 60 percent in the evening", "preference")
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


async def test_hush_only_after_real_run(hass: HomeAssistant) -> None:
    """An automation that has never run (e.g. after a restart) must not hush speech."""
    from homeassistant.util import dt as dt_util

    await _setup(hass, {**OPTIONS, "suppress_entities": ["automation.doorbell"]})
    async_mock_service(hass, "assist_satellite", "announce")
    hass.states.async_set("assist_satellite.hallway", "idle")
    hass.states.async_set("automation.doorbell", "on", {"last_triggered": None})
    res = await hass.services.async_call(DOMAIN, "say", {"message": "One"}, blocking=True, return_response=True)
    assert res["spoken"]
    hass.states.async_set("automation.doorbell", "on", {"last_triggered": dt_util.utcnow().isoformat()})
    res = await hass.services.async_call(DOMAIN, "say", {"message": "Two"}, blocking=True, return_response=True)
    assert res["reason"] == "hushed_by:automation.doorbell"


async def test_restart_remembers_from_history(recorder_mock, hass: HomeAssistant, freezer) -> None:
    """After a restart the scores are rebuilt from the recorder, not from restart time."""
    from homeassistant.components.recorder.util import get_instance
    from pytest_homeassistant_custom_component.components.recorder.common import async_wait_recording_done

    clock = _Clock(freezer)
    await _rooms(hass, (ROOMS[0], ROOMS[2]))
    clock.at(6)
    hass.states.async_set("binary_sensor.kitchen_motion", "on", {"device_class": "motion"})
    clock.at(1)
    hass.states.async_set("binary_sensor.kitchen_motion", "off", {"device_class": "motion"})
    clock.back()
    await async_wait_recording_done(hass)
    entry = await _setup(hass, {**OPTIONS, "speakers": ["assist_satellite.kitchen", "assist_satellite.living"],
                                "default_speaker": "assist_satellite.living", "motion_window": 10})
    assert entry.runtime_data.tracker.scores().get(entry.runtime_data.speech._area_of("assist_satellite.kitchen"), 0) > 100
    assert get_instance(hass) is not None


async def test_diagnostics_redacts_personal_bits(hass: HomeAssistant) -> None:
    from custom_components.sentient.diagnostics import async_get_config_entry_diagnostics

    entry = await _setup(hass, {**OPTIONS, "house_rules": "Never say the house is empty."})
    entry.runtime_data.memory.add("Alex likes tea", "preference")
    diag = await async_get_config_entry_diagnostics(hass, entry)
    assert diag["options"]["house_rules"] == "**REDACTED**"
    assert diag["options"]["persona"] == "**REDACTED**"
    assert diag["memory"] == {"count": 1, "categories": ["preference"]}
    assert "Alex" not in str(diag)


async def test_said_log_survives_restart(hass: HomeAssistant) -> None:
    from homeassistant.core import State
    from pytest_homeassistant_custom_component.common import mock_restore_cache

    mock_restore_cache(hass, [State("sensor.nova_last_spoken", "The washing's done.",
                                    {"log": [{"time": "2026-10-21T10:00:00+01:00", "said": "The washing's done."}]})])
    await _setup(hass)
    st = hass.states.get("sensor.nova_last_spoken")
    assert st.state == "The washing's done." and st.attributes["log"][0]["said"] == "The washing's done."
