"""Pure simulation does not call Home Assistant or trust matching stages as accuracy."""
from datetime import datetime, timezone

from custom_components.sentient.cognitive_replay import evaluate_scenarios

NOW = datetime(2026, 11, 13, 12, 0, tzinfo=timezone.utc)


def _incident(source1="binary_sensor.gate", source2="camera.garden", level=0):
    return {"id": "private-id-never-in-output", "end": NOW.isoformat(),
            "alert_level": level, "evidence": [
                {"kind": "contact", "source": source1, "status": "observed"},
                {"kind": "detection", "source": source2, "status": "observed"},
            ]}


def test_replay_distinguishes_labelled_correctness_from_stage_agreement():
    cases = [
        {"incident": _incident(), "expected_corroboration": "reported_activity"},
        {"incident": _incident(source2="binary_sensor.gate"),
         "expected_corroboration": "unconfirmed"},
        {"incident": _incident(level=2), "expected_stage": "concerning"},
    ]
    results = evaluate_scenarios(cases, now=NOW)
    assert results["scenarios_compared"] == 3
    assert results["scenarios_with_independent_labels"] == 1
    assert results["corroboration_correct"] == 2
    assert results["shadow_label_accuracy"] is not None
    assert results["automatic_actions"] == 0
    assert "private-id" not in str(results)


def test_replay_has_no_fake_accuracy_when_unlabelled():
    result = evaluate_scenarios([{"incident": _incident()}], now=NOW)
    assert result["legacy_label_accuracy"] is None
    assert result["shadow_label_accuracy"] is None


def test_replay_bounded_and_malformed_scenarios_never_crash():
    cases = [None, {}, {"incident": "not an incident"},
             {"incident": _incident(), "expected_stage": "safe"}]
    result = evaluate_scenarios(cases, now=NOW)
    assert result["invalid_scenario_count"] == 3
    assert result["invalid_label_count"] == 1
    assert result["scenarios_compared"] == 1
    assert result["automatic_actions"] == 0


def test_replay_skips_malformed_imported_scenarios_without_hiding_valid_results():
    valid = {"incident": _incident(), "expected_corroboration": "reported_activity"}
    cases = [
        {"incident": {"evidence": ["malformed event"], "alert_level": 0}},
        {"incident": {"evidence": [], "alert_level": "loud"}},
        {"incident": {"evidence": [], "alert_level": 0, "end": ["invalid date"]}},
        valid,
    ]
    report = evaluate_scenarios(cases, now=NOW)
    assert report["invalid_scenario_count"] == 3
    assert report["scenarios_compared"] == 1
    assert report["corroboration_correct"] == 1


def _cli():
    import importlib.util
    from pathlib import Path

    path = Path(__file__).resolve().parents[1] / "scripts" / "cognitive_replay.py"
    spec = importlib.util.spec_from_file_location("sentient_cognitive_replay", path)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module.main


def test_replay_command_outputs_only_aggregates(tmp_path, capsys):
    main = _cli()
    import json

    secret = "private-family-room-data"
    path = tmp_path / "cases.json"
    data = [{"incident": {**_incident(), "id": secret},
             "expected_corroboration": "reported_activity"}]
    path.write_text(json.dumps(data), encoding="utf-8")
    assert main([str(path), "--at", NOW.isoformat()]) == 0
    output = capsys.readouterr().out
    summary = json.loads(output)
    assert summary["scenarios_compared"] == 1
    assert summary["automatic_actions"] == 0
    assert secret not in output
    assert "camera.garden" not in output


def test_replay_command_rejects_malformed_input_without_echoing_data(tmp_path, capsys):
    main = _cli()
    import pytest

    path = tmp_path / "private-word.json"
    path.write_text('not valid JSON with private detail', encoding="utf-8")
    with pytest.raises(SystemExit) as result:
        main([str(path)])
    assert result.value.code == 2
    output = capsys.readouterr().err
    assert "private detail" not in output
    assert "private-word" not in output
