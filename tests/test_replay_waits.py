"""15 Nov: replaying a real day hung — face teaching waits a few seconds after someone leaves the gate, and on the
replay's simulated clock that wait never ended. Waits now pass at once in a replay; the order is kept."""

import asyncio
from pathlib import Path
import sys

from homeassistant.core import HomeAssistant

from custom_components.sentient import faces  # before Home Assistant loads custom integrations

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "scripts" / "replay"))
from runner import async_replay  # noqa: E402


async def test_a_long_wait_inside_sentient_doesnt_hang_the_replay(hass: HomeAssistant, freezer) -> None:
    waited = []
    real = faces.Faces._after_visit

    async def visit(self, camera):
        waited.append(camera)
        await asyncio.sleep(3600)  # an hour on a frozen clock: hangs without the fix
        waited.append("done")

    day = {"from": "2026-11-15T08:00:00+00:00", "to": "2026-11-15T08:10:00+00:00",
           "options": {"persona": "Nova", "speakers": [], "face_teach": True, "face_teach_cameras": ["camera.gate"]},
           "states": {"binary_sensor.gate_person_occupancy": {"state": "on", "attributes": {"device_class": "occupancy"}},
                      "camera.gate": {"state": "idle", "attributes": {}}},
           "changes": [{"ts": "2026-11-15T08:01:00+00:00", "entity": "binary_sensor.gate_person_occupancy",
                        "old": "on", "new": "off", "cause": "device"}]}
    faces.Faces._after_visit = visit
    try:
        result = await asyncio.wait_for(async_replay(hass, day, freezer), 120)
    finally:
        faces.Faces._after_visit = real
    assert result["changes"] == 1 and waited == ["gate", "done"]
