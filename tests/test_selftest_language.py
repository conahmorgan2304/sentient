"""The self-test asks the voice to answer in English, so an answer can be judged whatever language the house uses."""
from types import SimpleNamespace
from unittest.mock import patch

from homeassistant.core import HomeAssistant
from pytest_homeassistant_custom_component.common import MockConfigEntry

from custom_components.sentient.const import DOMAIN
from custom_components.sentient.conversations import drop_selftest_bursts
from custom_components.sentient.selftest import ENGLISH_ASK

OPTIONS = {"persona": "Nova", "speakers": ["assist_satellite.kitchen"], "default_speaker": "assist_satellite.kitchen",
           "quiet_start": "00:00:00", "quiet_end": "00:00:00", "voice_agent": "conversation.ai", "self_test": "nightly"}


async def _setup(hass: HomeAssistant, **extra) -> MockConfigEntry:
    hass.states.async_set("assist_satellite.kitchen", "idle", {"supported_features": 3})
    entry = MockConfigEntry(domain=DOMAIN, title="Nova", unique_id=DOMAIN, data={**OPTIONS, **extra})
    entry.add_to_hass(hass)
    assert await hass.config_entries.async_setup(entry.entry_id)
    await hass.async_block_till_done()
    return entry


async def test_every_question_asks_for_an_english_answer(hass: HomeAssistant):
    """Issue #235: the question was English, the agent answered German, and English word lists called it wrong."""
    entry = await _setup(hass)
    rt = entry.runtime_data
    seen = []

    async def converse(hass_, text, cid, context, **kw):
        seen.append((text, rt.conversations.testing))
        return SimpleNamespace(response=SimpleNamespace(speech={"plain": {"speech": "Yes."}}), conversation_id=cid)

    with patch("homeassistant.components.conversation.async_converse", converse), \
            patch.object(type(rt.selftest), "_exposed", lambda self, entity_id: True):
        await rt.selftest.async_run()
    assert seen
    for text, testing in seen:
        assert text.endswith(ENGLISH_ASK)
        assert testing == text  # the house's own question is recognised by the exact text it sent
    questions = {q.ask for q in rt.selftest.questions()}
    assert any(text.startswith(question) for text, _ in seen for question in questions)


def test_a_question_with_the_instruction_is_still_cleared_from_history():
    """Old self-test questions must not stay behind as things people asked (asked-often, reliability)."""

    def episode(start: str, text: str) -> dict:
        return {"id": text[:4] + start[-6:], "start": start,
                "turns": [{"who": "them", "text": text}, {"who": "me", "text": "ok"}]}

    burst = [episode(f"2026-11-26T03:25:0{i}+00:00", f"{question} {ENGLISH_ASK}")
             for i, question in enumerate(["Is the alarm on?", "What time is it?", "Is anything open?",
                                           "What reminders have I got?"])]
    real = [episode("2026-11-26T08:00:00+00:00", "Is the alarm on?")]
    assert drop_selftest_bursts(burst + real) == real


async def test_overlapping_self_test_is_skipped_and_errors_cannot_pass(hass: HomeAssistant):
    from custom_components.sentient.selftest import Question

    entry = await _setup(hass)
    rt = entry.runtime_data
    nested = []

    async def converse(hass_, text, cid, context, **kw):
        nested.append(await rt.selftest.async_run(force=True))
        raise RuntimeError("no reminders")

    question = Question("test", "What reminders have I got?", lambda answer: (True, "no reminders"))
    with patch.object(type(rt.selftest), "questions", lambda self: [question]), \
            patch("homeassistant.components.conversation.async_converse", converse):
        result = await rt.selftest.async_run(force=True)
    assert nested == [{"skipped": "a self-test is already running"}]
    assert not result["results"][0]["right"]
    assert "conversation failed" in result["results"][0]["expected"]
    assert rt.conversations.testing is None
    assert not rt.selftest._running


def test_cleanup_preserves_longer_real_questions():
    questions = ["Is the alarm on? I need to know before leaving.",
                 "What time is it? I have a meeting.",
                 "Is anything open? Please check the garage."]
    episodes = [{"start": f"2026-11-26T03:25:0{i}+00:00",
                 "turns": [{"who": "them", "text": question}]} for i, question in enumerate(questions)]
    assert drop_selftest_bursts(episodes) == episodes
