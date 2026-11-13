"""The cognitive core deciding incident stages: earned per kind of incident from the household's reviews, never
lowering a concerning incident, never touching an alert, and stopping by itself when later reviews fall short.

The last test follows one incident through the whole chain: a gate contact and a camera report become one
incident's evidence, the cognitive core assesses it, the alert decision is made and recorded, and the household's
review is recorded against what the core said.
"""
from datetime import datetime, timedelta

from homeassistant.core import HomeAssistant
from homeassistant.util import dt as dt_util
from pytest_homeassistant_custom_component.common import MockConfigEntry, async_mock_service

from custom_components.sentient.cognitive_coordinator import authority, decided_stage
from custom_components.sentient.const import DOMAIN


def _reviews(household_right=10, household_wrong=0, visitors=10, called_household=0, missed=0):
    rows = [{"label": "household", "core": "likely household", "prediction_recorded": True}] * household_right
    rows += [{"label": "household", "core": "observed", "prediction_recorded": True}] * household_wrong
    rows += [{"label": "visitor", "core": "observed", "prediction_recorded": True}] * (visitors - called_household)
    rows += [{"label": "visitor", "core": "likely household", "prediction_recorded": True}] * called_household
    rows += [{"label": "concerning", "core": "concerning", "prediction_recorded": True}] * 3
    rows += [{"label": "concerning", "core": "observed", "prediction_recorded": True}] * missed
    return rows


def test_it_earns_the_stage_only_when_each_kind_is_right() -> None:
    assert authority(_reviews())["earned"]
    few = authority(_reviews(household_right=9))
    assert not few["earned"] and few["why"] == ["needs 1 more reviewed household incidents"]
    assert not authority(_reviews(household_right=9, household_wrong=2))["earned"]  # 82% on household
    assert not authority(_reviews(visitors=12, called_household=1))["earned"]  # 1 in 12 wrongly household
    # a high overall score doesn't hide a missed threat: 20 right and one concerning called less serious
    missed = authority(_reviews(missed=1))
    assert not missed["earned"] and missed["progress"]["missed_threats"] == 1
    assert missed["why"] == ["called 1 concerning incident less serious"]
    assert not authority(_reviews(visitors=0))["earned"]  # never judged on anything but household


def test_it_never_calls_a_concerning_incident_less_serious() -> None:
    assert decided_stage("concerning", "likely household", True) == ("concerning", "rules (the core can't lower a concerning incident)")
    assert decided_stage("investigating", "concerning", True) == ("concerning", "cognitive core")
    assert decided_stage("investigating", "likely household", False) == ("investigating", "rules")


async def _setup(hass: HomeAssistant, **options) -> MockConfigEntry:
    for s, name in (("gate_person", "Gate Person occupancy"), ("garden_person", "Garden Person occupancy")):
        hass.states.async_set(f"binary_sensor.{s}", "off", {"friendly_name": name})
    hass.states.async_set("binary_sensor.back_gate", "off", {"friendly_name": "Back gate", "device_class": "door"})
    hass.states.async_set("assist_satellite.bedroom", "idle", {"supported_features": 3})
    entry = MockConfigEntry(domain=DOMAIN, title="Nova", unique_id=DOMAIN, data={
        "persona": "Nova", "speakers": ["assist_satellite.bedroom"], "default_speaker": "assist_satellite.bedroom",
        "quiet_start": "21:00:00", "quiet_end": "07:00:00", "camera_security": True, "security_incidents": True,
        "camera_security_sensors": ["binary_sensor.gate_person", "binary_sensor.garden_person"],
        "camera_security_inside": ["binary_sensor.garden_person"], "camera_security_entries": ["binary_sensor.back_gate"],
        "camera_security_describe": False, "night_watch_announce": True, "inside_sensors": [], **options})
    entry.add_to_hass(hass)
    assert await hass.config_entries.async_setup(entry.entry_id)
    await hass.async_block_till_done()
    return entry


async def test_reviews_earn_it_the_stage_and_the_timeline_says_so(hass: HomeAssistant, freezer) -> None:
    freezer.move_to(datetime(2026, 11, 10, 12, 0, tzinfo=dt_util.UTC))
    entry = await _setup(hass, cognitive_decides=True)
    incidents = entry.runtime_data.incidents
    assert not incidents.authority()["deciding"]
    for n in range(20):
        freezer.tick(timedelta(minutes=10))
        household = n < 10
        item = incidents.add("camera.gate", "Person reported at Gate" + (" — someone from the house" if household else ""),
                             "detection")
        assert incidents.review(item["id"], "household" if household else "visitor")["changed"]
    for n in range(3):
        freezer.tick(timedelta(minutes=10))
        item = incidents.add("camera.gate", "Person reported at Gate", "detection")
        incidents.finish_alert(item["id"], 2, True)
        incidents.review(item["id"], "concerning")
    status = incidents.authority()
    assert status["deciding"], status["why"]
    assert any("now decides the stage" in i["text"] for i in entry.runtime_data.timeline.items)
    row = incidents.recent()[0]
    assert row["stage_by"] == "cognitive core"
    # later reviews say it called a concerning incident harmless: it stops, and says why
    freezer.tick(timedelta(minutes=10))
    item = incidents.add("camera.gate", "Person reported at Gate — someone from the house", "detection")
    incidents.review(item["id"], "concerning")
    assert not incidents.authority()["deciding"]
    assert any("stopped deciding" in i["text"] and "less serious" in i["text"] for i in entry.runtime_data.timeline.items)
    assert incidents.recent()[0]["stage_by"] == "rules"


async def test_switched_off_the_rules_decide_and_reviews_are_kept(hass: HomeAssistant) -> None:
    entry = await _setup(hass)
    incidents = entry.runtime_data.incidents
    incidents.reviews = _reviews()
    assert incidents.authority()["earned"] and not incidents.authority()["deciding"]
    incidents.add("camera.gate", "Person reported at Gate", "detection")
    assert incidents.recent()[0]["stage_by"] == "rules"


async def test_one_incident_through_the_whole_chain(hass: HomeAssistant, freezer) -> None:
    """Gate contact and camera → one incident's evidence → the core's assessment → the alert decision, recorded →
    the household's review recorded against what the core said. A broken link fails here."""
    freezer.move_to(dt_util.as_utc(datetime(2026, 11, 10, 2, 0, tzinfo=dt_util.get_default_time_zone())))
    entry = await _setup(hass, cognitive_shadow=True, cognitive_decides=True)
    rt = entry.runtime_data
    said = async_mock_service(hass, "assist_satellite", "announce")
    freezer.tick(timedelta(minutes=20))
    hass.states.async_set("binary_sensor.back_gate", "on", {"friendly_name": "Back gate", "device_class": "door"})
    await hass.async_block_till_done()
    freezer.tick(timedelta(seconds=20))
    hass.states.async_set("binary_sensor.gate_person", "on", {"friendly_name": "Gate Person occupancy"})
    await hass.async_block_till_done(wait_background_tasks=True)

    # 1. both reports are one incident's evidence
    [incident] = [i for i in rt.incidents.items if i["sources"]]
    kinds = {e["kind"] for e in incident["evidence"]}
    assert {"contact", "detection"} <= kinds, incident["evidence"]
    # 2. the alert decision was made and recorded on the timeline against that incident, and spoken at night
    alerts = [i for i in rt.timeline.items if i.get("alert")]
    assert alerts and alerts[0]["incident_id"] == incident["id"] and said
    # 3. the cognitive core assessed it (alongside the rules), and the Mind shows who decided the stage
    row = rt.incidents.recent()[0]
    assert row["cognitive_shadow"]["stage"] and row["stage_by"] == "rules"  # not earned yet
    # 4. the household's verdict is recorded against what the core said, and counts towards earning the stage
    rt.incidents.review(incident["id"], "household")
    assert rt.incidents.reviews[-1]["core"] == row["cognitive_shadow"]["stage"]
    assert rt.incidents.authority()["progress"]["household_reviewed"] == 1


# ----- Phase 3: reasoning the rules can't do, scored against the rules, and a second decision -------------------
def _row(kind, source, seconds, text="", status="observed"):
    base = datetime(2026, 11, 10, 23, 0, tzinfo=dt_util.UTC)
    return {"kind": kind, "source": source, "status": status, "text": text,
            "time": (base + timedelta(seconds=seconds)).isoformat()}


def test_the_core_reads_the_order_things_happened_in() -> None:
    from custom_components.sentient.cognitive_coordinator import arrival, investigate

    gate_then_camera = {"evidence": [_row("contact", "binary_sensor.gate", 0, "Gate: on"),
                                     _row("detection", "camera.drive", 30, "Person reported at Drive")]}
    assert arrival(gate_then_camera) == (True, False)
    camera_then_gate = {"evidence": [_row("detection", "camera.drive", 0), _row("contact", "binary_sensor.gate", 30, "Gate: on")]}
    assert arrival(camera_then_gate) == (False, False)  # seen before anything opened
    too_late = {"evidence": [_row("contact", "binary_sensor.gate", 0, "Gate: on"), _row("detection", "camera.drive", 600)]}
    assert arrival(too_late) == (False, False)
    rang = {"evidence": [_row("visit", "camera.door", 0, "Someone at the door")]}
    assert arrival(rang) == (False, True)
    now = datetime(2026, 11, 10, 23, 5, tzinfo=dt_util.UTC)
    end = {"end": (now - timedelta(minutes=1)).isoformat()}
    assert investigate({**gate_then_camera, **end, "alert_level": 1}, {}, baseline_stage="investigating", now=now)["stage"] == "investigating"
    assert investigate({**gate_then_camera, **end, "alert_level": 2}, {}, baseline_stage="concerning", now=now)["stage"] == "concerning"
    assert investigate({**rang, **end}, {}, baseline_stage="observed", now=now)["stage"] == "observed"


def test_the_rules_are_scored_the_same_way_so_its_clear_which_judges_better() -> None:
    rows = [{"label": "household", "core": "likely household", "legacy": "investigating", "prediction_recorded": True}] * 10
    rows += [{"label": "visitor", "core": "observed", "legacy": "observed", "prediction_recorded": True}] * 10
    rows += [{"label": "concerning", "core": "concerning", "legacy": "concerning", "prediction_recorded": True}] * 5
    status = authority(rows)
    assert status["compared"]["core"]["agreed"] == 10 and status["compared"]["rules"]["agreed"] == 0
    assert status["earned"] and not authority(rows, "night_heads_up")["earned"]  # asks for 20 household
    assert authority(rows * 2, "night_heads_up")["earned"]


async def test_a_night_heads_up_is_left_unsaid_only_when_it_has_earned_it_and_is_sure(hass: HomeAssistant, freezer) -> None:
    freezer.move_to(dt_util.as_utc(datetime(2026, 11, 10, 2, 0, tzinfo=dt_util.get_default_time_zone())))
    entry = await _setup(hass, cognitive_decides=True, cognitive_quiet_heads_up=True)
    rt = entry.runtime_data
    rt.incidents.reviews = ([{"label": "household", "core": "likely household", "legacy": "investigating", "prediction_recorded": True}] * 20
                            + [{"label": "visitor", "core": "observed", "legacy": "observed", "prediction_recorded": True}] * 10)
    assert not rt.incidents.authority("night_heads_up")["deciding"]  # no concerning reviews: never earned
    said = async_mock_service(hass, "assist_satellite", "announce")
    freezer.tick(timedelta(minutes=20))
    # someone coming in: the gate opens, then the gate camera sees them
    hass.states.async_set("binary_sensor.back_gate", "on", {"friendly_name": "Back gate", "device_class": "door"})
    await hass.async_block_till_done()
    freezer.tick(timedelta(seconds=20))
    hass.states.async_set("binary_sensor.gate_person", "on", {"friendly_name": "Gate Person occupancy"})
    await hass.async_block_till_done(wait_background_tasks=True)
    [alert] = [i for i in rt.timeline.items if i.get("alert") and i.get("incident_id")]  # the camera's
    camera_heads_up = [c for c in said if "reported by the gate" in c.data["message"]]
    assert camera_heads_up  # gate + camera proves activity, not household identity: never suppress
    # (the night watch's own "the back gate has just opened" is a separate heads-up and is unchanged)
    # someone seen with nothing opening first: said as always
    hass.states.async_set("binary_sensor.gate_person", "off", {"friendly_name": "Gate Person occupancy"})
    hass.states.async_set("binary_sensor.back_gate", "off", {"friendly_name": "Back gate", "device_class": "door"})
    freezer.tick(timedelta(hours=1))
    await hass.async_block_till_done()
    hass.states.async_set("binary_sensor.gate_person", "on", {"friendly_name": "Gate Person occupancy"})
    await hass.async_block_till_done(wait_background_tasks=True)
    assert any("reported by the gate" in c.data["message"] for c in said)


async def test_switched_off_every_heads_up_is_said(hass: HomeAssistant, freezer) -> None:
    freezer.move_to(dt_util.as_utc(datetime(2026, 11, 10, 2, 0, tzinfo=dt_util.get_default_time_zone())))
    entry = await _setup(hass, cognitive_decides=True)
    rt = entry.runtime_data
    rt.incidents.reviews = [{"label": "household", "core": "likely household", "prediction_recorded": True}] * 20 + [{"label": "visitor", "core": "observed", "prediction_recorded": True}] * 10
    said = async_mock_service(hass, "assist_satellite", "announce")
    freezer.tick(timedelta(minutes=20))
    hass.states.async_set("binary_sensor.back_gate", "on", {"friendly_name": "Back gate", "device_class": "door"})
    await hass.async_block_till_done()
    freezer.tick(timedelta(seconds=20))
    hass.states.async_set("binary_sensor.gate_person", "on", {"friendly_name": "Gate Person occupancy"})
    await hass.async_block_till_done(wait_background_tasks=True)
    assert any("reported by the gate" in c.data["message"] for c in said)

def test_historic_reviews_without_prediction_snapshots_cannot_earn_authority() -> None:
    # Prior versions recalculated old incidents when reviewed; those labels must remain
    # visible but cannot be counted as evidence for the cognitive core's promotion.
    bad = [{"label": "household", "core": "resolved"}] * 30
    bad += [{"label": "visitor", "core": "observed"}] * 20
    bad += [{"label": "concerning", "core": "concerning"}] * 10
    assert not authority(bad)["earned"]
    assert authority(bad)["progress"]["household_reviewed"] == 0


async def test_delayed_review_uses_event_time_snapshot(hass: HomeAssistant, freezer) -> None:
    freezer.move_to(datetime(2026, 11, 10, 12, 0, tzinfo=dt_util.UTC))
    entry = await _setup(hass)
    incidents = entry.runtime_data.incidents
    item = incidents.add("camera.gate", "Person reported at Gate", "detection")
    expected = dict(item["decision_snapshot"])
    assert expected["core"] != "resolved"
    freezer.tick(timedelta(hours=10))
    assert incidents.review(item["id"], "visitor")["changed"]
    review = incidents.reviews[-1]
    assert review["core"] == expected["core"]
    assert review["legacy"] == expected["legacy"]
    assert review["prediction_at"] == expected["at"]


def test_unobserved_or_animal_reports_are_not_person_arrivals() -> None:
    from custom_components.sentient.cognitive_coordinator import arrival
    contact = _row("contact", "binary_sensor.gate", 0, "Gate: open")
    unknown = _row("detection", "camera.gate", 10, "Person", status="unknown")
    animal = _row("event", "camera.gate", 10, "Cat detected")
    assert not arrival({"evidence": [contact, unknown]})[0]
    assert not arrival({"evidence": [contact, animal]})[0]
