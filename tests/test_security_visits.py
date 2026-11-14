"""Security page events: only what entered a zone, grouped into visits, and corrections that do something."""
from unittest.mock import patch

from homeassistant.util import dt as dt_util

from custom_components.sentient.incidents import Incidents
from tests.test_security_events import event, setup


def at(seconds_ago, **extra):
    now = dt_util.utcnow().timestamp()
    return event(start_time=now - seconds_ago, end_time=now - seconds_ago + 5, **extra)


async def test_a_camera_with_zones_lists_only_what_entered_one(hass):
    events, _cache, rt = setup(hass)
    added = []
    rt.incidents = type("I", (), {"add": lambda self, *a, **k: added.append(k["event_id"])})()
    events.ingest(at(600, id="in.zone", zones=["driveway"]))
    street = at(300, id="street", zones=[])
    events.ingest(street)  # walked past outside every zone
    assert [r["id"] for r in events.recent()] == ["in.zone"]
    assert [v["id"] for v in events.visits()] == ["in.zone"]
    assert added == ["in.zone"]  # nothing outside the zones starts a security incident
    # the same object later walks into the zone: now it's listed, and counts
    events.ingest({**street, "entered_zones": ["driveway"]})
    assert {r["id"] for r in events.recent()} == {"in.zone", "street"} and added == ["in.zone", "street"]
    events.configure({**events.options, "security_event_zones": "all"})
    events.ingest(at(100, id="anything", zones=[]))
    assert "anything" in {r["id"] for r in events.recent()}


async def test_a_camera_without_zones_lists_everything(hass):
    events, _cache, _rt = setup(hass)
    events.ingest(at(60, zones=[]))
    assert [r["id"] for r in events.recent()] == ["123.event"]


async def test_frigate_says_which_cameras_have_zones(hass, aioclient_mock):
    events, _cache, _rt = setup(hass)
    aioclient_mock.get("http://frigate:5000/api/config", json={"cameras": {
        "garden": {"zones": {"lawn": {}}}, "doorbell": {"zones": {}}}})
    with patch("custom_components.sentient.security_events.frigate_url", return_value="http://frigate:5000"):
        await events._async_zoned()
    assert events._zoned == {"garden"}
    events.ingest(at(60, zones=[]))
    assert events.recent() == []  # garden has zones, and this never entered one


async def test_hidden_zones_drop_movement_only_there(hass):
    events, _cache, _rt = setup(hass, security_event_hidden_zones=["garden/hedge", "other/lawn"])
    events.ingest(at(900, id="hedge.only", zones=["hedge"]))
    events.ingest(at(300, id="hedge.and.lawn", zones=["hedge", "lawn"]))
    visits = events.visits()
    assert [v["id"] for v in visits] == ["hedge.and.lawn"] and visits[0]["zones"] == ["lawn"]


async def test_events_within_a_minute_are_one_visit(hass):
    events, _cache, _rt = setup(hass)
    events.ingest(at(400, id="a", zones=["path"]))
    events.ingest(at(360, id="b", zones=["path"], label="cat", has_snapshot=False))
    events.ingest(at(330, id="c", zones=["gate"]))
    events.ingest(at(60, id="later", zones=["path"]))
    visits = events.visits()
    assert [v["ids"] for v in visits] == [["later"], ["a", "b", "c"]]  # newest visit first
    first = visits[1]
    assert first["labels"] == ["person", "cat"] and first["zones"] == ["path", "gate"]
    assert first["snapshot"].endswith("/a/thumbnail.jpg")
    out = events.feedback("a", "not_useful", also=first["ids"] + ["not.retained"])
    assert out["event_ids"] == ["a", "b", "c"]
    assert all(events.items[i]["feedback"]["value"] == "not_useful" for i in "abc")
    assert events.visits()[1]["feedback"]["value"] == "not_useful"


async def test_not_a_person_reviews_an_incident_made_only_of_that_visit(hass):
    events, _cache, rt = setup(hass, security_incidents=True)
    rt.incidents = Incidents(hass, "visits", rt.options)
    for i in ("a", "b"):
        events.ingest(at(300, id=i, zones=["path"]))
    events.ingest(at(5000, id="mixed", zones=["path"]))
    camera_only = next(i for i in rt.incidents.items if any(e["event_id"] == "a" for e in i["evidence"]))
    mixed = next(i for i in rt.incidents.items if any(e["event_id"] == "mixed" for e in i["evidence"]))
    mixed["evidence"].append({"time": mixed["start"], "source": "binary_sensor.gate", "kind": "contact",
                              "status": "observed", "text": "Gate opened", "event_id": None})
    assert events.feedback("a", "false_positive", also=["b"])["incidents_reviewed"] == 1
    assert camera_only["household_review"]["label"] == "false_detection"
    assert [r["label"] for r in rt.incidents.reviews] == ["false_detection"]  # counts towards authority
    shown = {i["id"]: i for i in rt.incidents.recent()}
    assert shown[camera_only["id"]]["reviewed"] is True and shown[mixed["id"]]["reviewed"] is False
    assert all("household_review" not in i for i in shown.values())  # the label itself stays private
    assert events.feedback("mixed", "false_positive")["incidents_reviewed"] == 0 and "household_review" not in mixed
    # changing the answer takes back only the review made here
    events.feedback("a", "useful", also=["b"])
    assert "household_review" not in camera_only and rt.incidents.reviews == []
    # a review given on the Mind tab is never replaced
    rt.incidents.review(camera_only["id"], "visitor")
    events.feedback("a", "false_positive", also=["b"])
    assert camera_only["household_review"]["label"] == "visitor"


async def test_a_zone_marked_not_a_person_three_times_is_suggested_for_hiding(hass):
    events, _cache, _rt = setup(hass)
    for n in range(3):
        events.ingest(at(1000 - n * 200, id=f"e{n}", zones=["hedge"]))
        assert events.feedback_summary() == [] or events.feedback_summary()[0]["zones"] == []
        events.feedback(f"e{n}", "false_positive")
    assert events.feedback_summary()[0]["zones"] == [{"zone": "hedge", "not_a_person": 3}]
    events.configure({**events.options, "security_event_hidden_zones": ["garden/hedge"]})
    assert events.feedback_summary()[0]["zones"] == []


async def test_hiding_a_zone_needs_an_admin_and_changes_only_sentient(hass, hass_ws_client, hass_read_only_user):
    from tests.test_phase3 import _setup

    entry = await _setup(hass, security_event_evidence=True, clip_cameras=["camera.garden"])
    ws = await hass_ws_client(hass)
    await ws.send_json({"id": 1, "type": "sentient/security/zone/hide", "camera": "camera.garden", "zone": "hedge"})
    result = await ws.receive_json()
    assert result["success"] and result["result"]["detector_settings_changed"] is False
    assert entry.options["security_event_hidden_zones"] == ["garden/hedge"]
    await ws.send_json({"id": 2, "type": "sentient/security/zone/hide", "camera": "garden", "zone": "hedge", "hide": False})
    assert (await ws.receive_json())["success"] and entry.options["security_event_hidden_zones"] == []
    await ws.send_json({"id": 3, "type": "sentient/security/zone/hide", "camera": "a/b", "zone": "hedge"})
    assert not (await ws.receive_json())["success"]
    refresh = await hass.auth.async_create_refresh_token(hass_read_only_user, client_id="https://test.example")
    other = await hass_ws_client(hass, hass.auth.async_create_access_token(refresh))
    await other.send_json({"id": 1, "type": "sentient/security/zone/hide", "camera": "garden", "zone": "hedge"})
    assert not (await other.receive_json())["success"]


async def test_the_zone_settings_are_in_camera_security(hass, hass_ws_client):
    from tests.test_phase3 import _setup

    await _setup(hass)
    ws = await hass_ws_client(hass)
    await ws.send_json({"id": 1, "type": "sentient/settings"})
    result = (await ws.receive_json())["result"]
    assert result["data"]["security_event_zones"] == "zones" and result["data"]["security_event_hidden_zones"] == []
    camsec = next(g for g in result["groups"] if g["id"] == "camsec")
    assert {"security_event_zones", "security_event_hidden_zones"} <= {f["name"] for f in camsec["schema"]}
