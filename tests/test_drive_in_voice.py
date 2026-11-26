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
    prompt = await _prompt(hass)
    assert "say it to the minute" in prompt
    assert "\"half past\" when it wasn't on the half hour" in prompt  # 16 Nov: 08:34 was said "half past eight"
    assert "start 21st and end 22nd is just the 21st" in prompt  # 16 Nov: an all-day event said a day early
    # 16 Nov: "Is it on?" with nothing said before was answered about the coffee machine
    assert "ask which one they mean" in prompt and "don't pick a device" in prompt
    # 16 Nov: told the scooter "was packed up just after one", it agreed without looking
    assert "don't agree or repeat it as fact unless a tool or this prompt shows it" in prompt
    assert "look again rather than changing your answer to match theirs" in prompt

    assert local_when(dt_util.now() - timedelta(days=1)).endswith("yesterday")


def test_discussions_end_on_the_answer_not_an_offer_of_more_help() -> None:
    """16 Nov: "Is there anything else you would like me to check on?" after each answer, until "stop listening"."""
    import inspect

    from custom_components.sentient import conversation

    source = inspect.getsource(conversation)
    assert "Ask a specific, useful question only when needed" in source
    assert "Never repeat a closing question or offer generic help" in source


async def test_a_repeated_question_is_looked_up_afresh(hass: HomeAssistant) -> None:
    """15 Nov: after the fix, it repeated its earlier "past three o'clock" from the recent conversations."""
    from tests.test_phase1 import _prompt, _setup as setup_one

    entry = await setup_one(hass)
    conv = entry.runtime_data.conversations
    conv.attach("c1", None)
    conv._chat("c1", "content_added", {"content": {"role": "user", "content": "what time did she get back?"}})
    conv._chat("c1", "content_added", {"content": {"role": "assistant", "content": "Just after three."}})
    conv._finish("c1")
    prompt = await _prompt(hass)
    assert "look the answer up afresh" in prompt and "look it up with a tool, not from these" in prompt


async def test_saved_memories_are_cleaned_up_and_guessed_corrections_are_marked(hass: HomeAssistant) -> None:
    """16 Nov: "macon coffee" and "a Ford Cougar" were saved as misheard; an overnight guess was listed as a lesson."""
    from custom_components.sentient.llm_api import RememberTool
    from tests.test_phase1 import _prompt, _setup as setup_one

    assert "fix an obvious mishearing" in RememberTool.description
    assert "if you can't tell what they said, ask before saving" in RememberTool.description
    assert "Say a date" in RememberTool.description
    entry = await setup_one(hass)
    entry.runtime_data.memory.add("When asked about the vacuum, call it the vacuum.", "correction", "reflection")
    entry.runtime_data.memory.add("Never offer to close the windows.", "correction", "conversation")
    prompt = await _prompt(hass)
    assert "- When asked about the vacuum, call it the vacuum. (a guess)" in prompt
    assert "- Never offer to close the windows.\n" in prompt  # someone said it: not marked
