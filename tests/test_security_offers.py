"""15 Nov: "Is the alarm on?" — "The alarm is currently disarmed. Shall I arm it in away mode?" though the house rules
say never to offer. With "Never offer to arm, disarm or unlock" on, that sentence never reaches the voice: not in the
finished reply, not while it's spoken as it's written, and not in what the conversation remembers it said."""

from unittest.mock import patch

import pytest
from homeassistant.components import conversation
from homeassistant.core import Context, HomeAssistant
from homeassistant.helpers import intent

from custom_components.sentient.security_offers import (
    SECURITY_OFFER,
    StreamFilter,
    applies,
    remove,
    sentences,
)
from custom_components.sentient.voice_stream import StreamWatch, unspoken


@pytest.mark.parametrize("said", [
    "Shall I arm it in away mode?",
    "Would you like me to disarm the alarm?",
    "I can arm it for you if you like.",
    "Do you want me to unlock the back door?",
    "Want me to set the alarm?",
    "I could put the alarm on before bed.",
    "Should I turn the alarm on?",
    "Let me know and I'll arm it.",
])
def test_offers_are_recognised(said: str) -> None:
    assert SECURITY_OFFER.search(said)


@pytest.mark.parametrize("said", [
    "The alarm is currently disarmed.",
    "It's armed away.",
    "The back door is locked.",
    "Shall I turn the hallway light on?",
    "I can't arm or disarm the alarm.",
    "Would you like me to check the gate camera?",
    "The alarm was disarmed at 7:02am.",
    "Do you want me to lock the back door?",
])
def test_ordinary_sentences_are_left_alone(said: str) -> None:
    assert not SECURITY_OFFER.search(said)


def test_the_offer_is_taken_out_and_the_rest_kept() -> None:
    kept, gone = remove("The alarm is currently disarmed. Shall I arm it in away mode?")
    assert kept == "The alarm is currently disarmed." and gone == ["Shall I arm it in away mode?"]
    kept, gone = remove("It's disarmed. Would you like me to arm it? The doors are all shut.")
    assert kept == "It's disarmed. The doors are all shut." and gone == ["Would you like me to arm it?"]
    assert remove("Everything's shut and the alarm's off.") == ("Everything's shut and the alarm's off.", [])
    # only the offer: left as it is rather than saying nothing
    assert remove("Shall I arm it?") == ("Shall I arm it?", [])
    assert remove("") == ("", [])


def test_someone_asking_for_a_change_still_gets_their_confirmation() -> None:
    assert applies(True, "Is the alarm on?")
    assert applies(True, "has the alarm been set today?")
    assert applies(True, "what's happening at the gate")
    assert not applies(True, "arm the alarm")
    assert not applies(True, "can you disarm it")
    assert not applies(True, "set the alarm to away")
    assert not applies(True, "unlock the back door")
    assert not applies(True, "turn the alarm on please")
    assert not applies(True, "open the front door")  # "it's locked — shall I unlock it?" is its confirmation
    assert applies(True, "is anything open?")
    assert not applies(False, "Is the alarm on?")  # switched off: nothing changes


def test_a_streamed_reply_is_held_a_sentence_at_a_time() -> None:
    f = StreamFilter()
    out = "".join(f.feed(piece) for piece in ["The alarm is cur", "rently disarmed. Sha", "ll I arm it in away", " mode?"])
    out += f.flush()
    assert out == "The alarm is currently disarmed. "
    assert f.dropped == ["Shall I arm it in away mode?"]
    f = StreamFilter()
    out = "".join(f.feed(p) for p in ["Would you like me to arm it? ", "The doors are shut."]) + f.flush()
    assert out == "The doors are shut." and f.dropped == ["Would you like me to arm it?"]
    f = StreamFilter()
    out = "".join(f.feed(p) for p in ["It's 3.5 degrees", " outside. All quiet."]) + f.flush()
    assert out == "It's 3.5 degrees outside. All quiet."  # a decimal point isn't the end of a sentence
    f = StreamFilter()  # only the offer: said after all rather than nothing
    assert (f.feed("Shall I arm it?") + f.flush()) == "Shall I arm it?"


def test_sentences_split_on_endings_and_lines() -> None:
    assert sentences("One. Two? Three!\nFour") == ["One.", "Two?", "Three!", "Four"]


class _Log:
    """Enough of a chat log for StreamWatch: content, a delta listener, and adding deltas."""

    def __init__(self) -> None:
        self.content: list = []
        self.heard: list[str] = []
        self.delta_listener = lambda _log, delta: self.heard.append(delta.get("content") or "")

    async def async_add_delta_content_stream(self, agent_id, stream):
        from homeassistant.components.conversation.chat_log import AssistantContent

        text = ""
        async for delta in stream:
            if self.delta_listener:
                self.delta_listener(self, delta)
            text += delta.get("content") or ""
            yield delta
        self.content.append(AssistantContent(agent_id=agent_id, content=text))


async def test_streaming_never_speaks_the_offer_and_adds_nothing_after() -> None:
    from homeassistant.components.conversation.chat_log import AssistantContent

    log = _Log()
    with StreamWatch(log, "conversation.x", None, no_security_offers=True) as watch:
        log.delta_listener(log, {"role": "assistant"})
        for piece in ["The alarm is currently disarmed, and the doors and windows are all shut", " tight. ",
                      "Shall I arm it in away mode?"]:
            log.delta_listener(log, {"content": piece})
        log.content.append(AssistantContent(agent_id="conversation.x", content="The alarm is currently disarmed, "
                                            "and the doors and windows are all shut tight. Shall I arm it in away mode?"))
        watch.started = True
        added = await watch.finish("The alarm is currently disarmed, and the doors and windows are all shut tight.")
    assert "arm it" not in "".join(log.heard)
    assert added == ""  # nothing repeated: what was spoken is the filtered reply


async def test_switched_off_the_stream_is_untouched() -> None:
    log = _Log()
    with StreamWatch(log, "conversation.x", None) as _watch:
        log.delta_listener(log, {"role": "assistant"})
        log.delta_listener(log, {"content": "It's disarmed. Shall I arm it?"})
    assert "".join(log.heard) == "It's disarmed. Shall I arm it?"


def test_unspoken_ignores_spacing() -> None:
    assert unspoken("A.  B.", "A. B.") == ""
    assert unspoken("A.", "A. B.") == "B."


async def _agent_reply(hass: HomeAssistant, asked: str, said: str, **options) -> conversation.ConversationResult:
    from pytest_homeassistant_custom_component.common import MockConfigEntry

    from custom_components.sentient.const import DOMAIN

    from homeassistant.setup import async_setup_component

    assert await async_setup_component(hass, "homeassistant", {})
    entry = MockConfigEntry(domain=DOMAIN, title="Nova", unique_id=DOMAIN, data={
        "persona": "Nova", "speakers": ["assist_satellite.kitchen"], "default_speaker": "assist_satellite.kitchen",
        "voice_agent": "conversation.inner", **options})
    entry.add_to_hass(hass)
    assert await hass.config_entries.async_setup(entry.entry_id)
    await hass.async_block_till_done()
    agent = next(e for e in hass.data["entity_components"]["conversation"].entities
                 if getattr(e, "_entry", None) is entry and type(e).__name__ == "BackedUpAgent")

    async def impl(user_input):
        response = intent.IntentResponse(language="en")
        response.async_set_speech(said)
        return conversation.ConversationResult(response=response, conversation_id="c1")

    with patch.object(agent, "_async_process_impl", impl):
        return await agent._process_streaming_aware(conversation.ConversationInput(
            text=asked, context=Context(), conversation_id=None, device_id=None, satellite_id=None, language="en",
            agent_id=agent.entity_id))


async def test_the_agent_takes_the_offer_out_when_switched_on(hass: HomeAssistant) -> None:
    result = await _agent_reply(hass, "Is the alarm on?", "The alarm is currently disarmed. Shall I arm it in away mode?",
                                no_security_offers=True)
    assert result.response.speech["plain"]["speech"] == "The alarm is currently disarmed."


async def test_the_agent_leaves_it_when_switched_off(hass: HomeAssistant) -> None:
    result = await _agent_reply(hass, "Is the alarm on?", "The alarm is currently disarmed. Shall I arm it in away mode?")
    assert result.response.speech["plain"]["speech"] == "The alarm is currently disarmed. Shall I arm it in away mode?"


async def test_the_agent_still_confirms_when_asked_to_arm(hass: HomeAssistant) -> None:
    result = await _agent_reply(hass, "arm the alarm", "Shall I arm it in away mode or at home mode?",
                                no_security_offers=True)
    assert result.response.speech["plain"]["speech"] == "Shall I arm it in away mode or at home mode?"


async def test_the_setting_is_on_the_settings_page(hass: HomeAssistant, hass_ws_client) -> None:
    from tests.test_phase3 import _setup

    await _setup(hass)
    ws = await hass_ws_client(hass)
    await ws.send_json({"id": 1, "type": "sentient/settings"})
    result = (await ws.receive_json())["result"]
    names = {f["name"] for g in result["groups"] for f in g["schema"]}
    assert "no_security_offers" in names and result["data"]["no_security_offers"] is False
