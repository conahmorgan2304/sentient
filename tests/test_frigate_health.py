"""Passive Frigate telemetry distinguishes quiet cameras from failed processing."""
import json
from datetime import timedelta
from types import SimpleNamespace

from custom_components.sentient.frigate_health import FrigateHealth
from tests.test_security_events import event


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
