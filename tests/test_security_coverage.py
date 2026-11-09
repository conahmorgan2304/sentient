"""Historical outages, present availability and permission-scoped incident reports."""
from datetime import timedelta
from unittest.mock import patch

from homeassistant.helpers import llm
from homeassistant.util import dt as dt_util

from custom_components.sentient.incidents import Incidents
from custom_components.sentient.security_voice import SecurityEventTool
from custom_components.sentient.voice_api import IncidentsTool
from tests.test_security_events import context, event, setup


async def test_coverage_distinguishes_historical_and_current_camera_gaps(hass, freezer):
    events, _, rt = setup(hass, security_incidents=True, security_coverage=True,
                          clip_cameras=["camera.garden", "camera.shed", "camera.front", "camera.hidden"])
    incidents = rt.incidents = Incidents(hass, "test", rt.options)
    hass.states.async_set("camera.garden", "unavailable")
    hass.states.async_set("camera.shed", "idle")
    hass.states.async_set("camera.front", "idle")
    hass.states.async_set("binary_sensor.gate", "on")
    hass.states.async_set("binary_sensor.secret", "on")
    when = dt_util.utcnow()
    incidents.add("binary_sensor.gate", "Gate opened", "contact")
    freezer.tick(timedelta(seconds=12))
    events.ingest(event(camera="shed", start_time=dt_util.utcnow().timestamp(), end_time=None))
    incidents.add("camera.front", "Secret motion", "detection", read_source="binary_sensor.secret")
    hass.states.async_set("camera.garden", "idle")
    hass.states.async_set("camera.front", "unavailable")

    with patch("custom_components.sentient.voice_api._runtime", return_value=rt), \
            patch("custom_components.sentient.security_voice._runtime", return_value=rt), \
            patch("custom_components.sentient.security_voice.async_should_expose",
                  side_effect=lambda h, a, eid: eid not in {"camera.hidden", "binary_sensor.secret"}):
        result = await IncidentsTool().async_call(hass, llm.ToolInput(tool_name="sentient_incidents", tool_args={}), context())
        exact = await SecurityEventTool().async_call(
            hass, llm.ToolInput(tool_name="sentient_security_event",
                                tool_args={"action": "detail", "event_id": "123.event"}), context()
        )
        assert "Secret motion" not in str(result) and "camera.hidden" not in str(result)
        report, = result["incidents"]
        assert [r["text"] for r in report["evidence"]] == ["Gate opened", "Frigate reported person"]
        assert dt_util.parse_datetime(report["end"]) - dt_util.parse_datetime(report["start"]) == timedelta(seconds=12)
        assert report["evidence"][1]["seconds_from_first_observation"] == 12
        for coverage in (report["coverage"], exact["coverage"]):
            assert coverage["route_confirmed"] is False
            cameras = {r["camera"]: r for r in coverage["cameras"]}
            assert cameras["camera.garden"]["during_incident"] == {
                "state": "unavailable", "observed_at": when.isoformat(),
            }
            assert cameras["camera.garden"]["current_ha_report"] == "idle"
            assert cameras["camera.shed"]["linked_evidence"] is True
            assert cameras["camera.front"]["linked_evidence"] is False
            assert cameras["camera.front"]["current_ha_report"] == "unavailable"
            assert "during_incident" not in cameras["camera.front"]
            assert "camera.hidden" not in cameras
        incidents.configure({**rt.options, "security_coverage": False})
        assert all("camera_gaps" not in row for row in incidents.items[0]["evidence"])
