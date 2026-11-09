"""1.3.1: speaking a reply as it's written, and saying whatever Sentient changes afterwards."""

from types import SimpleNamespace
from unittest.mock import AsyncMock, patch

from homeassistant.components.conversation import AssistantContent

from custom_components.sentient.voice_stream import StreamWatch, unspoken
from tests.test_voice_sessions_control import agent, request, response


def test_what_is_left_to_say():
    assert unspoken("Sure, I will.", "Sure, I will.") == ""
    assert unspoken("", "Shall I unlock the door?") == ""  # nothing went out early: the reply is spoken whole
    assert unspoken("Sure, I will.", "Sure, I will. Anything else?") == "Anything else?"
    assert unspoken("I'll unlock it.", "Shall I unlock the front door?") == "Shall I unlock the front door?"


class FakeLog:
    def __init__(self):
        self.content = []
        self.delta_listener = None
        self.heard = []

    async def async_add_delta_content_stream(self, agent_id, stream):
        text = ""
        async for delta in stream:
            if self.delta_listener:
                self.delta_listener(self, delta)
            text += delta.get("content") or ""
        self.content.append(AssistantContent(agent_id=agent_id, content=text))
        yield self.content[-1]


def _stream(log, *texts):
    log.delta_listener({}, {"role": "assistant"}) if False else None
    for i, text in enumerate(texts):
        delta = {"content": text}
        if i == 0:
            delta["role"] = "assistant"
        log.delta_listener(log, delta)
    log.content.append(AssistantContent(agent_id="x", content="".join(texts)))


async def test_a_changed_reply_is_added_once_speaking_has_begun():
    log = FakeLog()
    log.delta_listener = lambda chat_log, delta: log.heard.append(delta.get("content"))
    with StreamWatch(log, "conversation.sentient") as watch:
        _stream(log, "I'll unlock the front door for you, just a moment while I do ", "that now.")
        assert watch.started
        added = await watch.finish("Shall I unlock the front door?")
    assert added == "Shall I unlock the front door?"
    assert log.heard[-1].strip() == "Shall I unlock the front door?"
    assert log.delta_listener is not None  # the pipeline's own listener is back


async def test_a_short_reply_isnt_touched():
    log = FakeLog()
    log.delta_listener = lambda chat_log, delta: log.heard.append(delta.get("content"))
    with StreamWatch(log, "conversation.sentient") as watch:
        _stream(log, "Done.")
        assert not watch.started
        assert await watch.finish("Shall I do that?") == ""
    assert log.heard == ["Done."]


async def test_an_unchanged_streamed_reply_adds_nothing():
    log = FakeLog()
    log.delta_listener = lambda chat_log, delta: None
    text = "The house is settled and the heating is at twenty-one degrees, with every window shut."
    with StreamWatch(log, "conversation.sentient") as watch:
        _stream(log, text)
        assert await watch.finish(text) == ""


async def test_sessions_stop_streaming_unless_switched_on(hass):
    from unittest.mock import PropertyMock

    stub = SimpleNamespace(supports_streaming=True)

    def streams(**options):
        obj, _ = agent(hass, {"conversation_sessions": True, **options})
        with patch("custom_components.sentient.conversation.conversation.async_get_agent", return_value=stub), \
                patch.object(type(obj), "main_agent", new_callable=PropertyMock, return_value="conversation.x"):
            return obj.supports_streaming

    assert not streams()
    assert streams(stream_voice_replies=True)
    assert not streams(stream_voice_replies=True, guarded_control=True)  # confirmations must be decided before speech
    assert not streams(stream_voice_replies=True, automatic_language=True, speak_house_language=True)


async def test_nothing_changes_when_not_streaming(hass):
    obj, _ = agent(hass, {"conversation_sessions": True, "stream_voice_replies": True})
    with patch.object(obj, "_async_process_legacy", AsyncMock(return_value=response("Of course"))):
        result = await obj.async_process(request("hello"))
    assert result.response.speech["plain"]["speech"] == "Of course"


async def test_through_the_agent_a_confirmation_is_still_spoken(hass):
    from unittest.mock import PropertyMock

    from homeassistant.components.conversation.chat_log import current_chat_log

    obj, _ = agent(hass, {"conversation_sessions": True, "stream_voice_replies": True})
    log = FakeLog()
    log.delta_listener = lambda chat_log, delta: log.heard.append(delta.get("content"))

    async def impl(user_input):
        _stream(log, "I'll unlock the front door for you, just a moment while I do ", "that now.")
        return response("Shall I unlock the front door?")

    token = current_chat_log.set(log)
    try:
        with patch.object(type(obj), "supports_streaming", new_callable=PropertyMock, return_value=True), \
                patch.object(obj, "_async_process_impl", impl):
            result = await obj._process_streaming_aware(request("unlock the door"))
    finally:
        current_chat_log.reset(token)
    assert result.response.speech["plain"]["speech"] == "Shall I unlock the front door?"
    assert log.heard[-1].strip() == "Shall I unlock the front door?"
