"""Exact event evidence, bounded reuse and private conversational references."""
import asyncio
from datetime import timedelta
from types import SimpleNamespace
from unittest.mock import AsyncMock, Mock, patch

import pytest
from homeassistant.core import Context
from homeassistant.helpers import llm
from homeassistant.util import dt as dt_util
from pytest_homeassistant_custom_component.common import MockConfigEntry

from custom_components.sentient.clip_analysis import ClipAnalysis
from custom_components.sentient.security_events import SecurityEvents, normal_event
from custom_components.sentient.sessions import VoiceSession, current_session


def setup(hass, **options):
    opts = {"security_event_evidence": True, "security_conversation_context": True,
            "security_event_feedback": True, "share_clip_analysis": True, "clip_cameras": ["camera.garden"], **options}
    speech = SimpleNamespace(_privacy_generation=0, _guest_generation=0, guest_mode=False)
    rt = SimpleNamespace(options=opts, speech=speech, ai=SimpleNamespace(_generation=0), incidents=None,
                         watches=SimpleNamespace(spend=Mock(return_value=True)))
    events = rt.security_events = SecurityEvents(hass, "test", opts, lambda: rt)
    cache = rt.clip_analysis = ClipAnalysis(lambda: rt)
    hass.states.async_set("camera.garden", "idle", {"friendly_name": "Garden"})
    return events, cache, rt


def event(**extra):
    now = dt_util.utcnow().timestamp()
    return {"id": "123.event", "camera": "garden", "start_time": now - 10, "end_time": now - 1,
            "label": "person", "has_snapshot": True, "has_clip": True, **extra}


def context(user=None, device="speaker"):
    return llm.LLMContext(platform="test", context=Context(user_id=user), device_id=device, assistant=None, language="en")


@pytest.mark.parametrize("bad", [None, [], {}, {"id": "../bad"}, {"camera": "../../camera"},
                                 {"start_time": float("nan")}, {"end_time": float("inf")},
                                 {"start_time": -1}, {"end_time": 1}, {"start_time": 1e300}])
async def test_invalid_event_payloads_do_not_create_evidence(hass, bad):
    obj, _, _ = setup(hass)
    data = {**event(), **bad} if isinstance(bad, dict) and bad else bad
    assert normal_event(data) is None
    assert obj.ingest(data) is None
    assert not obj.items


async def test_event_id_cannot_be_rebound_or_reopened(hass):
    obj, _, _ = setup(hass)
    original = event()
    row = obj.ingest(original)
    obj.feedback(row["id"], "cat")
    assert obj.ingest({**original, "end_time": None})["end"] == row["end"]
    assert obj.ingest({**original, "camera": "other"}) is None
    assert obj.items[row["id"]]["camera"] == "garden"
    assert obj.items[row["id"]]["feedback"]["value"] == "cat"


async def test_unique_candidate_is_not_identity_proof_and_multiple_candidates_are_ambiguous(hass):
    obj, _, _ = setup(hass)
    obj.ingest(event())
    row = await obj.async_match("camera.garden", dt_util.utcnow())
    assert row["id"] == "123.event"
    obj.alert(row["id"], "binary_sensor.garden_person", "night", "Person reported", "incident")
    assert "candidate" in obj.items[row["id"]]["alerts"][0]["link"]
    obj.ingest(event(id="124.event"))
    assert await obj.async_match("camera.garden", dt_util.utcnow()) is None


async def test_rest_matching_checks_camera_time_and_label(hass, aioclient_mock):
    obj, _, _ = setup(hass)
    MockConfigEntry(domain="frigate", data={"url": "http://frigate:5000"}).add_to_hass(hass)
    aioclient_mock.get("http://frigate:5000/api/events", json=[event(id="cat", label="cat"), event(id="other", camera="other"), event()])
    assert (await obj.async_match("camera.garden", dt_util.utcnow()))["id"] == "123.event"


@pytest.mark.parametrize("change", ["disable", "privacy", "guest", "stop"])
async def test_late_exact_event_fetch_cannot_repopulate_evidence(hass, change):
    obj, _, rt = setup(hass)
    MockConfigEntry(domain="frigate", data={"url": "http://frigate:5000"}).add_to_hass(hass)
    entered, release = asyncio.Event(), asyncio.Event()

    async def get(*_, **__):
        entered.set()
        await release.wait()
        return SimpleNamespace(status=200, json=AsyncMock(return_value=event()))

    with patch("custom_components.sentient.security_events.async_get_clientsession", return_value=SimpleNamespace(get=get)):
        task = asyncio.create_task(obj.async_event("camera.garden", "123.event"))
        await entered.wait()
        if change == "disable": obj.configure({**obj.options, "security_event_evidence": False, "security_conversation_context": False, "security_event_feedback": False})
        elif change == "privacy": rt.speech._privacy_generation += 1
        elif change == "guest": rt.speech._guest_generation += 1
        else: obj.async_stop()
        release.set()
        assert await task is None
    assert not obj.items


async def test_event_snapshot_is_exact_bounded_and_temporary(hass, aioclient_mock, tmp_path):
    obj, _, rt = setup(hass)
    hass.config.media_dirs = {"local": str(tmp_path)}
    MockConfigEntry(domain="frigate", data={"url": "http://frigate:5000"}).add_to_hass(hass)
    row = obj.ingest(event())
    aioclient_mock.get("http://frigate:5000/api/events/123.event/snapshot.jpg", content=b"\xff\xd8jpeg")
    from custom_components.sentient.camsecurity import CameraSecurity
    camera = CameraSecurity(hass, {}, lambda: rt)
    generate = AsyncMock(return_value={"data": {"person": True, "description": "A person stood at the gate"}})
    with patch("custom_components.sentient.camsecurity.async_generate", generate), \
            patch("custom_components.sentient.camwake.async_wake", AsyncMock()) as wake:
        assert (await camera._describe("camera.garden", lambda: True, row))[0] is True
        wake.assert_not_awaited()
    attachment = generate.call_args.args[1]["attachments"][0]["media_content_id"]
    assert "security_event_" in attachment and "media-source://camera/" not in attachment
    assert row["_snapshot_used"] is True
    assert not list((tmp_path / "sentient").glob("security_event_*"))


async def test_html_snapshot_response_is_not_sent_as_image(hass, aioclient_mock, tmp_path):
    obj, _, _ = setup(hass)
    hass.config.media_dirs = {"local": str(tmp_path)}
    MockConfigEntry(domain="frigate", data={"url": "http://frigate:5000"}).add_to_hass(hass)
    aioclient_mock.get("http://frigate:5000/api/events/123.event/snapshot.jpg", content=b"<html>proxy error</html>")
    assert await obj.async_snapshot(obj.ingest(event())) is None


async def test_feedback_is_reversible_and_counts_only_reviewed_events(hass):
    obj, _, _ = setup(hass)
    for i in range(4):
        obj.ingest(event(id=f"event-{i}"))
    for i in range(3):
        assert obj.feedback(f"event-{i}", "cat")["changed_detector_settings"] is False
    summary = obj.feedback_summary()[0]
    assert summary["reviewed"] == 3 and summary["cat"] == 3 and summary["suggestion"]
    assert "not a measured false-positive rate" in summary["limits"]
    assert obj.feedback("event-0", "clear")["feedback"] is None
    assert obj.feedback_summary()[0]["reviewed"] == 2
    assert obj.feedback_summary()[0]["suggestion"] is None


async def test_switches_hide_feedback_links_and_context_without_erasing_retained_events(hass):
    obj, _, _ = setup(hass)
    row = obj.ingest(event())
    obj.feedback(row["id"], "cat")
    obj.focus(("conv", "device", None, None), row["id"])
    obj.configure({**obj.options, "security_event_evidence": False, "security_conversation_context": False, "security_event_feedback": False})
    assert not obj.recent() and not obj.feedback_summary() and not obj._focus
    assert obj.feedback(row["id"], "useful").get("error")
    assert row["id"] in obj.items


async def test_forgetting_removes_recent_feedback_on_an_older_event(hass, freezer):
    obj, cache, _ = setup(hass)
    obj.ingest(event())
    freezer.tick(timedelta(days=3))
    obj.feedback("123.event", "cat")
    obj.focus(("conv", "device", None, None), "123.event")
    assert obj.forget(dt_util.utcnow() - timedelta(hours=48)) == 1
    assert not obj.items and not obj._focus


async def test_shared_analysis_coalesces_and_returns_independent_values(hass):
    _, cache, _ = setup(hass)
    entered, release = asyncio.Event(), asyncio.Event()

    async def produce():
        entered.set()
        await release.wait()
        return {"summary": "A person left a parcel", "kind": "delivery", "worth": True}

    producer = AsyncMock(side_effect=produce)
    task = asyncio.create_task(cache.analyse("garden", "123.event", 1, 20, "ai.main", "neutral", producer))
    await entered.wait()
    other = asyncio.create_task(cache.analyse("camera.garden", "123.event", 1, 20, "ai.main", "neutral", producer))
    await asyncio.sleep(0)
    release.set()
    first, second = await asyncio.gather(task, other)
    first["summary"] = "modified by caller"
    assert second["summary"] == "A person left a parcel"
    assert (await cache.analyse("garden", "123.event", 1, 20, "ai.main", "neutral", producer))["summary"] == second["summary"]
    assert producer.await_count == 1 and cache.joined == 1 and cache.hits == 1


@pytest.mark.parametrize("difference", ["event", "camera", "provider", "coverage", "identity"])
async def test_incompatible_clip_analyses_are_not_shared(hass, difference):
    _, cache, _ = setup(hass)
    producer = AsyncMock(return_value={"summary": "A person arrived", "kind": "visitor", "worth": False})
    args = ["garden", "123.event", 1, 20, "ai.main", "neutral"]
    await cache.analyse(*args, producer)
    if difference == "event": args[1] = "other"
    elif difference == "camera": args[0] = "gate"
    elif difference == "provider": args[4] = "ai.other"
    elif difference == "coverage": args[3] = 30
    else: args[5] = "household"
    await cache.analyse(*args, producer)
    assert producer.await_count == 2


async def test_cache_clear_discards_an_inflight_result(hass):
    _, cache, _ = setup(hass)
    entered = asyncio.Event()

    async def produce():
        entered.set()
        await asyncio.Event().wait()

    task = asyncio.create_task(cache.analyse("garden", "event", 1, 20, None, "neutral", produce))
    await entered.wait()
    cache.clear()
    assert await task is None
    assert not cache._cache and not cache._pending


async def test_cancelled_waiter_does_not_cancel_another_waiters_analysis(hass):
    _, cache, _ = setup(hass)
    entered, release = asyncio.Event(), asyncio.Event()

    async def produce():
        entered.set()
        await release.wait()
        return {"summary": "A visitor arrived", "kind": "visitor", "worth": False}

    first = asyncio.create_task(cache.analyse("garden", "event", 1, 20, None, "neutral", produce))
    await entered.wait()
    second = asyncio.create_task(cache.analyse("garden", "event", 1, 20, None, "neutral", produce))
    await asyncio.sleep(0)
    first.cancel()
    with pytest.raises(asyncio.CancelledError): await first
    release.set()
    assert (await second)["summary"] == "A visitor arrived"


async def test_cache_expiry_and_off_switch(hass, freezer):
    _, cache, rt = setup(hass)
    producer = AsyncMock(return_value={"summary": "A person arrived", "kind": "visitor", "worth": False})
    await cache.analyse("garden", "event", 1, 20, None, "neutral", producer)
    freezer.tick(timedelta(hours=7))
    await cache.analyse("garden", "event", 1, 20, None, "neutral", producer)
    assert producer.await_count == 2
    rt.options["share_clip_analysis"] = False
    await cache.analyse("garden", "event", 1, 20, None, "neutral", producer)
    assert producer.await_count == 3


@pytest.mark.parametrize("change", ["device", "conversation", "user", "expired", "closed", "privacy", "guest", "disabled", "unexposed"])
async def test_security_focus_cannot_leak_to_another_owner_or_context(hass, freezer, change):
    from custom_components.sentient.security_voice import SecurityEventTool
    obj, _, rt = setup(hass)
    obj.ingest(event())
    session = VoiceSession("conversation", ("speaker", None, None), dt_util.utcnow())
    token = current_session.set(session)
    exposure = True
    try:
        with patch("custom_components.sentient.security_voice._runtime", return_value=rt), \
                patch("custom_components.sentient.security_voice.async_should_expose", return_value=True):
            result = await SecurityEventTool().async_call(hass, llm.ToolInput(tool_name="sentient_security_event", tool_args={"event_id": "123.event"}), context())
            assert result["event"]["id"] == "123.event"
        ctx = context()
        if change == "device": ctx = context(device="other_speaker")
        elif change == "conversation": session.id = "other_conversation"
        elif change == "user": session.owner = ("speaker", None, "other")
        elif change == "expired": freezer.tick(timedelta(minutes=6))
        elif change == "closed": session.closed = True
        elif change == "privacy": rt.speech._privacy_generation += 1
        elif change == "guest": rt.speech.guest_mode = True
        elif change == "disabled": obj.options["security_conversation_context"] = False
        else: exposure = False
        with patch("custom_components.sentient.security_voice._runtime", return_value=rt), \
                patch("custom_components.sentient.security_voice.async_should_expose", return_value=exposure):
            result = await SecurityEventTool().async_call(hass, llm.ToolInput(tool_name="sentient_security_event", tool_args={"action": "current"}), ctx)
        assert "event_id" not in result
    finally:
        current_session.reset(token)


async def test_current_question_reads_current_reports_and_does_not_infer_identity(hass):
    from custom_components.sentient.security_voice import SecurityEventTool
    obj, _, rt = setup(hass, camera_security_sensors=["binary_sensor.garden_person"])
    obj.ingest(event())
    hass.states.async_set("binary_sensor.garden_person", "off", {"friendly_name": "Garden Person"})
    with patch("custom_components.sentient.security_voice._runtime", return_value=rt), \
            patch("custom_components.sentient.security_voice.async_should_expose", return_value=True):
        result = await SecurityEventTool().async_call(hass, llm.ToolInput(tool_name="sentient_security_event", tool_args={"event_id": "123.event", "action": "current"}), context())
    assert result["current_reports"][0]["state"] == "off"
    assert "cannot prove" in result["limits"] and "same person" in result["limits"]


@pytest.mark.parametrize("first", ["automatic", "question"])
async def test_both_clip_paths_share_one_compatible_analysis(hass, first):
    from tests.test_phase3 import _setup
    hass.states.async_set("camera.garden", "idle")
    entry = await _setup(hass, share_clip_analysis=True, clip_watch=True, clip_cameras=["camera.garden"], event_clips=True,
                         security_event_evidence=True)
    rt = entry.runtime_data
    raw = event()
    rt.security_events.ingest(raw)
    review = {**raw, "id": "review", "severity": "alert", "data": {"detections": [raw["id"]]}}
    with patch.object(rt.clipwatch, "_ask_ai", AsyncMock(return_value={"summary": "A person left a parcel", "kind": "delivery", "worth": True})) as model, \
            patch.object(rt.watches, "spend", return_value=True) as spend:
        if first == "automatic":
            assert "recorded" in await rt.clipwatch.async_watch(review)
            assert (await rt.clips.async_what_happened("camera.garden", event_id=raw["id"]))["what_happened"] == "A person left a parcel"
        else:
            assert "what_happened" in await rt.clips.async_what_happened("camera.garden", event_id=raw["id"])
            assert "recorded" in await rt.clipwatch.async_watch(review)
    assert model.await_count == 1 and spend.call_count == 1
    assert rt.clip_analysis.hits == 1


async def test_custom_clip_question_bypasses_generic_cache(hass):
    from tests.test_phase3 import _setup
    hass.states.async_set("camera.garden", "idle")
    entry = await _setup(hass, share_clip_analysis=True, clip_watch=True, clip_cameras=["camera.garden"], event_clips=True,
                         security_event_evidence=True)
    rt = entry.runtime_data
    raw = event()
    rt.security_events.ingest(raw)
    rt.security_events.analysis(raw["id"], "garden", {"summary": "A visitor arrived"})
    with patch.object(rt.clips, "async_save_clip", AsyncMock(return_value="media-source://clip.mp4")), \
            patch("custom_components.sentient.clips.async_generate", AsyncMock(return_value={"data": "A red coat"})) as model:
        assert (await rt.clips.async_what_happened("camera.garden", question="What colour was the coat?", event_id=raw["id"]))["what_happened"] == "A red coat"
    assert model.await_count == 1 and rt.clip_analysis.hits == 0
    assert rt.security_events.items[raw["id"]]["analysis"]["summary"] == "A visitor arrived"


async def test_feedback_websocket_requires_admin_and_enabled_setting(hass, hass_ws_client, hass_read_only_user):
    from tests.test_phase3 import _setup
    entry = await _setup(hass, security_event_feedback=True, clip_cameras=["camera.garden"])
    entry.runtime_data.security_events.ingest(event())
    ws = await hass_ws_client(hass)
    await ws.send_json({"id": 1, "type": "sentient/security/event/feedback", "event_id": "123.event", "value": "cat"})
    result = await ws.receive_json()
    assert result["success"] and result["result"]["feedback"]["value"] == "cat"
    entry.runtime_data.security_events.options["security_event_feedback"] = False
    await ws.send_json({"id": 2, "type": "sentient/security/event/feedback", "event_id": "123.event", "value": "useful"})
    assert not (await ws.receive_json())["success"]
    refresh = await hass.auth.async_create_refresh_token(hass_read_only_user, client_id="https://test.example")
    other = await hass_ws_client(hass, hass.auth.async_create_access_token(refresh))
    await other.send_json({"id": 1, "type": "sentient/security/event/feedback", "event_id": "123.event", "value": "cat"})
    assert not (await other.receive_json())["success"]


async def test_bound_post_alert_clip_never_substitutes_the_latest_event(hass):
    from tests.test_phase3 import _setup
    entry = await _setup(hass, event_clips=True, security_event_evidence=True, clip_cameras=["camera.garden"])
    rt = entry.runtime_data
    raw = event()
    rt.security_events.ingest(raw)
    with patch.object(rt.clips, "async_latest_event", AsyncMock()) as latest, \
            patch.object(rt.clips, "async_save_clip", AsyncMock(return_value="media-source://clip.mp4")) as save, \
            patch("custom_components.sentient.clips.async_generate", AsyncMock(return_value={"data": "A person walked past"})):
        result = await rt.clips.async_what_happened("camera.garden", event_id=raw["id"])
    assert result["event_id"] == raw["id"]
    assert save.call_args.args[0] == raw["id"]
    latest.assert_not_awaited()
    assert rt.security_events.items[raw["id"]]["has_clip"] and rt.security_events.items[raw["id"]]["has_snapshot"]


async def test_security_alert_binds_the_chosen_event_and_reviews_that_id(hass):
    from tests.test_camera_security_reliability import security
    obj, rt = security(hass, security_event_evidence=True, clip_cameras=["camera.garden"], camera_security_describe=False)
    rt.options = obj.options
    rt.security_events = SecurityEvents(hass, "test", obj.options, lambda: rt)
    rt.security_events.ingest(event())
    rt.clips = SimpleNamespace(enabled=True, _stamp=lambda: (), async_after_alert=AsyncMock())
    with patch("custom_components.sentient.camsecurity.camera_for", return_value="camera.garden"):
        await obj._async_alert("binary_sensor.garden_person", "Person reported", reason="night")
        await asyncio.sleep(0)
    assert rt.timeline.mark.call_args.kwargs["event_id"] == "123.event"
    assert rt.clips.async_after_alert.call_args.args[-1] == "123.event"
    assert rt.security_events.items["123.event"]["alerts"][0]["reason"] == "night"


async def test_ambiguous_event_keeps_plain_alert_without_wrong_clip_review(hass):
    from tests.test_camera_security_reliability import security
    obj, rt = security(hass, security_event_evidence=True, clip_cameras=["camera.garden"], camera_security_describe=False)
    rt.options = obj.options
    rt.security_events = SecurityEvents(hass, "test", obj.options, lambda: rt)
    rt.security_events.ingest(event())
    rt.security_events.ingest(event(id="other.event"))
    rt.clips = SimpleNamespace(enabled=True, _stamp=lambda: (), async_after_alert=AsyncMock())
    with patch("custom_components.sentient.camsecurity.camera_for", return_value="camera.garden"):
        await obj._async_alert("binary_sensor.garden_person", "Person reported", reason="night")
    rt.timeline.mark.assert_not_called()
    rt.clips.async_after_alert.assert_not_awaited()


async def test_feedback_voice_requires_admin_and_records_only_the_selected_event(hass, hass_admin_user, hass_read_only_user):
    from custom_components.sentient.security_voice import SecurityFeedbackTool
    obj, _, rt = setup(hass)
    obj.ingest(event())
    with patch("custom_components.sentient.security_voice._runtime", return_value=rt), \
            patch("custom_components.sentient.security_voice.async_should_expose", return_value=True):
        denied = await SecurityFeedbackTool().async_call(hass, llm.ToolInput(tool_name="sentient_security_feedback", tool_args={"event_id": "123.event", "value": "cat"}), context(hass_read_only_user.id))
        assert "error" in denied and not obj.items["123.event"].get("feedback")
        result = await SecurityFeedbackTool().async_call(hass, llm.ToolInput(tool_name="sentient_security_feedback", tool_args={"event_id": "123.event", "value": "cat"}), context(hass_admin_user.id))
    assert result["feedback"]["value"] == "cat" and result["changed_detector_settings"] is False


async def test_user_permissions_and_failed_exposure_lookup_deny_event_read(hass, hass_read_only_user):
    from custom_components.sentient.security_voice import SecurityEventTool
    obj, _, rt = setup(hass)
    obj.ingest(event())
    user = SimpleNamespace(is_active=True, permissions=Mock())
    user.permissions.check_entity.return_value = False
    with patch("custom_components.sentient.security_voice._runtime", return_value=rt), \
            patch("custom_components.sentient.security_voice.async_should_expose", return_value=True), \
            patch.object(type(hass.auth), "async_get_user", AsyncMock(return_value=user)):
        result = await SecurityEventTool().async_call(hass, llm.ToolInput(tool_name="sentient_security_event", tool_args={"event_id": "123.event"}), context(hass_read_only_user.id))
    assert "123.event" not in str(result)
    with patch("custom_components.sentient.security_voice._runtime", return_value=rt), \
            patch("custom_components.sentient.security_voice.async_should_expose", side_effect=KeyError("not loaded")):
        result = await SecurityEventTool().async_call(hass, llm.ToolInput(tool_name="sentient_security_event", tool_args={"event_id": "123.event"}), context())
    assert "123.event" not in str(result)


async def test_forgetting_removes_new_analysis_of_an_old_event(hass, freezer):
    obj, _, _ = setup(hass)
    obj.ingest(event())
    freezer.tick(timedelta(days=3))
    obj.analysis("123.event", "garden", {"summary": "A new private analysis"})
    assert obj.forget(dt_util.utcnow() - timedelta(hours=48)) == 1
    assert not obj.items


async def test_retention_still_expires_records_when_features_are_off(hass, freezer):
    obj, _, _ = setup(hass)
    obj.ingest(event())
    obj.configure({**obj.options, "security_event_evidence": False, "security_conversation_context": False, "security_event_feedback": False})
    freezer.tick(timedelta(days=8))
    obj._expire(dt_util.utcnow())
    assert not obj.items
    obj.async_stop()


async def test_full_settings_expose_all_four_features_off_by_default(hass, hass_ws_client):
    from tests.test_phase3 import _setup
    entry = await _setup(hass)
    ws = await hass_ws_client(hass)
    await ws.send_json({"id": 1, "type": "sentient/settings"})
    result = (await ws.receive_json())["result"]
    keys = {"security_event_evidence", "security_conversation_context", "security_event_feedback", "share_clip_analysis"}
    assert all(result["data"][key] is False for key in keys)
    assert keys <= {field["name"] for group in result["groups"] for field in group["schema"]}
    api = await llm.async_get_api(hass, "sentient", context())
    assert not {"sentient_security_event", "sentient_security_feedback"} & {tool.name for tool in api.tools}
    from custom_components.sentient.llm_api import WhatHappenedTool
    result = await WhatHappenedTool().async_call(hass, llm.ToolInput(tool_name="sentient_what_happened",
        tool_args={"camera": "garden", "event_id": "123.event"}), context())
    assert "Enable a security event feature" in result["error"]


async def test_ambiguous_camera_names_are_not_silently_resolved(hass):
    from custom_components.sentient.security_voice import camera_named
    setup(hass)
    hass.states.async_set("camera.front_door", "idle", {"friendly_name": "Front Door"})
    hass.states.async_set("camera.front_garden", "idle", {"friendly_name": "Front Garden"})
    assert camera_named(hass, "front") is None
    assert camera_named(hass, "front door") == "camera.front_door"


async def test_event_read_rechecks_privacy_after_permission_lookup(hass):
    from custom_components.sentient.security_voice import SecurityEventTool
    obj, _, rt = setup(hass)
    obj.ingest(event())

    async def permitted(*_):
        obj.invalidate()
        obj.items.clear()
        return True

    with patch("custom_components.sentient.security_voice._runtime", return_value=rt), \
            patch("custom_components.sentient.security_voice.allowed", permitted):
        result = await SecurityEventTool().async_call(hass, llm.ToolInput(tool_name="sentient_security_event", tool_args={"event_id": "123.event"}), context())
    assert "event" not in result and "123.event" not in str(result)


async def test_settings_changes_during_mqtt_subscription_do_not_leak_or_duplicate(hass):
    obj, _, _ = setup(hass)
    MockConfigEntry(domain="mqtt").add_to_hass(hass)
    first, second, finish = asyncio.Event(), asyncio.Event(), asyncio.Event()
    calls = []
    unsub = Mock()

    async def subscribe(*args):
        calls.append(args)
        (first if len(calls) == 1 else second).set()
        await finish.wait()
        return unsub

    with patch("homeassistant.components.mqtt.async_wait_for_mqtt_client", AsyncMock(return_value=True)), \
            patch("homeassistant.components.mqtt.async_subscribe", subscribe):
        obj.configure(obj.options)
        await first.wait()
        obj.configure(dict(obj.options))
        await second.wait()
        finish.set()
        await hass.async_block_till_done(wait_background_tasks=True)
        assert len(calls) == 2 and obj._unsub is unsub and obj._subscribing is None
        obj.configure({**obj.options, "security_event_evidence": False, "security_conversation_context": False, "security_event_feedback": False})
        unsub.assert_called_once()
    obj.async_stop()


async def test_late_snapshot_preparation_does_not_wake_camera_after_forgetting(hass):
    from tests.test_camera_security_reliability import security
    obj, rt = security(hass)

    async def snapshot(_):
        rt.speech._privacy_generation += 1
        return None

    rt.security_events = SimpleNamespace(async_snapshot=snapshot)
    stamp = rt.speech._privacy_generation
    with patch("custom_components.sentient.camwake.async_wake", AsyncMock()) as wake:
        assert await obj._describe("camera.garden", lambda: stamp == rt.speech._privacy_generation, event()) == (None, None)
        wake.assert_not_awaited()


async def test_disabling_settings_clears_shared_answers_focus_and_tools(hass):
    from tests.test_phase3 import _setup
    entry = await _setup(hass, share_clip_analysis=True, security_event_evidence=True,
                         security_conversation_context=True, security_event_feedback=True, clip_cameras=["camera.garden"])
    rt = entry.runtime_data
    rt.security_events.ingest(event())
    rt.security_events.focus(("conversation", "speaker", None, None), "123.event")
    await rt.clip_analysis.analyse("garden", "123.event", 1, 20, None, "neutral",
                                   AsyncMock(return_value={"summary": "A visitor arrived", "worth": False, "kind": "visitor"}))
    assert rt.clip_analysis._cache and rt.security_events._focus
    hass.config_entries.async_update_entry(entry, options={**entry.data, "share_clip_analysis": False,
        "security_event_evidence": False, "security_conversation_context": False, "security_event_feedback": False})
    await hass.async_block_till_done()
    assert not rt.clip_analysis._cache and not rt.security_events._focus and not rt.security_events.recent()
    assert "123.event" in rt.security_events.items  # retained until expiry/forget, as the setting describes
    api = await llm.async_get_api(hass, "sentient", context())
    assert not {"sentient_security_event", "sentient_security_feedback"} & {tool.name for tool in api.tools}


async def test_event_and_cache_storage_are_bounded(hass):
    obj, cache, _ = setup(hass)
    now = dt_util.utcnow().timestamp()
    for i in range(205):
        obj.ingest(event(id=f"event-{i}", start_time=now - 300 + i))
    assert len(obj.items) == 200 and "event-0" not in obj.items and "event-204" in obj.items
    producer = AsyncMock(return_value={"summary": "A visitor arrived", "worth": False, "kind": "visitor"})
    for i in range(105):
        await cache.analyse("garden", f"event-{i}", 1, 20, None, "neutral", producer)
    assert len(cache._cache) == 100


async def test_review_of_multiple_events_is_not_shared_or_bound_to_the_first(hass):
    from tests.test_phase3 import _setup
    entry = await _setup(hass, share_clip_analysis=True, clip_watch=True, clip_cameras=["camera.garden"],
                         security_event_evidence=True)
    rt = entry.runtime_data
    raw = event()
    rt.security_events.ingest(raw)
    review = {**raw, "id": "review", "severity": "alert", "data": {"detections": [raw["id"], "other.event"]}}
    with patch.object(rt.clipwatch, "_ask_ai", AsyncMock(return_value={"summary": "Two people passed", "worth": False, "kind": "visitor"})):
        result = await rt.clipwatch.async_watch(review)
    assert result["recorded"]["event_bound"] is False
    assert not rt.clip_analysis._cache
    assert not rt.security_events.items[raw["id"]].get("analysis")


async def test_permissions_are_rechecked_when_camera_exposure_changes_during_auth(hass, hass_read_only_user):
    from custom_components.sentient.security_voice import allowed
    visible = True
    user = SimpleNamespace(is_active=True, permissions=Mock())
    user.permissions.check_entity.return_value = True

    async def get_user(*_):
        nonlocal visible
        visible = False
        return user

    with patch("custom_components.sentient.security_voice.async_should_expose", side_effect=lambda *_: visible), \
            patch.object(type(hass.auth), "async_get_user", get_user):
        assert await allowed(hass, context(hass_read_only_user.id), "camera.garden") is False


@pytest.mark.parametrize("ambiguous", [False, True])
async def test_latest_alert_question_selects_unique_recent_alert_or_asks_which(hass, ambiguous):
    from custom_components.sentient.security_voice import SecurityEventTool
    obj, _, rt = setup(hass)
    obj.ingest(event())
    obj.alert("123.event", "binary_sensor.person", "night", "Person reported", None)
    obj.ingest(event(id="other.event"))
    if ambiguous:
        obj.alert("other.event", "binary_sensor.person", "night", "Another detection", None)
    with patch("custom_components.sentient.security_voice._runtime", return_value=rt), \
            patch("custom_components.sentient.security_voice.async_should_expose", return_value=True):
        result = await SecurityEventTool().async_call(hass, llm.ToolInput(tool_name="sentient_security_event", tool_args={"action": "latest_alert"}), context())
    if ambiguous:
        assert len(result["choose_event"]) == 2 and "event" not in result
    else:
        assert result["event"]["id"] == "123.event"


async def test_empty_event_list_does_not_request_impossible_clarification(hass):
    from custom_components.sentient.security_voice import select
    _, _, rt = setup(hass)
    row, result = await select(hass, rt, context())
    assert row is None and result["events"] == [] and "choose_event" not in result


async def test_stationary_vehicle_tracks_do_not_hide_a_different_moving_vehicle(hass):
    events, _cache, rt = setup(hass)
    added = []
    rt.incidents = SimpleNamespace(add=lambda *a, **k: added.append(k.get("event_id")))
    parked = event(id="parked.car", label="car", end_time=None)
    events.ingest(parked)
    assert not added
    still = events.ingest({**parked, "stationary": True, "position_changes": 0})
    ended = events.ingest({**parked, "end_time": dt_util.utcnow().timestamp()})
    assert still["parked"] and ended["parked"] and not added

    moving = event(id="moving.car", label="car", end_time=None, stationary=False, position_changes=3)
    assert not events.ingest(moving).get("parked")
    assert added == ["moving.car"]
    assert not events.ingest({**moving, "stationary": True}).get("parked")
    other = events.ingest(event(id="second.car", label="car", stationary=False, position_changes=4))
    person = events.ingest(event(id="person.event"))
    assert not other.get("parked") and not person.get("parked")
    assert added[-2:] == ["second.car", "person.event"]
