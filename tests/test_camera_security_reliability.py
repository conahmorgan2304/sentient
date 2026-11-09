"""Security decisions retain evidence and cannot outlive their context."""
import asyncio
from datetime import timedelta
from types import SimpleNamespace
from unittest.mock import AsyncMock, Mock, patch

import pytest
from homeassistant.helpers import entity_registry as er, device_registry as dr
from homeassistant.util import dt as dt_util
from pytest_homeassistant_custom_component.common import MockConfigEntry, async_fire_time_changed

from custom_components.sentient.camsecurity import CameraSecurity, camera_for
from custom_components.sentient.clipwatch import ClipWatch


def security(hass, **extra):
    options = {"camera_security": True, "camera_security_sensors": ["binary_sensor.garden_person", "binary_sensor.gate_person"],
               "camera_security_describe": True, "night_watch_announce": True, **extra}
    speech = SimpleNamespace(_privacy_generation=0, _guest_generation=0, guest_mode=False,
                             quiet_hours_now=Mock(return_value=True), async_say=AsyncMock())
    rt = SimpleNamespace(speech=speech, ai=SimpleNamespace(_generation=0), timeline=Mock(), incidents=None,
                         watches=SimpleNamespace(spend=Mock(return_value=True)), clips=None,
                         night_watch=SimpleNamespace(someone_inside=lambda _: None))
    return CameraSecurity(hass, options, lambda: rt), rt


async def test_camera_matching_uses_full_name_and_refuses_ambiguous_views(hass):
    hass.states.async_set("binary_sensor.front_garden_person_occupancy", "off", {"friendly_name": "Front Garden Person occupancy"})
    hass.states.async_set("camera.front_door", "idle", {"friendly_name": "Front Door"})
    hass.states.async_set("camera.front_garden", "idle", {"friendly_name": "Front Garden"})
    assert camera_for(hass, "binary_sensor.front_garden_person_occupancy") == "camera.front_garden"
    hass.states.async_set("camera.front_garden_substream", "idle", {"friendly_name": "Front Garden"})
    assert camera_for(hass, "binary_sensor.front_garden_person_occupancy") is None


async def test_device_camera_matching_prefers_unique_exact_view(hass):
    entry = MockConfigEntry(domain="test")
    entry.add_to_hass(hass)
    device = dr.async_get(hass).async_get_or_create(config_entry_id=entry.entry_id, identifiers={("test", "camera")})
    registry = er.async_get(hass)
    sensor = registry.async_get_or_create("binary_sensor", "test", "person", suggested_object_id="garden_person", device_id=device.id)
    camera = registry.async_get_or_create("camera", "test", "view", suggested_object_id="garden", device_id=device.id)
    assert camera_for(hass, sensor.entity_id) == camera.entity_id
    registry.async_get_or_create("camera", "test", "other", suggested_object_id="another_view", device_id=device.id)
    assert camera_for(hass, sensor.entity_id) == camera.entity_id
    registry.async_update_entity(camera.entity_id, new_entity_id="camera.unrelated_view")
    assert camera_for(hass, sensor.entity_id) is None


@pytest.mark.parametrize("person", ["false", "true", "unknown", 0, 1, [], {}, None])
async def test_invalid_person_result_is_unknown_and_cannot_supply_description(hass, person):
    obj, _ = security(hass)
    with patch("custom_components.sentient.camwake.async_wake", AsyncMock()), \
            patch("custom_components.sentient.camsecurity.async_generate", AsyncMock(return_value={"data": {"person": person, "description": "Untrusted detail"}})):
        assert await obj._describe("camera.garden") == (None, None)


@pytest.mark.parametrize("person", [False, True])
async def test_actual_boolean_person_results_are_preserved(hass, person):
    obj, _ = security(hass)
    with patch("custom_components.sentient.camwake.async_wake", AsyncMock()), \
            patch("custom_components.sentient.camsecurity.async_generate", AsyncMock(return_value={"data": {"person": person, "description": "A person at the door"}})):
        assert await obj._describe("camera.garden") == (person, "A person at the door" if person else None)


@pytest.mark.parametrize("change", ["disable", "remove", "privacy", "guest", "settings", "stop", "clear"])
async def test_late_security_result_cannot_publish_after_context_changes(hass, change):
    obj, rt = security(hass)
    entered, release = asyncio.Event(), asyncio.Event()

    async def describe(*_):
        entered.set()
        await release.wait()
        return True, "A person at the door"

    with patch("custom_components.sentient.camsecurity.camera_for", return_value="camera.garden"), patch.object(obj, "_describe", describe):
        task = asyncio.create_task(obj._async_alert("binary_sensor.garden_person", "Person reported"))
        await entered.wait()
        rt.timeline.add.assert_called_once()
        rt.speech.async_say.assert_awaited_once()
        assert "Person reported" in rt.timeline.add.call_args.args[1]
        if change == "disable": obj.options["camera_security"] = False
        elif change == "remove": obj.options["camera_security_sensors"] = []
        elif change == "privacy": rt.speech._privacy_generation += 1
        elif change == "guest": rt.speech._guest_generation += 1
        elif change == "settings": obj.options = dict(obj.options)
        elif change == "stop": obj.async_stop()
        else: obj.clear()
        release.set()
        await task
    rt.timeline.add.assert_called_once()
    rt.timeline.mark.assert_not_called()
    rt.speech.async_say.assert_awaited_once()
    assert not obj._pending_alert_keys


async def test_clear_cancels_retained_security_tasks(hass):
    obj, rt = security(hass)
    entered = asyncio.Event()

    async def describe(*_):
        entered.set()
        await asyncio.Event().wait()

    with patch("custom_components.sentient.camsecurity.camera_for", return_value="camera.garden"), patch.object(obj, "_describe", describe):
        obj._alert("binary_sensor.garden_person", "Person reported", "night")
        await entered.wait()
        tasks = list(obj._tasks)
        obj.clear()
        await asyncio.gather(*tasks, return_exceptions=True)
    assert tasks and all(t.cancelled() for t in tasks)
    rt.timeline.add.assert_called_once()
    rt.timeline.mark.assert_not_called()


async def test_cancel_before_first_task_turn_releases_incident_reservation(hass):
    from custom_components.sentient.incidents import Incidents
    obj, rt = security(hass)
    rt.incidents = Incidents(hass, "test", {"security_incidents": True})
    obj._alert("binary_sensor.garden_person", "Person reported", "night")
    assert rt.incidents._pending_alerts
    tasks = list(obj._tasks)
    obj.clear()
    await asyncio.gather(*tasks, return_exceptions=True)
    await hass.async_block_till_done()
    assert not rt.incidents._pending_alerts and not obj._tasks
    rt.timeline.add.assert_not_called()


async def test_context_change_releases_own_pending_key_for_the_next_alert(hass):
    obj, rt = security(hass)

    async def describe(*_):
        rt.speech._guest_generation += 1
        return True, "A person"

    with patch("custom_components.sentient.camsecurity.camera_for", return_value="camera.garden"), patch.object(obj, "_describe", describe):
        obj._alert("binary_sensor.garden_person", "Person reported", "night")
        await hass.async_block_till_done()
    assert not obj._pending_alert_keys
    rt.timeline.add.assert_called_once()
    rt.timeline.mark.assert_not_called()
    obj.options["camera_security_describe"] = False
    obj._alert("binary_sensor.garden_person", "New detection", "night")
    await hass.async_block_till_done()
    assert rt.timeline.add.call_count == 2


async def test_empty_later_image_keeps_sensor_alert_and_cooldown(hass):
    obj, rt = security(hass)
    with patch("custom_components.sentient.camsecurity.camera_for", return_value="camera.garden"), \
            patch.object(obj, "_describe", AsyncMock(return_value=(False, None))):
        obj._alert("binary_sensor.garden_person", "Person reported", "night")
        await hass.async_block_till_done()
        obj._alert("binary_sensor.garden_person", "Person reported", "night")
        await hass.async_block_till_done()
    assert rt.timeline.add.call_count == 1
    assert "earlier sensor detection is unverified" in rt.timeline.mark.call_args.kwargs["analysis"]
    assert rt.speech.async_say.await_count == 1


@pytest.mark.parametrize("uncertain", ["startup", "entry_missing", "entry_unknown", "entry_unavailable", "entry_unconfigured", "occupied"])
async def test_boundary_inference_requires_known_quiet_history(hass, freezer, uncertain):
    obj, rt = security(hass, camera_security_inside=["binary_sensor.garden_person", "binary_sensor.gate_person"],
                       camera_security_entries=["binary_sensor.entry"], camera_security_describe=False)
    rt.speech.quiet_hours_now.return_value = False
    hass.states.async_set("binary_sensor.garden_person", "off")
    hass.states.async_set("binary_sensor.gate_person", "on" if uncertain == "occupied" else "off")
    if uncertain != "entry_missing":
        hass.states.async_set("binary_sensor.entry", {"entry_unknown": "unknown", "entry_unavailable": "unavailable"}.get(uncertain, "off"))
    if uncertain == "entry_unconfigured": obj.options["camera_security_entries"] = []
    if uncertain != "startup": freezer.tick(timedelta(minutes=11))
    obj.async_start()
    hass.states.async_set("binary_sensor.garden_person", "on")
    await hass.async_block_till_done()
    rt.timeline.add.assert_not_called()
    obj.async_stop()


async def test_known_quiet_boundary_alert_does_not_claim_forced_entry(hass, freezer):
    obj, rt = security(hass, camera_security_inside=["binary_sensor.garden_person"],
                       camera_security_entries=["binary_sensor.entry"], camera_security_describe=False)
    rt.speech.quiet_hours_now.return_value = False
    hass.states.async_set("binary_sensor.garden_person", "off")
    hass.states.async_set("binary_sensor.entry", "off")
    freezer.tick(timedelta(minutes=11))
    obj.async_start()
    hass.states.async_set("binary_sensor.garden_person", "on")
    await hass.async_block_till_done()
    assert "no recent entry opening recorded" in rt.timeline.add.call_args.args[1]
    obj.async_stop()


async def test_loiter_timer_rechecks_quiet_hours(hass, freezer):
    obj, rt = security(hass, camera_security_describe=False)
    hass.states.async_set("binary_sensor.garden_person", "on")
    with patch.object(obj, "_alert") as alert:
        obj._seen("binary_sensor.garden_person", dt_util.utcnow())
        alert.reset_mock()
        rt.speech.quiet_hours_now.return_value = False
        freezer.tick(timedelta(minutes=5))
        async_fire_time_changed(hass)
        await hass.async_block_till_done()
        alert.assert_not_called()
    obj.async_stop()


async def test_pending_journey_does_not_publish_after_privacy_change(hass, freezer):
    obj, rt = security(hass, camera_security_describe=False)
    with patch.object(obj, "_alert"):
        obj._seen("binary_sensor.gate_person", dt_util.utcnow())
        obj._seen("binary_sensor.garden_person", dt_util.utcnow())
    rt.speech._privacy_generation += 1
    freezer.tick(timedelta(minutes=4))
    async_fire_time_changed(hass)
    await hass.async_block_till_done()
    rt.timeline.add.assert_not_called()
    assert not obj.journeys
    obj.async_stop()


def clips(hass):
    options = {"clip_watch": True, "clip_cameras": ["camera.garden"], "clip_notify": "worth"}
    speech = SimpleNamespace(_privacy_generation=0, _guest_generation=0, guest_mode=False,
                             quiet_hours_now=lambda: False)
    rt = SimpleNamespace(speech=speech, ai=SimpleNamespace(_generation=0), timeline=Mock(),
                         watches=SimpleNamespace(spend=Mock(return_value=True)), incidents=None)
    obj = ClipWatch(hass, "test", options, lambda: rt)
    review = {"id": "visit", "camera": "garden", "start_time": dt_util.utcnow().timestamp() - 20,
              "end_time": dt_util.utcnow().timestamp() - 5, "severity": "alert", "data": {"detections": ["event"]}}
    return obj, rt, review


@pytest.mark.parametrize("change", ["disable", "remove", "privacy", "guest", "settings", "stop", "forget"])
async def test_late_clip_cannot_repopulate_or_notify(hass, change):
    obj, rt, review = clips(hass)
    entered, release = asyncio.Event(), asyncio.Event()

    async def analyse(*_):
        entered.set()
        await release.wait()
        return {"summary": "A visitor arrived", "worth": True, "kind": "visitor"}

    with patch.object(obj, "_ask_ai", analyse), patch.object(obj, "_notify") as notify:
        task = asyncio.create_task(obj.async_watch(review))
        await entered.wait()
        if change == "disable": obj.options["clip_watch"] = False
        elif change == "remove": obj.options["clip_cameras"] = []
        elif change == "privacy": rt.speech._privacy_generation += 1
        elif change == "guest": rt.speech._guest_generation += 1
        elif change == "settings": obj.options = dict(obj.options)
        elif change == "stop": obj.async_stop()
        else: obj.invalidate()
        release.set()
        assert "skipped" in await task
        notify.assert_not_called()
    assert not obj.items and not obj._busy
    rt.timeline.add.assert_not_called()


async def test_overlapping_clip_work_and_replayed_review_do_not_spend_twice(hass, freezer):
    obj, rt, review = clips(hass)
    entered, release = asyncio.Event(), asyncio.Event()

    async def analyse(*_):
        entered.set()
        await release.wait()
        return {"summary": "A visitor arrived", "worth": False, "kind": "visitor"}

    with patch.object(obj, "_ask_ai", analyse):
        task = asyncio.create_task(obj.async_watch(review))
        await entered.wait()
        assert (await obj.async_watch(review, force=True))["skipped"] == "already running"
        release.set()
        assert "recorded" in await task
        freezer.tick(timedelta(minutes=6))
        assert (await obj.async_watch(review))["skipped"] == "already watched"
    assert rt.watches.spend.call_count == 1 and len(obj.items) == 1


@pytest.mark.parametrize("worth", ["false", "true", 1, 0, {}, None])
async def test_clip_worth_requires_a_boolean(hass, worth):
    obj, _, _ = clips(hass)
    item = obj._record("garden", dt_util.utcnow().timestamp(), "event", "visit", {"summary": "A visitor arrived", "kind": "visitor", "worth": worth})
    assert item["recorded"]["worth"] is False


@pytest.mark.parametrize("start,end", [(float("nan"), 50), (50, float("inf")), (-1, 50), (50, 40), (1e300, 1e300)])
async def test_invalid_clip_times_never_spend_ai(hass, start, end):
    obj, rt, review = clips(hass)
    review.update(start_time=start, end_time=end)
    assert (await obj.async_watch(review))["error"] == "invalid clip times"
    rt.watches.spend.assert_not_called()


async def test_clip_cancel_during_media_preparation_cleans_files(hass):
    obj, _, review = clips(hass)
    entered = asyncio.Event()

    async def video(*_):
        entered.set()
        await asyncio.Event().wait()

    with patch.object(obj, "_video", video), patch.object(obj, "_remove", AsyncMock()) as remove:
        task = asyncio.create_task(obj.async_watch(review))
        await entered.wait()
        task.cancel()
        with pytest.raises(asyncio.CancelledError): await task
        assert remove.await_count == 2
    assert not obj._busy


async def test_clip_privacy_change_during_download_prevents_ai_call(hass):
    obj, rt, review = clips(hass)

    async def video(*_):
        rt.speech._privacy_generation += 1
        return "media-source://clip.mp4"

    with patch.object(obj, "_video", video), patch.object(obj, "_remove", AsyncMock()) as remove, \
            patch("custom_components.sentient.clipwatch.async_generate", AsyncMock()) as generate:
        assert "skipped" in await obj.async_watch(review)
        generate.assert_not_awaited()
        assert remove.await_count == 2
    assert not obj.items


async def test_queued_notification_rechecks_context_before_each_phone(hass):
    obj, rt, _ = clips(hass)
    entered, release = asyncio.Event(), asyncio.Event()
    delivered = []

    async def notify(call):
        delivered.append(call.service)
        entered.set()
        await release.wait()

    hass.services.async_register("notify", "first_phone", notify)
    hass.services.async_register("notify", "second_phone", notify)
    with patch("custom_components.sentient.clipwatch.phone_apps", return_value=["first_phone", "second_phone"]):
        obj._record("garden", dt_util.utcnow().timestamp(), "event", "visit", {"summary": "A visitor arrived", "kind": "visitor", "worth": True})
        await entered.wait()
        rt.speech._privacy_generation += 1
        release.set()
        await hass.async_block_till_done()
    assert delivered == ["first_phone"]


@pytest.mark.parametrize("mode", ["disabled", "other_camera", "stopped"])
async def test_manual_clip_check_honours_feature_and_camera_settings(hass, mode):
    obj, rt, review = clips(hass)
    if mode == "disabled": obj.options["clip_watch"] = False
    elif mode == "other_camera": obj.options["clip_cameras"] = ["camera.other"]
    else: obj.async_stop()
    assert "skipped" in await obj.async_watch(review, force=True)
    assert "skipped" in await obj.async_latest("garden")
    rt.watches.spend.assert_not_called()


async def test_diagnostics_show_unresolved_camera_without_clip_or_person_details(hass):
    from tests.test_phase3 import _setup
    from custom_components.sentient.diagnostics import async_get_config_entry_diagnostics
    entry = await _setup(hass, camera_security_sensors=["binary_sensor.front_person"])
    hass.states.async_set("binary_sensor.front_person", "off", {"friendly_name": "Front Person"})
    hass.states.async_set("camera.front_garden", "idle")
    hass.states.async_set("camera.front_door", "idle")
    entry.runtime_data.clipwatch.items = [{"summary": "Private clip description", "whose": "Private name"}]
    data = await async_get_config_entry_diagnostics(hass, entry)
    assert data["camera_security"]["camera_matches"] == [{"sensor": "binary_sensor.front_person", "camera": None}]
    assert data["clip_watch"]["retained_summaries"] == 1
    assert "Private clip description" not in str(data) and "Private name" not in str(data)


@pytest.mark.parametrize("change", ["disable", "privacy", "guest", "settings", "stop", "forget"])
async def test_event_clip_result_does_not_restore_old_incident_or_last_answer(hass, change):
    from custom_components.sentient.clips import Clips
    _, rt, review = clips(hass)
    rt.incidents = Mock()
    obj = Clips(hass, {"event_clips": True}, lambda: rt)
    entered, release = asyncio.Event(), asyncio.Event()

    async def analyse(*_):
        entered.set()
        await release.wait()
        return {"data": "A visitor arrived"}

    with patch.object(obj, "async_latest_event", AsyncMock(return_value={**review, "id": "event"})), \
            patch.object(obj, "async_save_clip", AsyncMock(return_value="media-source://clip.mp4")), \
            patch("custom_components.sentient.clips.async_generate", analyse):
        task = asyncio.create_task(obj.async_what_happened("camera.garden"))
        await entered.wait()
        if change == "disable": obj.options["event_clips"] = False
        elif change == "privacy": rt.speech._privacy_generation += 1
        elif change == "guest": rt.speech._guest_generation += 1
        elif change == "settings": obj.options = dict(obj.options)
        elif change == "stop": obj.async_stop()
        else: obj.invalidate()
        release.set()
        assert "skipped" in await task
    assert not obj.last and not obj._busy
    rt.incidents.add.assert_not_called()


async def test_delayed_event_clip_rechecks_context_after_settling(hass):
    from custom_components.sentient.clips import Clips
    obj = Clips(hass, {"event_clips": True}, lambda: None)
    with patch("custom_components.sentient.clips.SETTLE", 0), \
            patch.object(obj, "async_what_happened", AsyncMock()) as analyse:
        task = asyncio.create_task(obj.async_after_alert("camera.garden", "Person reported"))
        await asyncio.sleep(0)
        obj.invalidate()
        await task
        analyse.assert_not_awaited()


async def test_queued_event_clip_uses_originating_security_context(hass):
    from custom_components.sentient.clips import Clips
    obj = Clips(hass, {"event_clips": True}, lambda: None)
    stamp = obj._stamp()
    obj.invalidate()
    with patch.object(obj, "async_what_happened", AsyncMock()) as analyse:
        await obj.async_after_alert("camera.garden", "Person reported", stamp)
        analyse.assert_not_awaited()


async def test_overlapping_manual_event_clip_requests_do_not_double_spend(hass):
    from custom_components.sentient.clips import Clips
    _, rt, review = clips(hass)
    obj = Clips(hass, {"event_clips": True}, lambda: rt)
    entered, release = asyncio.Event(), asyncio.Event()

    async def analyse(*_):
        entered.set()
        await release.wait()
        return {"data": "A visitor arrived"}

    with patch.object(obj, "async_latest_event", AsyncMock(return_value={**review, "id": "event"})), \
            patch.object(obj, "async_save_clip", AsyncMock(return_value="media-source://clip.mp4")), \
            patch("custom_components.sentient.clips.async_generate", analyse):
        task = asyncio.create_task(obj.async_what_happened("camera.garden"))
        await entered.wait()
        assert "skipped" in await obj.async_what_happened("garden", force=True)
        release.set()
        assert (await task)["what_happened"] == "A visitor arrived"
    assert rt.watches.spend.call_count == 1
