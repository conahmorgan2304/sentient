"""Regression coverage for reminders, camera history and announcement isolation."""

import asyncio
from datetime import timedelta
from types import SimpleNamespace
from unittest.mock import patch

import pytest
from homeassistant.core import Context
from homeassistant.helpers import device_registry as dr, entity_registry as er, llm
from homeassistant.util import dt as dt_util
from pytest_homeassistant_custom_component.common import MockConfigEntry

from custom_components.sentient.camsecurity import camera_for
from custom_components.sentient.instant import async_answer
from custom_components.sentient.security_events import SecurityEvents
from custom_components.sentient.security_voice import CameraActivityTool, camera_named, checked_history_reply
from custom_components.sentient.selftest import Question, SelfTest, _bins_check, failed_tool_reasons, yes_no
from custom_components.sentient.speech import SpeechRouter


def _frigate(hass):
    entry = MockConfigEntry(domain="frigate", data={"url": "http://frigate:5000"})
    entry.add_to_hass(hass)
    return entry


def _camera(hass, entry, name, label, *, object_id=None, aliases=()):
    registry = er.async_get(hass)
    camera = registry.async_get_or_create("camera", "frigate", f"{entry.entry_id}:camera:{name}",
                                          config_entry=entry, suggested_object_id=object_id or name)
    registry.async_update_entity(camera.entity_id, aliases=set(aliases))
    hass.states.async_set(camera.entity_id, "idle", {"friendly_name": label})
    return camera.entity_id


async def test_live_zone_metadata_links_only_unique_frigate_camera(hass, aioclient_mock):
    entry = _frigate(hass)
    gate = _camera(hass, entry, "entrance", "Entrance Camera", object_id="renamed")
    _camera(hass, entry, "path", "Path")
    zone = dr.async_get(hass).async_get_or_create(config_entry_id=entry.entry_id,
                                                 identifiers={("frigate", f"{entry.entry_id}:zone1")})
    sensor = er.async_get(hass).async_get_or_create("binary_sensor", "frigate", f"{entry.entry_id}:occupancy_sensor:Zone1_person",
                                                    config_entry=entry, device_id=zone.id)
    hass.states.async_set("camera.zone1", "idle", {"friendly_name": "Zone1"})
    events = SecurityEvents(hass, "test", {}, lambda: None)
    assert camera_for(hass, sensor.entity_id) is None
    aioclient_mock.get("http://frigate:5000/api/config", json={"cameras": {
        "entrance": {"zones": {"Zone1": {}}}, "path": {"zones": {"Other": {}}}}})
    await events._async_zoned()
    assert camera_for(hass, sensor.entity_id) == gate
    aioclient_mock.clear_requests()
    aioclient_mock.get("http://frigate:5000/api/config", json={"cameras": {
        "entrance": {"zones": {"Zone1": {}}}, "path": {"zones": {"Zone1": {}}}}})
    await events._async_zoned()
    assert camera_for(hass, sensor.entity_id) is None
    events.async_stop()


async def test_camera_history_checks_permissions_aliases_and_renamed_frigate_camera(hass, aioclient_mock):
    entry = _frigate(hass)
    camera = _camera(hass, entry, "porch", "Porch Entrance Camera", object_id="renamed",
                     aliases={"doorbell"})
    other = _camera(hass, entry, "gate", "Gate Camera", aliases={"doorbell"})
    hass.states.async_set("camera.porch_hd_stream", "idle", {"friendly_name": "Porch Entrance HD Stream"})
    assert camera_named(hass, "doorbell") is None
    assert camera_named(hass, "Porch Entrance Camera") == camera
    assert camera_named(hass, "Porch Entrance Camera camera") == camera
    assert camera_named(hass, other) == other
    options = {"security_event_evidence": True, "door_camera": camera}
    rt = SimpleNamespace(spots=None)
    rt.security_events = SecurityEvents(hass, "test", options, lambda: rt)
    now = dt_util.utcnow()
    tool = CameraActivityTool()
    context = llm.LLMContext(platform="test", context=Context(), language="en", assistant=None, device_id=None)

    async def query(target, allowed):
        with patch("custom_components.sentient.security_voice._runtime", return_value=rt), \
                patch("custom_components.sentient.security_voice.allowed", return_value=allowed):
            result = await tool.async_call(hass, llm.ToolInput(tool.name, {"camera": target, "hours": 2, "label": "person"}), context)
            return getattr(result, "data", result)

    assert (await query("doorbell", True))["reason"] == "camera_unresolved"
    assert (await query(other, True))["reason"] == "camera_not_configured"
    assert (await query(camera, False))["reason"] == "camera_access"
    assert not aioclient_mock.mock_calls
    aioclient_mock.get("http://frigate:5000/api/events", status=503)
    assert (await query(camera, True))["reason"] == "evidence_unavailable"
    aioclient_mock.clear_requests()
    aioclient_mock.get("http://frigate:5000/api/events", json=[])
    checked = await query(camera, True)
    assert checked["visits"] == [] and checked["source"] == "Frigate event history"
    assert aioclient_mock.mock_calls[-1][1].query["cameras"] == "porch"

    rt.security_events.ingest({"id": "recent-person", "camera": "porch", "label": "person",
                               "start_time": now.timestamp() - 2, "end_time": now.timestamp() - 1})
    checked = await query(camera, True)
    assert checked["camera_id"] == camera and checked["visits"][0]["seen"] == ["person"]


async def test_coming_up_separates_reminders_from_alarms_in_both_answer_paths(hass):
    from custom_components.sentient.local_answers import async_answer as offline

    today = dt_util.utcnow()
    rt = SimpleNamespace(
        options={"instant_answers": True}, speech=SimpleNamespace(guest_mode=False),
        alarms=SimpleNamespace(listing=lambda: [{"name": "Screen on", "on": True, "next": today.isoformat()}]),
        watches=SimpleNamespace(watches=[
            SimpleNamespace(kind="reminder", what="Buy milk", until_dt=today + timedelta(hours=2)),
            SimpleNamespace(kind="reminder", what="Call the dentist", until_dt=today + timedelta(hours=1)),
        ]),
    )
    for answer in (async_answer, offline):
        text = await answer(hass, rt, "What reminders have I got?", Context())
        assert text.index("Call the dentist") < text.index("Buy milk")
        assert "Screen on" not in text
        text = await answer(hass, rt, "What is my next alarm?", Context())
        assert "Screen on" in text and "Buy milk" not in text
        text = await answer(hass, rt, "What's coming up?", Context())
        assert "Screen on" in text and "Buy milk" in text
        for command in ("Can you remind me about anything?", "Please set a reminder for Thursday?",
                        "Would you create a reminder for tomorrow?", "When you get a chance, set a reminder?"):
            assert await answer(hass, rt, command, Context()) is None
        rt.speech.guest_mode = True
        assert await answer(hass, rt, "What reminders have I got?", Context()) is None
        rt.speech.guest_mode = False


@pytest.mark.parametrize(("answer", "right"), [
    ("I can't take the bins out myself, but collection is on Friday. Shall I put a reminder on your list?", True),
    ("I can't, but I can remind you to.", True),
    ("I can't take them out. I can do it later.", False),
    ("I can't, but should I move the wheelie bins?", False),
    ("I can't do it myself. I'll wheel them out later.", False),
    ("I can't do it myself. I've already put a reminder on your list.", False),
    ("I can't. Shall I turn on the outside lights?", False),
    ("I can't. Yes, consider it done.", False),
])
def test_physical_refusal_requires_a_supported_alternative(answer, right):
    assert _bins_check(answer) is right


def test_camera_result_requires_matching_scope_and_keeps_redacted_errors():
    cam = "camera.entrance"
    scope = {"camera_id": cam, "hours": 2, "label": "person"}
    broken = {"tool_name": "mcp__sentient_camera_activity", "result": {"error": True, "data": {
        **scope, "error": "Failure at https://example.org/?token=private-token", "reason": "evidence_unavailable"}}}
    alternate = {"tool_name": "frigate_query", "tool_result": {**scope, "count": 0}}
    wrong_scope = {"tool_name": "frigate_query", "tool_result": {**scope, "camera_id": "camera.other", "count": 0}}
    assert failed_tool_reasons([broken]) == {"sentient_camera_activity": "evidence_unavailable"}
    assert checked_history_reply("No one was seen.", [broken, wrong_scope]).startswith("I couldn't verify")
    assert checked_history_reply("No one was seen.", [broken, alternate]).startswith("According to Frigate")
    test = SelfTest.__new__(SelfTest)
    check = lambda answer: (yes_no(answer) is False, "no one")
    q = Question("camera", "", check, "camera", camera_id=cam)
    for answer, results, tools, outcome in (
        ("No one was seen.", [broken], ["sentient_camera_activity"], "failed_evidence_retrieval"),
        ("I couldn't check the camera.", [broken], ["sentient_camera_activity"], "correct_uncertainty"),
        ("I couldn't check the camera. No one was seen.", [broken], ["sentient_camera_activity"], "failed_evidence_retrieval"),
        ("No one was seen.", [broken, alternate], ["sentient_camera_activity", "frigate_query"], "supported_answer_with_tool_error"),
        ("No one was seen.", [wrong_scope], ["frigate_query"], "incorrect_tool_selection"),
        ("No one was seen.", [alternate], ["frigate_query"], "correct_with_evidence"),
        ("Yes, someone was seen.", [alternate], ["frigate_query"], "incorrect_answer"),
    ):
        _right, _expected, reason, errors = test._grade(q, check, answer, tools, results, False)
        assert reason == outcome
        assert "private-token" not in repr(errors)


async def test_slow_satellite_does_not_block_other_announcements_or_retry_after_timeout(hass):
    slow, healthy = "assist_satellite.slow", "assist_satellite.healthy"
    for entity in (slow, healthy):
        hass.states.async_set(entity, "idle")
    router = SpeechRouter(hass, {"speakers": [slow, healthy], "quiet_start": "00:00", "quiet_end": "00:00"})
    slow_started = asyncio.Event()
    slow_exited = asyncio.Event()
    calls = []

    async def announce(call):
        speaker = call.data["entity_id"][0]
        calls.append(speaker)
        if speaker == slow:
            slow_started.set()
            try:
                await asyncio.Future()
            finally:
                slow_exited.set()

    hass.services.async_register("assist_satellite", "announce", announce)
    with patch("custom_components.sentient.speech.DELIVERY_TIMEOUT", 0.2):
        first = asyncio.create_task(router.async_say("A visitor arrived", targets=[slow, healthy]))
        await asyncio.wait_for(slow_started.wait(), timeout=1)
        urgent = await asyncio.wait_for(
            router.async_say("A second visitor arrived", "urgent", [healthy], source="camera security"), timeout=1)
        assert urgent.spoken and not slow_exited.is_set()
        result = await first
        assert [row["status"] for row in result.deliveries] == ["timed_out", "accepted"]
        assert all(row["playback"] == "unverified" for row in result.deliveries)
        retry = await router.async_say("A visitor arrived", targets=[slow, healthy])
        assert retry.reason == "duplicate" and calls.count(slow) == 1
