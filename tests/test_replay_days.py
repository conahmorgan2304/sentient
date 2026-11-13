"""Regression days: recorded (made-up) days replayed through the current code, with what must happen.

Each day in tests/fixtures/replay/ has an "expect" block: how many times each source may speak or be held back,
and an upper limit on AI requests. A bug found in a real house becomes a day here, so it can't come back
unnoticed. The same days run through two versions with scripts/replay.py bench.
"""
from collections import Counter
import json
from pathlib import Path
import sys

import pytest

import custom_components.sentient  # noqa: F401 - this checkout's code, before the test plugin's own custom_components

sys.path.insert(0, str(Path(__file__).parents[1] / "scripts" / "replay"))
from runner import async_replay  # noqa: E402

DAYS = sorted((Path(__file__).parent / "fixtures" / "replay").glob("*.json"))


@pytest.mark.parametrize("path", DAYS, ids=[p.stem for p in DAYS])
async def test_a_recorded_day_decides_what_it_should(hass, freezer, path: Path) -> None:
    day = json.loads(path.read_text(encoding="utf-8"))
    result = await async_replay(hass, day, freezer)
    expect = day["expect"]
    spoken = Counter(d["source"] for d in result["decisions"] if d["action"] == "spoke")
    held = Counter(d["source"] for d in result["decisions"] if d["action"] == "held")
    for source, n in expect.get("spoken", {}).items():
        assert spoken[source] == n, f"{path.stem}: {source} spoke {spoken[source]} times, expected {n}: {result['decisions']}"
    for source, n in expect.get("held", {}).items():
        assert held[source] == n, f"{path.stem}: {source} held {held[source]} times, expected {n}"
    phoned = Counter(d["source"] for d in result["decisions"] if d["phoned"])
    for source, n in expect.get("phoned_sources_at_most", {}).items():
        assert phoned[source] <= n, f"{path.stem}: {source} went to phones {phoned[source]} times (at most {n})"
    assert result["ai_requests"] <= expect.get("ai_requests_at_most", 1000)
    assert not result["device_actions"] or expect.get("device_actions", True)


def test_benchmark_rejects_a_missing_important_security_alert() -> None:
    """Removing a safety notification must not look like a quieter, better version."""
    import importlib.util
    script = Path(__file__).resolve().parents[1] / "scripts" / "replay.py"
    spec = importlib.util.spec_from_file_location("sentient_replay_benchmark", script)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    alert = {"time": "2026-11-10T20:00:00+00:00", "action": "spoke", "kind": "said",
             "source": "camera security", "priority": "urgent", "phoned": False,
             "text": "Person at the gate"}
    old = {"decisions": [alert], "ai_requests": 0}
    new = {"decisions": [], "ai_requests": 0}
    _, failures = module.compare(old, new)
    assert any("protected" in item and "disappeared" in item for item in failures)
    ordinary = {**alert, "source": "welcome", "priority": "low"}
    _, ordinary_failures = module.compare({"decisions": [ordinary], "ai_requests": 0}, new)
    assert not ordinary_failures
