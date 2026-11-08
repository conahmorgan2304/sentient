"""Optional bounded reuse of compatible, event-bound clip analyses. No polling."""
from __future__ import annotations

import asyncio
from copy import deepcopy
from datetime import timedelta

from homeassistant.util import dt as dt_util

CONF_SHARED_CLIPS = "share_clip_analysis"
KINDS = {"delivery", "visitor", "household", "passer-by", "animal", "vehicle", "suspicious", "nothing"}


class ClipAnalysis:
    def __init__(self, runtime):
        self.runtime = runtime
        self._generation = 0
        self._cache = {}
        self._pending = {}
        self.hits = self.joined = 0

    @property
    def enabled(self):
        rt = self.runtime()
        return bool(rt and rt.options.get(CONF_SHARED_CLIPS))

    def stamp(self):
        rt = self.runtime()
        speech = getattr(rt, "speech", None)
        return (self._generation, id(getattr(rt, "options", None)),
                getattr(speech, "_privacy_generation", 0), getattr(speech, "_guest_generation", 0),
                getattr(speech, "guest_mode", False), getattr(getattr(rt, "ai", None), "_generation", 0))

    def clear(self):
        self._generation += 1
        self._cache.clear()
        for task in self._pending.values():
            task.cancel()
        self._pending.clear()

    async def analyse(self, camera, event, start, end, provider, profile, producer):
        """Only identical event coverage/provider/privacy profiles may share an answer."""
        if not self.enabled:
            return await producer()
        stamp = self.stamp()
        key = (camera.removeprefix("camera."), event, start, end, provider or "", profile, stamp)
        now = dt_util.utcnow()
        self._cache = {k: v for k, v in self._cache.items() if now - v[0] < timedelta(hours=6)}
        if key in self._cache:
            self.hits += 1
            return deepcopy(self._cache[key][1])
        task = self._pending.get(key)
        if task is not None:
            self.joined += 1
        else:
            # Bound work as well as retained answers; do not evict a running producer.
            if len(self._pending) >= 8:
                return None

            async def run():
                result = await producer()
                if not self.enabled or self.stamp() != stamp:
                    return None
                if not isinstance(result, dict) or not isinstance(result.get("summary"), str) or not result["summary"].strip():
                    return None
                answer = {"summary": result["summary"].strip()[:1200],
                          "kind": result.get("kind") if isinstance(result.get("kind"), str) and result["kind"] in KINDS else "visitor",
                          "worth": result.get("worth") is True}
                if profile != "neutral" and isinstance(result.get("whose"), str):
                    answer["whose"] = result["whose"][:100]
                self._cache[key] = (dt_util.utcnow(), answer)
                while len(self._cache) > 100:
                    self._cache.pop(next(iter(self._cache)))
                return deepcopy(answer)

            task = asyncio.create_task(run(), name="sentient shared clip analysis")
            self._pending[key] = task

            def done(completed):
                if self._pending.get(key) is completed:
                    self._pending.pop(key, None)
                if not completed.cancelled():
                    completed.exception()  # consume errors even if all waiters left

            task.add_done_callback(done)
        try:
            answer = await asyncio.shield(task)
        except asyncio.CancelledError:
            if task.cancelled() and self.stamp() != stamp:
                return None
            raise
        return deepcopy(answer) if self.enabled and self.stamp() == stamp else None

    def describe(self):
        return {"enabled": self.enabled, "retained": len(self._cache) if self.enabled else 0,
                "running": len(self._pending), "reused": self.hits, "joined": self.joined}
