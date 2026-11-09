"""Replay shows readable recorded observations, exact media and passive telemetry."""
import json
from datetime import timedelta
from types import SimpleNamespace
from unittest.mock import Mock, patch

from homeassistant.core import Context, callback
from homeassistant.util import dt as dt_util

from custom_components.sentient.frigate_health import FrigateHealth
from custom_components.sentient.incident_replay import replay_data
from custom_components.sentient.incidents import Incidents
from tests.test_security_events import event, setup
from tests.test_voice_sessions_control import agent, request


def runtime(hass):
    events, _, rt = setup(hass, security_incidents=True, security_coverage=True, incident_replay=True,
                         incident_replay_image="/local/security.png", clip_cameras=["camera.shed", "camera.garden"],
                         incident_replay_points={"camera.shed": [80, 20], "camera.garden": [53, 20], "camera.hidden": [10, 10]})
    rt.incidents = Incidents(hass, "test", rt.options)
    return events, rt


async def test_replay_keeps_event_time_gap_and_exact_clip(hass, freezer):
    events, rt = runtime(hass)
    hass.states.async_set("camera.garden", "unavailable")
    hass.states.async_set("camera.shed", "idle")
    hass.states.async_set("binary_sensor.gate", "on", {"friendly_name": "Gate"})
    inc = rt.incidents.add("binary_sensor.gate", "Gate opened", "contact")
    freezer.tick(timedelta(seconds=12))
    events.ingest(event(camera="shed", start_time=dt_util.utcnow().timestamp(), end_time=None))
    rt.incidents.add("camera.shed", "Unverified clip ID", "clip", evidence="inferred", event_id="missing-event")
    rt.incidents.add("camera.shed", "Private detection", "detection", read_source="binary_sensor.hidden")
    hass.states.async_set("camera.garden", "idle", {"friendly_name": "Garden"})
    user = SimpleNamespace(is_active=True, permissions=Mock())
    user.permissions.check_entity.side_effect = lambda eid, _: "hidden" not in eid
    reply = replay_data(hass, rt, user, inc["id"], "gate")
    assert reply["image"] == "/local/security.png"
    assert "camera.hidden" not in reply["points"]
    gate, shed, unverified = reply["selected"]["steps"]
    assert gate["offset"] == 0 and "clip" not in gate
    assert gate["gaps"] == [{"entity": "camera.garden", "name": "Garden", "state": "unavailable"}]
    assert shed["offset"] == 12 and shed["clip"].endswith("/123.event/clip.mp4")
    assert "clip" not in unverified
    assert not replay_data(hass, rt, user, "unreadable")["selected"]
    rt.speech.guest_mode = True
    assert replay_data(hass, rt, user) == {"enabled": False}


async def test_voice_opens_configured_tablet_only_for_authenticated_admin(hass, hass_admin_user):
    _, rt = runtime(hass)
    rt.options["incident_replay_tablet"] = "kiosk"
    obj, voice_rt = agent(hass, rt.options)
    voice_rt.incidents = rt.incidents
    voice_rt.security_events = rt.security_events
    voice_rt.speech = rt.speech
    inc = rt.incidents.add("binary_sensor.gate", "Gate opened", "contact")
    calls = []

    @callback
    def load(call):
        calls.append(call.data)

    hass.services.async_register("fully_kiosk", "load_url", load)
    with patch("custom_components.sentient.incident_replay.get_url", return_value="http://ha.test"):
        denied = await obj.async_process(request("Show me what happened at the gate"))
        assert "administrator" in denied.response.speech["plain"]["speech"] and not calls
        result = await obj.async_process(request("Show me what happened at the gate.", user=hass_admin_user.id))
    assert not result.continue_conversation
    assert calls == [{"device_id": "kiosk", "url": f"http://ha.test/sentient/security?incident={inc['id']}"}]


async def test_frigate_health_ignores_retained_stats_and_quiet_camera_is_not_failed(hass, hass_admin_user, freezer):
    monitor = FrigateHealth(hass, {"frigate_pipeline_health": True, "clip_cameras": ["camera.shed", "camera.garden"]})
    body = {"cameras": {"shed": {"camera_fps": 5, "process_fps": 5, "detection_fps": 0},
                        "garden": {"camera_fps": 0, "process_fps": 0}}}
    monitor._received(SimpleNamespace(payload="online"), "available")
    monitor._received(SimpleNamespace(payload=json.dumps(body), retain=True), "stats")
    assert not monitor.report(hass_admin_user)["stats_fresh"]
    monitor._received(SimpleNamespace(payload=json.dumps(body), retain=False), "stats")
    cameras = {row["camera"]: row for row in monitor.report(hass_admin_user)["cameras"]}
    assert cameras["camera.shed"]["processing"] == "reporting"
    assert cameras["camera.shed"]["last_event_received"] is None
    assert cameras["camera.garden"]["processing"] == "zero_rate"
    monitor._received(SimpleNamespace(payload=json.dumps({"type": "new", "after": event(camera="shed")}), retain=False), "events")
    assert monitor.report(hass_admin_user)["cameras"][1]["last_event_received"]
    freezer.tick(timedelta(minutes=4))
    assert not monitor.report(hass_admin_user)["stats_fresh"]
    monitor.configure({})
    assert monitor.report(hass_admin_user) == {"enabled": False}
