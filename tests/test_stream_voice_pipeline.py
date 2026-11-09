"""A virtual run of a voice turn: Home Assistant's real chat log and the pipeline's own streaming rule around Sentient's agent."""

import asyncio
from dataclasses import replace
from time import monotonic
from unittest.mock import AsyncMock, PropertyMock, patch

from homeassistant.components import conversation
STREAM_RESPONSE_CHARS = 60  # assist_pipeline/pipeline.py
from homeassistant.components.conversation import chat_log as chat_log_module
from homeassistant.helpers import chat_session, intent

from tests.test_voice_sessions_control import agent, request

LONG = ("The house is settled. The heating is at twenty-one degrees, the bedroom window is still open, "
        "and the kettle finished a little while ago.")


class Pipeline:
    """What Home Assistant's assist pipeline does with the chat log's deltas (assist_pipeline/pipeline.py)."""

    def __init__(self):
        self.queue = asyncio.Queue()
        self.streamed = False
        self.chars = 0
        self.role = None
        self.first_audio = None
        self.began = monotonic()

    def listener(self, log, delta):
        if role := delta.get("role"):
            self.role = role
        if self.role != "assistant":
            return
        if content := delta.get("content"):
            self.queue.put_nowait(content)
        if self.streamed:
            if content and self.first_audio is None:
                self.first_audio = monotonic() - self.began
            return
        started = self.chars > 0 and delta.get("tool_calls")
        if not started and content:
            self.chars += len(content)
            started = self.chars > STREAM_RESPONSE_CHARS
        if started:
            self.streamed = True
            self.first_audio = monotonic() - self.began

    def spoken(self, final_text):
        if not self.streamed:
            return final_text  # the pipeline speaks the final result when nothing was streamed
        parts = []
        while not self.queue.empty():
            if (part := self.queue.get_nowait()) is not None:
                parts.append(part)
        return "".join(parts)


async def turn(hass, options, chunks, final=None, same_id=True, pause=0.05):
    """One turn: the pipeline opens the chat log, Sentient's agent runs, a model streams `chunks` into the same log."""
    obj, _ = agent(hass, {"conversation_sessions": True, "conversation_style": "natural", **options})
    pipe = Pipeline()
    user = request("how's the house")

    async def model(req):
        with chat_session.async_get_chat_session(hass, req.conversation_id) as session, \
                chat_log_module.async_get_chat_log(hass, session, req) as log:
            async def stream():
                yield {"role": "assistant"}
                for text in chunks:
                    await asyncio.sleep(pause)
                    yield {"content": text}
            async for _ in log.async_add_delta_content_stream("conversation.model", stream()):
                pass
            result = conversation.async_get_result_from_chat_log(req, log)
        if final is not None:
            result.response.async_set_speech(final)
        return result

    async def legacy(req):
        return await model(replace(req, conversation_id=req.conversation_id if same_id else "another-conversation"))

    started = monotonic()
    with chat_session.async_get_chat_session(hass, None) as session, \
            chat_log_module.async_get_chat_log(hass, session, user, chat_log_delta_listener=pipe.listener):
        user = replace(user, conversation_id=session.conversation_id)
        with patch.object(type(obj), "supports_streaming", new_callable=PropertyMock, return_value=True), \
                patch.object(obj, "_async_process_legacy", legacy):
            result = await obj.async_process(user)
        final_text = result.response.speech["plain"]["speech"]
        heard = pipe.spoken(final_text)
    return pipe, heard, final_text, monotonic() - started


async def test_a_long_answer_starts_before_the_model_finishes(hass):
    chunks = [LONG[i:i + 20] for i in range(0, len(LONG), 20)]
    pipe, heard, final, total = await turn(hass, {"stream_voice_replies": True}, chunks)
    assert pipe.streamed and heard == final == LONG
    assert pipe.first_audio < total * 0.7  # speaking began well before the model was done


async def test_a_short_answer_is_spoken_whole(hass):
    pipe, heard, final, _ = await turn(hass, {"stream_voice_replies": True}, ["Done."])
    assert not pipe.streamed and heard == final == "Done."


async def test_a_changed_reply_is_still_heard_in_full(hass):
    chunks = [LONG[i:i + 20] for i in range(0, len(LONG), 20)]
    pipe, heard, final, _ = await turn(hass, {"stream_voice_replies": True}, chunks, final="Shall I close the bedroom window?")
    assert pipe.streamed and final == "Shall I close the bedroom window?"
    assert heard.startswith(LONG) and heard.endswith("Shall I close the bedroom window?")


async def test_a_carried_on_turn_on_another_log_just_isnt_streamed(hass):
    chunks = [LONG[i:i + 20] for i in range(0, len(LONG), 20)]
    pipe, heard, final, _ = await turn(hass, {"stream_voice_replies": True}, chunks, same_id=False)
    assert not pipe.streamed and heard == final == LONG  # no double speech: the whole reply is spoken at the end


async def test_the_brief_style_follow_up_is_spoken_after_the_streamed_reply(hass):
    chunks = [LONG[i:i + 20] for i in range(0, len(LONG), 20)]
    pipe, heard, final, _ = await turn(hass, {"stream_voice_replies": True, "conversation_style": "brief"}, chunks)
    assert pipe.streamed and final.startswith(LONG) and final != LONG
    assert heard == final  # the model's words, then the added question, once each


async def test_how_much_sooner_it_starts(hass):
    chunks = [LONG[i:i + 20] for i in range(0, len(LONG), 20)]
    pipe, heard, final, total = await turn(hass, {"stream_voice_replies": True}, chunks)
    assert heard == final
    assert pipe.first_audio < total  # speech began before the reply was complete
