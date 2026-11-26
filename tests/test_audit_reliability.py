"""Regression checks for the intelligence reliability audit."""
from custom_components.sentient.reliability import actionable_offer, explicit_completion
from custom_components.sentient.voice_timing import VoiceTimings
from custom_components.sentient.conversation_quality import ConversationQuality


def test_action_offers_are_distinct_from_information():
    assert actionable_offer("Would you like me to start the kettle?")
    assert actionable_offer("Shall I turn the light off?")
    assert not actionable_offer("Shall I check when it arrived?")
    assert not actionable_offer("Is there anything else?")


def test_completion_claim_is_not_a_state_answer():
    assert explicit_completion("I've set that up.")
    assert explicit_completion("Done.")
    assert not explicit_completion("The light is now on.")
    assert not explicit_completion("I cannot confirm it changed.")


def test_timing_round_trip_is_bounded_and_content_free():
    timing = VoiceTimings()
    for _ in range(25):
        with timing.turn() as trace:
            trace["listen"] = True
    restored = VoiceTimings()
    restored.restore(timing.describe())
    assert restored.describe() == timing.describe()
    assert len(restored.describe()) == 20
    assert all(set(row) == {"time", "outcome", "total_ms", "stages_ms", "listen"}
               for row in restored.describe())
    restored.clear()
    assert not restored.describe()


def test_invalid_timing_storage_is_ignored():
    timing = VoiceTimings()
    timing.restore([None, {}, {"time": "today", "total_ms": "bad", "stages_ms": {}}])
    assert not timing.describe()


def test_carry_kind_and_voice_block_are_recorded():
    q = ConversationQuality()
    q.record(text="What about that?", reply="All clear.", carried=True,
             asked_back=False, error=False, carry_kind="same_id")
    q.record(text="What about that?", reply="Please clarify.", carried=False,
             asked_back=True, error=False, voice_id_block="cautious")
    assert q.turns[0]["carry_kind"] == "same_id"
    assert q.turns[1]["voice_id_block"] == "cautious"
