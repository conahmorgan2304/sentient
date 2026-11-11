"""Regression tests for the problems found reviewing PR #127 (cognitive foundation)."""
import json
from types import SimpleNamespace
from unittest.mock import AsyncMock, patch

from homeassistant.core import Context

from custom_components.sentient.ai import AIUnavailable
from custom_components.sentient.incident_reasoning import assess_incident
from custom_components.sentient.manuals import async_answer
from custom_components.sentient.plan_review import review_plan
from custom_components.sentient.security import check
from custom_components.sentient.world_model import prompt_view, summarise_world


# --- security: an alarm going off beside a sensor with no reading must not sound "all closed" ---------------------
async def test_alarm_with_an_unsettled_door_still_says_it_cannot_confirm(hass) -> None:
    hass.states.async_set("binary_sensor.back_door", "unavailable", {"device_class": "door", "friendly_name": "Back door"})
    hass.states.async_set("alarm_control_panel.house", "triggered", {"friendly_name": "Alarm"})
    report = check(hass)
    assert report["secure"] is False
    assert report["assessment"]["verdict"] == "problem" and report["assessment"]["confirmed"] is False
    assert report["summary"].startswith("I can't confirm the whole perimeter.")
    assert "report closed" not in report["summary"]


async def test_alarm_alone_with_no_perimeter_sensors_reads_sensibly(hass) -> None:
    hass.states.async_set("alarm_control_panel.house", "triggered", {"friendly_name": "Alarm"})
    report = check(hass)
    assert report["secure"] is False
    assert not report["summary"].startswith(".")
    assert "can't confirm" in report["summary"]


async def test_fully_reported_alarm_problem_keeps_its_plain_wording(hass) -> None:
    hass.states.async_set("binary_sensor.back_door", "off", {"device_class": "door", "friendly_name": "Back door"})
    hass.states.async_set("alarm_control_panel.house", "triggered", {"friendly_name": "Alarm"})
    report = check(hass)
    assert report["secure"] is False and report["assessment"]["confirmed"] is True
    assert "report closed" in report["summary"]


# --- guarded plans: stopping a cover must never wait for a yes ------------------------------------------------------
def test_stopping_a_cover_is_not_held_for_confirmation() -> None:
    assert review_plan([{"entity_id": "cover.garage", "action": "stop_cover"}], validated=True).requires_confirmation is False
    assert review_plan([{"entity_id": "cover.garage", "action": "open_cover"}], validated=True).requires_confirmation is True
    assert review_plan([{"entity_id": "lock.front", "action": "lock"}], validated=True).requires_confirmation is True
    assert review_plan([{"entity_id": "light.hall", "action": "turn_off"}], validated=True).requires_confirmation is False


# --- incidents: Frigate events are camera evidence -------------------------------------------------------------------
def test_frigate_event_counts_as_camera_evidence() -> None:
    result = assess_incident({"evidence": [
        {"kind": "contact", "status": "observed", "source": "binary_sensor.gate"},
        {"kind": "event", "status": "observed", "source": "camera.gate"},
    ]})
    assert "camera_detection_report" in result["reasons"]
    assert "contact_and_camera_activity" in result["reasons"]
    assert result["corroboration"] == "reported_activity"
    assert result["identity_confirmed"] is False and result["route_confirmed"] is False


def test_visits_are_named_and_never_corroborate_on_their_own() -> None:
    result = assess_incident({"evidence": [{"kind": "visit", "status": "observed", "source": "camera.door"}]})
    assert "doorbell_or_doorstep_visit" in result["reasons"]
    assert result["corroboration"] == "unconfirmed"


# --- world model: the prompt carries counts and only the readings that need care -------------------------------------
def test_prompt_view_is_compact_and_flags_only_what_needs_care() -> None:
    readings = [{"entity_id": f"sensor.s{i}", "status": "reported", "state": "1", "reported_at": "2026-11-11T16:59:00+00:00"}
                for i in range(7)]
    readings.append({"entity_id": "binary_sensor.shed", "status": "unknown", "state": "unavailable",
                     "reported_at": "2026-11-11T16:59:00+00:00"})
    full = summarise_world({"as_of": "2026-11-11T17:00:00+00:00", "current_readings": readings})
    small = prompt_view(full)
    assert small["needs_care"] == [{"entity_id": "binary_sensor.shed", "certainty": "unknown"}]
    assert small["uncertainty"]["unknown_count"] == 1
    assert len(json.dumps(small)) < len(json.dumps(full)) / 2


async def test_house_context_embeds_the_compact_view(hass) -> None:
    from tests.test_house_context import engine, heat

    obj, _rt = engine(hass)
    heat(hass)
    with patch("custom_components.sentient.house_context.async_should_expose", return_value=True):
        data = await obj.snapshot(Context(), request="house")
    assert set(data["world_assessment"]) == {"uncertainty", "needs_care", "recent_change_count", "coverage", "interpretation"}
    assert "observations" not in data["world_assessment"]


# --- manuals: look inside text files and quote one when the AI is down, behind its own switch ------------------------
def _rt(**options):
    ai = SimpleNamespace(spend=lambda *a, **k: None, async_call=AsyncMock(side_effect=AIUnavailable("the AI is unavailable")))
    return SimpleNamespace(options={"manuals": True, "manuals_folder": "manuals", **options}, ai=ai)


def _folder(hass, tmp_path):
    folder = tmp_path / "manuals"
    folder.mkdir()
    (folder / "notes.txt").write_text("Unpack carefully.\n\nThe kettle filter is model KF42 and lifts out from the spout.\n\n"
                                      "Descale monthly.", encoding="utf-8")
    hass.config.media_dirs = {"local": str(tmp_path)}


async def test_text_manual_is_quoted_when_the_ai_is_unavailable(hass, tmp_path) -> None:
    _folder(hass, tmp_path)
    result = await async_answer(hass, _rt(), "which kettle filter model?")
    assert result["document"] == "notes" and result["ai_unavailable"] is True
    assert result["passage"] == "The kettle filter is model KF42 and lifts out from the spout."


async def test_switching_text_search_off_matches_by_file_name_only(hass, tmp_path) -> None:
    _folder(hass, tmp_path)
    result = await async_answer(hass, _rt(manuals_local_text=False), "which kettle filter model?")
    assert "error" in result and "passage" not in result


async def test_passage_never_reads_outside_the_folder(tmp_path) -> None:
    from custom_components.sentient.document_retrieval import best_passage

    (tmp_path / "secret.txt").write_text("kettle filter secret", encoding="utf-8")
    inner = tmp_path / "manuals"
    inner.mkdir()
    (inner / "link.txt").symlink_to(tmp_path / "secret.txt")
    assert best_passage(str(inner), "../secret.txt", "kettle filter") is None
    assert best_passage(str(inner), "link.txt", "kettle filter") is None
    assert best_passage(str(inner), "manual.pdf", "kettle filter") is None
