"""15 Nov: "what time did Kayleigh get back?" — the drive had logged her car arriving at 15:12, but the voice only saw
what was parked now, the timeline tool left out the drive, and the question didn't bring the timeline tool along."""

from homeassistant.core import HomeAssistant, ServiceCall, SupportsResponse
from homeassistant.helpers import llm

from custom_components.sentient.focus import Focus
from tests.test_phase3 import _setup


async def test_the_voice_hears_when_a_car_came_and_can_look_it_up(hass: HomeAssistant) -> None:
    hass.states.async_set("camera.gate", "idle")
    entry = await _setup(hass, known_cars="Sam: dark grey hatchback")
    rt = entry.runtime_data
    rt.spots.set("camera.gate", "drive", [0.1, 0.4, 0.6, 0.9])
    replies = iter([{"cars": 0}, {"cars": 1, "description": "dark grey hatchback", "whose": "Sam"}])

    async def generate(_call: ServiceCall):
        return {"conversation_id": "x", "data": next(replies)}

    hass.services.async_register("ai_task", "generate_data", generate, supports_response=SupportsResponse.ONLY)
    await rt.drive.async_check(force=True)
    await rt.drive.async_check(force=True)
    line = rt.drive.prompt_line()
    assert "Last change: Sam's car arrived on the drive" in line and "today" in line and "sentient_timeline" in line

    from custom_components.sentient.llm_api import TimelineTool

    tool = TimelineTool()
    context = llm.LLMContext(platform="test", context=None, language="en", assistant=None, device_id=None)
    out = await tool.async_call(hass, llm.ToolInput(tool_name=tool.name, tool_args={"hours": 12}), context)
    assert any("Sam's car arrived" in e["what"] for e in out["events"])


def test_asking_when_someone_got_back_brings_the_timeline() -> None:
    for asked in ("what time did kayleigh get back", "when did Sam get in?", "has the car arrived yet", "when she left for work", "when did they go out"):
        assert "sentient_timeline" in Focus().tools_for(asked), asked
    assert "sentient_timeline" not in (Focus().tools_for("turn on the left bedroom light") or set())


async def test_times_are_said_to_the_minute(hass: HomeAssistant) -> None:
    """15 Nov: the car arrived at 3:12pm and the voice said "past three o'clock"."""
    from datetime import timedelta

    from homeassistant.util import dt as dt_util

    from custom_components.sentient.watches import local_when
    from tests.test_phase1 import _prompt, _setup as setup_one

    await setup_one(hass)
    assert "say it to the minute" in await _prompt(hass)
    assert local_when(dt_util.now() - timedelta(days=1)).endswith("yesterday")
