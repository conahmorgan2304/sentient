"""Real retained incident reviews are owner-only and never change alert policy."""
from homeassistant.util import dt as dt_util

from custom_components.sentient.incidents import Incidents
from tests.test_websocket import _setup


async def test_reviewed_incident_persists_and_does_not_affect_alerts(hass):
    incidents = Incidents(hass, "phase2-review", {"security_incidents": True})
    await incidents.async_start()
    row = incidents.add("camera.gate", "Person detected", "detection")
    assert row is not None
    iid = row["id"]
    assert incidents.review(iid, "visitor") == {
        "changed": True, "detector_settings_changed": False, "alert_policy_changed": False}
    report = incidents.reviewed_outcomes()
    assert report["reviewed_total"] == 1
    assert report["accuracy_established"] is False
    assert "camera.gate" not in str(report)
    assert all("household_review" not in r for r in incidents.recent())
    assert incidents.review(iid, "invalid")["error"]
    assert incidents.review(iid, "clear")["changed"]
    assert incidents.reviewed_outcomes()["reviewed_total"] == 0
    incidents.async_stop()


async def test_incident_review_websocket_requires_admin_and_rejects_unknown(hass, hass_ws_client, hass_read_only_access_token):
    entry = await _setup(hass)
    ws = await hass_ws_client(hass, hass_read_only_access_token)
    await ws.send_json({"id": 1, "type": "sentient/security/incident/review",
                        "incident_id": "nope", "outcome": "household"})
    assert (await ws.receive_json())["error"]["code"] == "unauthorized"
    admin = await hass_ws_client(hass)
    await admin.send_json({"id": 1, "type": "sentient/security/incident/review",
                           "incident_id": "nope", "outcome": "household"})
    assert (await admin.receive_json())["error"]["code"] == "invalid"
