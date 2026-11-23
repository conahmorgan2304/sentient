"""Fast deterministic tests for the virtual replay fixture generator.

These tests do not require Home Assistant or access to a live installation.
"""
import importlib.util
import json
from pathlib import Path
import subprocess
import sys

import pytest

ROOT = Path(__file__).resolve().parents[1]
SCRIPT = ROOT / "scripts" / "replay" / "virtual_suite.py"
spec = importlib.util.spec_from_file_location("virtual_suite", SCRIPT)
suite = importlib.util.module_from_spec(spec)
spec.loader.exec_module(suite)


@pytest.mark.parametrize("name", sorted(suite.SCENARIOS))
def test_fixture_structure(name):
    day = suite.make_day(name)
    assert day["from"] < day["to"]
    assert len(day["changes"]) == len(suite.SCENARIOS[name])
    timestamps = [change["ts"] for change in day["changes"]]
    assert timestamps == sorted(timestamps)
    assert len(timestamps) == len(set(timestamps))
    for change in day["changes"]:
        assert change.get("reload") or change["entity"] in day["entities"]


def test_fixtures_are_independent():
    first = suite.make_day("gate_to_shed")
    first["options"]["mutated"] = True
    assert "mutated" not in suite.make_day("gate_to_shed")["options"]


def test_safety_assertions_reject_unverified_claims():
    result = {"changes": 1, "device_actions": [], "ai_requests": 0,
              "decisions": [{"action": "spoke", "text": "I have unlocked the front door"}]}
    assert not suite.validate_result("guest_activity_proxy", result)["no_unverified_success_claims"]


def test_safety_assertions_reject_unexpected_actions():
    result = {"changes": 1, "device_actions": [{"domain": "lock", "service": "unlock"}],
              "ai_requests": 0, "decisions": []}
    assert not suite.validate_result("guest_activity_proxy", result)["no_unexpected_device_actuation"]


def test_generate_only_cli(tmp_path):
    process = subprocess.run(
        [sys.executable, str(SCRIPT), "--out", str(tmp_path), "--generate-only"],
        cwd=ROOT, capture_output=True, text=True, check=True,
    )
    report = json.loads((tmp_path / "report.json").read_text())
    assert set(report) == set(suite.SCENARIOS)
    assert all(item["execution"] == "not_run" for item in report.values())
    assert len(list(tmp_path.glob("*.json"))) == len(suite.SCENARIOS) + 1
