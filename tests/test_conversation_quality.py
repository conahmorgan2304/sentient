"""Phase 2: how conversations go is counted (never the words), so a change can be judged on it."""
from homeassistant.core import HomeAssistant

from custom_components.sentient.conversation_quality import ConversationQuality, classify


def test_follow_ups_corrections_and_clarifications_are_counted() -> None:
    q = ConversationQuality()
    q.record(text="is everything ok outside?", reply="All quiet.", carried=False, asked_back=False, error=False, ms=900)
    q.record(text="what about the shed?", reply="Nothing near the shed.", carried=True, asked_back=False, error=False, ms=800)
    q.record(text="no, I meant the garden", reply="The garden's quiet too.", carried=True, asked_back=False, error=False)
    q.record(text="make it warmer", reply="Which room?", carried=True, asked_back=True, error=False)
    s = q.summary()
    assert s["turns"] == 4 and s["carried_on"] == 3 and s["follow_ups"] == 3
    assert s["follow_up_kinds"] == {"comparison": 1, "correction": 1, "adjustment": 1}
    assert s["answers_corrected"] == 1 and s["follow_ups_needing_clarification"] == 1
    assert s["clarification_rate"] is None  # too few to give a rate
    assert all("text" not in t and "reply" not in t for t in q.turns)  # no words kept
    assert classify("turn on the kitchen light") is None


async def test_a_carried_on_turn_is_counted_from_the_real_conversation_path(hass: HomeAssistant) -> None:
    from unittest.mock import AsyncMock, patch

    from tests.test_voice_sessions_control import agent, request, response

    obj, rt = agent(hass, {"voice_timing_diagnostics": True, "carry_on_minutes": 5, "persona": "Nova"})
    rt.speech = None
    answers = [response("All quiet outside."), response("Nothing near the shed.")]
    for a in answers:
        a.conversation_id = "talk-1"
    with patch.object(obj, "_async_process_legacy", AsyncMock(side_effect=answers)):
        await obj.async_process(request("is everything ok outside", cid=None))
        await obj.async_process(request("what about the shed", cid=None))
    s = rt.conversation_quality.summary()
    assert s["turns"] == 2 and s["carried_on"] == 1 and s["follow_up_kinds"] == {"comparison": 1}


def test_memory_recall_is_measured_without_keeping_the_memories() -> None:
    q = ConversationQuality()
    q.record(text="when do the bins go out?", reply="The bins go out on Thursday morning.", carried=False,
             asked_back=False, error=False, memories=["The bins go out on Thursday", "Sam takes sugar in tea"])
    q.record(text="who's coming tonight?", reply="I don't know.", carried=False, asked_back=False, error=False,
             memories=["Sam takes sugar in tea"], recalled=True)
    q.record(text="no, that's wrong", reply="Sorry, noted.", carried=True, asked_back=False, error=False, corrected=True)
    m = q.summary()["memory"]
    assert m == {"turns_with_memories": 2, "turns_using_one": 1, "turns_with_one_on_topic": 1, "had_to_search": 1,
                 "corrected": 1, "use_rate": None}
    assert all(isinstance(t["memories_offered"], int) for t in q.turns)


def test_every_memory_says_how_it_is_known() -> None:
    from custom_components.sentient.memory import Memory

    assert Memory("x", source="reflection").basis == "guess"
    assert Memory("x", source="reflection", pinned=True).basis == "confirmed"
    assert Memory("x", source="settings").basis == "told"
    assert Memory("x", source="conversation").basis == "said"
    assert Memory("x", source="reflection", origin="edited").basis == "confirmed"  # put right on the Memories page
    assert Memory("x").as_dict()["basis"] == "said"


async def test_a_correction_sticks_when_the_thing_isnt_said_again(hass: HomeAssistant, freezer) -> None:
    """Phase 2, learning: a thumbs down or shush starts a fortnight's watch; anything said about it counts against it."""
    from datetime import timedelta

    from custom_components.sentient.feedback import Feedback
    from custom_components.sentient.timeline import EVENT_TIMELINE

    fb = Feedback(hass, "t")
    await fb.async_load()
    fb.record("source:appliance", 1.0, "thumbs down")
    fb.record("sensor.kettle", 1.0, "thumbs down")
    hass.bus.async_fire(EVENT_TIMELINE, {"kind": "said", "source": "appliance", "text": "The washer's done."})
    await hass.async_block_till_done()
    freezer.tick(timedelta(days=15))
    s = fb.stuck()
    assert s == {**s, "corrections": 2, "judged": 2, "stuck": 1, "said_again": 1}
    fb.record("sensor.kettle", -1.0, "thumbs up")  # liked after all: not a correction any more
    assert fb.stuck()["corrections"] == 1
    fb.async_stop()
