"""Streaming voice replies: say anything Sentient changes after the model has already started being spoken.

Home Assistant starts speaking a long reply while the model is still writing it. Sentient sometimes replaces or adds to
the model's words afterwards (a confirmation question, the outcome of an action, a correction notice). Words already
spoken can't be taken back, so the difference is added to the chat log, and the pipeline speaks it straight after.
"""

from __future__ import annotations

from typing import Any

try:  # the length at which Home Assistant starts speaking a reply as it arrives
    from homeassistant.components.assist_pipeline.pipeline import STREAM_RESPONSE_CHARS
except Exception:  # noqa: BLE001 - older or newer Home Assistant: assume the usual figure
    STREAM_RESPONSE_CHARS = 60


def unspoken(streamed: str, final: str) -> str:
    """What still has to be said once the model's words have gone out: nothing, the tail, or the whole replacement."""
    streamed, final = " ".join(streamed.split()), " ".join(final.split())
    if not streamed or not final or final == streamed:
        return ""
    if final.startswith(streamed):
        return final[len(streamed):].strip()
    return final


class StreamWatch:
    """Watches the chat log's deltas the way the pipeline does, to know whether speaking has begun early."""

    def __init__(self, log: Any, agent_id: str, pronouncer: Any = None, no_security_offers: bool = False,
                 impossible: Any = None, blocked=None) -> None:
        self.log = log
        self.agent_id = agent_id
        self._say = None
        self._offers = None
        if no_security_offers or impossible is not None or blocked is not None:
            from .security_offers import StreamFilter

            # a sentence offering to arm, disarm or unlock (or to do what no device here can) never reaches the voice
            self._offers = StreamFilter(security=no_security_offers, extra=impossible, blocked=blocked)
        if pronouncer is not None and (pronouncer.active() if hasattr(pronouncer, "active") else pronouncer.table()):
            from .pronounce import StreamPronouncer

            self._say = StreamPronouncer(pronouncer, pronouncer.table, getattr(pronouncer, "numbers", lambda: False))  # names as they're said, on their way to the voice
        self._spoken: list[str] = []
        self.chars = 0
        self.started = False
        self._role: str | None = None
        self._orig = None
        self._own = False  # Sentient's own words being added at the end: already checked, passed straight on

    def __enter__(self) -> "StreamWatch":
        self._orig = self.log.delta_listener
        self.log.delta_listener = self._spy
        return self

    def __exit__(self, *exc: object) -> None:
        self._flush()
        if self.log.delta_listener == self._spy:
            self.log.delta_listener = self._orig

    def _flush(self) -> None:
        if self._orig is None:
            return
        held = self._offers.flush() if self._offers is not None else ""
        if held:
            self._spoken.append(held)
        if self._say is not None:
            held = (self._say.feed(held) if held else "") + self._say.flush()
        if held:
            self._forward(self.log, {"content": held})

    def _spy(self, chat_log: Any, delta: dict) -> None:
        if role := delta.get("role"):
            self._flush()  # a held word belongs to the message that just ended
            self._role = role
        if self._offers is not None and not self._own and self._role == "assistant" and isinstance(delta.get("content"), str):
            delta = {**delta, "content": self._offers.feed(delta["content"])}
        if self._role == "assistant" and isinstance(content := delta.get("content"), str):
            self._spoken.append(content)
            if self._say is not None:
                delta = {**delta, "content": self._say.feed(content)}
        self._forward(chat_log, delta)

    def _forward(self, chat_log, delta):
        if self._role == "assistant" and not self.started:
            if self.chars and delta.get("tool_calls"):
                self.started = True
            elif content := delta.get("content"):
                self.chars += len(content)
                self.started = self.chars > STREAM_RESPONSE_CHARS
        if self._orig is not None:
            self._orig(chat_log, delta)

    def streamed_text(self) -> str:
        """The original wording forwarded to the pipeline, before pronunciation changes."""
        return "".join(self._spoken)

    async def finish(self, final: str) -> str:
        """Say what the model didn't: it goes through the chat log's own delta stream, which is what a streaming
        pipeline listens to (adding finished content to the log directly would not be spoken). Returns what was added."""
        self._flush()
        if not self.started or not (extra := unspoken(self.streamed_text(), final)):
            return ""

        async def delta():
            yield {"role": "assistant"}
            yield {"content": " " + extra}

        self._own = True
        try:
            async for _ in self.log.async_add_delta_content_stream(self.agent_id, delta()):
                pass
        finally:
            self._own = False
        return extra
