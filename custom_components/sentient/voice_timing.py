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
    def __init__(self, hass=None, changed=None):
        self.hass = hass
        self.rows = deque(maxlen=20)
        self.generation = 0
        self.changed = changed

    def clear(self):
        self.generation += 1
        self.rows.clear()
        if self.changed is not None:
            self.changed()

    def restore(self, rows):
        """Restore bounded, content-free diagnostic records only."""
        if not isinstance(rows, list):
            return
        for row in rows[:20]:
            if not isinstance(row, dict) or not isinstance(row.get('time'), str):
                continue
            stages = row.get('stages_ms')
            total = row.get('total_ms')
            if not isinstance(stages, dict) or not isinstance(total, (int, float)):
                continue
            self.rows.append({'time': row['time'], 'outcome': str(row.get('outcome', 'completed')),
                'total_ms': total, 'stages_ms': {k: v for k, v in stages.items() if isinstance(v, (int, float))},
                'listen': bool(row.get('listen'))})

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
                if self.changed is not None:
                    self.changed()
                if self.hass is not None:
                    self.hass.bus.async_fire('sentient_voice_timing')

    def describe(self):
        return [{**row, 'stages_ms': dict(row['stages_ms'])} for row in self.rows]
