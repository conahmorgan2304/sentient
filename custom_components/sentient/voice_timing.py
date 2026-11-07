"""Optional content-free timings for a single agent turn; stages may overlap."""
from collections import deque
from contextlib import contextmanager
from contextvars import ContextVar
from time import monotonic

from homeassistant.util import dt as dt_util

_current = ContextVar('sentient_voice_timing', default=None)


@contextmanager
def measure(stage):
    trace = _current.get()
    if trace is None:
        yield
        return
    start = monotonic()
    try:
        yield
    finally:
        if not trace.get('finished'):
            trace['stages'][stage] = trace['stages'].get(stage, 0) + monotonic() - start


class VoiceTimings:
    def __init__(self, hass=None):
        self.hass = hass
        self.rows = deque(maxlen=20)
        self.generation = 0

    def clear(self):
        self.generation += 1
        self.rows.clear()

    @contextmanager
    def turn(self, allowed=None):
        trace = {'stages': {}, 'finished': False}
        generation = self.generation
        token = _current.set(trace)
        start = monotonic()
        outcome = 'completed'
        try:
            yield trace
        except BaseException as error:
            outcome = 'cancelled' if type(error).__name__ == 'CancelledError' else 'failed'
            raise
        finally:
            trace['finished'] = True
            _current.reset(token)
            if generation == self.generation and (allowed is None or allowed()):
                self.rows.appendleft({'time': dt_util.utcnow().isoformat(), 'outcome': trace.get('outcome', outcome),
                    'total_ms': round((monotonic() - start) * 1000),
                    'stages_ms': {k: round(v * 1000) for k, v in trace['stages'].items()},
                    'listen': trace.get('listen', False)})
                if self.hass is not None:
                    self.hass.bus.async_fire('sentient_voice_timing')

    def describe(self):
        return [{**row, 'stages_ms': dict(row['stages_ms'])} for row in self.rows]
