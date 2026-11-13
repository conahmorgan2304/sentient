"""One replay: REPLAY_DAY (a recorded day) in, REPLAY_OUT (what this version decided) out.

Run through scripts/replay.py, not directly: it picks the version and compares the results.
"""
import json
import os
from pathlib import Path
import sys

import pytest

sys.path.insert(0, str(Path(__file__).parent))
from runner import async_replay  # noqa: E402

DAY, OUT = os.environ.get("REPLAY_DAY"), os.environ.get("REPLAY_OUT")


@pytest.mark.skipif(not DAY or not OUT, reason="run through scripts/replay.py")
async def test_replay_day(hass, freezer) -> None:
    day = json.loads(Path(DAY).read_text(encoding="utf-8"))
    result = await async_replay(hass, day, freezer)
    import custom_components.sentient as sentient

    result["code"] = str(Path(sentient.__file__).parent)
    Path(OUT).write_text(json.dumps(result, indent=1, default=str), encoding="utf-8")
