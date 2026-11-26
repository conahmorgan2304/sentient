"""How conversations go, measured: are follow-ups carried on, understood first time, and answered quickly?

Every voice or chat turn is counted with what can be seen without AI: whether it carried on the last conversation
(a new wake word within "Carry on a conversation for"), what kind of follow-up it was ("what about the shed?",
"why?", "make it warmer"), whether the reply had to ask which thing was meant, whether the person then corrected
it ("no, I meant…"), whether it failed, and how long it took. No words are kept — only those labels and counts —
and nothing is kept in guest mode. Shown on the Mind tab with the voice timings (setting "Show voice timings").
The counts are saved, so a restart doesn't wipe them (16 Nov: several restarts a day meant it never built up);
switching "Show voice timings" off clears them.

These are the Phase 2 measures: a change to how conversations work is judged on them.
"""
from __future__ import annotations

from collections import Counter, deque
from contextvars import ContextVar
from statistics import median
from typing import Any

from homeassistant.core import HomeAssistant, callback
from homeassistant.helpers.storage import Store
from homeassistant.util import dt as dt_util

from .const import DOMAIN

TURN: ContextVar[dict | None] = ContextVar("sentient_turn_quality", default=None)  # set for the turn being answered
KEEP = 300  # turns
MIN_TURNS = 10  # before rates are shown
SAVE_DELAY = 60


def classify(text: str) -> str | None:
    """The kind of follow-up a turn is, from its words alone (None: a fresh request)."""
    from .followups import classify as followup
    from .sessions import CORRECTION, CUE_CORRECTION

    words = (text or "").strip()
    if not words:
        return None
    if CORRECTION.search(words) or CUE_CORRECTION.search(words):
        return "correction"
    return followup(words)


_WORD = __import__("re").compile(r"[a-z0-9']{4,}")
_COMMON = {"that", "this", "with", "have", "there", "their", "they", "them", "what", "when", "will", "your", "from",
           "about", "into", "only", "just", "been", "were", "which", "would", "could", "should", "every", "after"}


REPEAT_HOURS = 24
_SENTENCE = __import__("re").compile(r"[^.!?]+")


def signatures(reply: str) -> list[str]:
    """Short hashes of a reply's opening and of each of its longer sentences — so a reply said word for word
    before can be counted without keeping any words (19 Nov: "it repeats the same thing")."""
    import hashlib

    def sig(words: list[str]) -> str:
        return hashlib.sha1(" ".join(words).encode()).hexdigest()[:10]

    out: list[str] = []
    words = __import__("re").findall(r"[a-z0-9']+", (reply or "").casefold())
    if len(words) >= 5:
        out.append("o" + sig(words[:5]))  # the same opening
    for sentence in _SENTENCE.findall((reply or "").casefold()):
        w = __import__("re").findall(r"[a-z0-9']+", sentence)
        if len(w) >= 6:
            out.append("s" + sig(w))
    return out[:8]


def _shows_in(memory: str, text: str, need: int = 2) -> bool:
    """Whether a memory's own words show up in a piece of text (at least *need* of its words)."""
    words = {w for w in _WORD.findall((memory or "").casefold()) if w not in _COMMON}
    return bool(words) and len(words & set(_WORD.findall((text or "").casefold()))) >= min(need, len(words))


class ConversationQuality:
    def __init__(self, hass: HomeAssistant | None = None, entry_id: str | None = None) -> None:
        self.turns: deque[dict[str, Any]] = deque(maxlen=KEEP)
        self._store: Store[dict[str, Any]] | None = (
            Store(hass, 1, f"{DOMAIN}.{entry_id}.conversation_quality") if hass is not None and entry_id else None)

    async def async_load(self, enabled: bool = True) -> None:
        if self._store is None:
            return
        data = await self._store.async_load() or {}
        if not enabled:
            if data:
                await self._store.async_remove()  # switched off: nothing kept
            return
        self.turns.extend(t for t in data.get("turns", []) if isinstance(t, dict) and "time" in t)

    @callback
    def async_stop(self) -> None:
        if self._store is not None:
            self._store.async_delay_save(self._data, 0)

    def _data(self) -> dict[str, Any]:
        return {"turns": list(self.turns)}

    def clear(self) -> None:
        self.turns.clear()
        if self._store is not None:
            self._store.async_delay_save(self._data, 1)

    def record(self, *, text: str, reply: str, carried: bool, asked_back: bool, error: bool,
               ms: int | None = None, memories: list[str] | None = None, recalled: bool = False,
               corrected: bool = False, prompt_chars: int | None = None, instant: bool = False,
               carry_kind: str | None = None, voice_id_block: str | None = None) -> dict[str, Any]:
        kind = classify(text)
        offered = list(memories or [])
        turn = {"time": dt_util.utcnow().isoformat(), "carried": bool(carried), "kind": kind,
                "carry_kind": carry_kind, "voice_id_block": voice_id_block,
                "asked_back": bool(asked_back), "error": bool(error), "ms": ms,
                # memory recall: how many memories the voice was given, how many show in the reply, whether it had
                # to search for more, and whether it was corrected (counts only — no memory text is kept)
                "memories_offered": len(offered), "memories_used": sum(_shows_in(m, reply) for m in offered),
                "relevant_offered": sum(_shows_in(m, text, 1) for m in offered),
                "recalled": bool(recalled), "memory_corrected": bool(corrected),
                # 22 Nov: is the voice getting slower as it's given more to read, and how much do instant answers save
                "prompt_chars": prompt_chars if isinstance(prompt_chars, int) else None, "instant": bool(instant)}
        sigs = signatures(reply)
        if sigs and not error:
            since = (dt_util.utcnow() - __import__("datetime").timedelta(hours=REPEAT_HOURS)).isoformat()
            before = {x for t in self.turns if t.get("time", "") >= since for x in t.get("sigs", [])}
            turn["sigs"] = sigs
            turn["repeated_opening"] = any(x[0] == "o" and x in before for x in sigs)
            turn["repeated_sentence"] = any(x[0] == "s" and x in before for x in sigs)
        # a correction straight after an answer means that answer was wrong: count it against the turn before
        if kind == "correction" and self.turns:
            self.turns[-1]["corrected"] = True
        self.turns.append(turn)
        if self._store is not None:
            self._store.async_delay_save(self._data, SAVE_DELAY)
        return turn

    def summary(self) -> dict[str, Any]:
        turns = list(self.turns)
        n = len(turns)
        follow = [t for t in turns if t["carried"] or t["kind"]]
        kinds = Counter(t["kind"] for t in turns if t["kind"])
        times = [t["ms"] for t in turns if isinstance(t.get("ms"), (int, float))]
        rate = lambda part, whole: round(part / whole, 3) if whole >= MIN_TURNS else None  # noqa: E731
        return {
            "turns": n,
            "carried_on": sum(t["carried"] for t in turns),
            "kept_listening": sum(t.get("carry_kind") == "same_id" for t in turns),
            "picked_up_after_wake": sum(t.get("carry_kind") == "rewake" for t in turns),
            "voice_id_blocks": dict(Counter(t["voice_id_block"] for t in turns if t.get("voice_id_block"))),
            "follow_ups": len(follow),
            "follow_up_kinds": dict(kinds),
            "follow_ups_needing_clarification": sum(t["asked_back"] for t in follow),
            "clarification_rate": rate(sum(t["asked_back"] for t in follow), len(follow)),
            "answers_corrected": sum(bool(t.get("corrected")) for t in turns),
            "correction_rate": rate(sum(bool(t.get("corrected")) for t in turns), n),
            "errors": sum(t["error"] for t in turns),
            "memory": {
                "turns_with_memories": sum(t.get("memories_offered", 0) > 0 for t in turns),
                "turns_using_one": sum(t.get("memories_used", 0) > 0 for t in turns),
                "turns_with_one_on_topic": sum(t.get("relevant_offered", 0) > 0 for t in turns),
                "had_to_search": sum(bool(t.get("recalled")) for t in turns),
                "corrected": sum(bool(t.get("memory_corrected")) for t in turns),
                "use_rate": rate(sum(t.get("memories_used", 0) > 0 for t in turns),
                                 sum(t.get("relevant_offered", 0) > 0 for t in turns)),
            },
            "repeats": {
                "same_opening": sum(bool(t.get("repeated_opening")) for t in turns),
                "same_sentence": sum(bool(t.get("repeated_sentence")) for t in turns),
                "rate": rate(sum(bool(t.get("repeated_opening") or t.get("repeated_sentence")) for t in turns),
                             sum("sigs" in t for t in turns)),
                "note": f"A reply that starts with the same five words, or repeats a whole sentence, said in the {REPEAT_HOURS} hours before.",
            },
            "median_ms": round(median(times)) if len(times) >= MIN_TURNS else None,
            "speed": _speed(turns),
            "note": "Counted from what's said, not what was meant: a correction is someone saying \"no, I meant…\" straight after.",
        }


def _speed(turns: list[dict[str, Any]]) -> dict[str, Any]:
    """Instant answers against AI answers, and how much the voice is given to read."""
    def mid(values: list) -> int | None:
        values = [v for v in values if isinstance(v, (int, float))]
        return round(median(values)) if len(values) >= MIN_TURNS else None

    instant = [t for t in turns if t.get("instant")]
    by_ai = [t for t in turns if not t.get("instant")]
    return {"instant_answers": len(instant), "instant_ms": mid([t.get("ms") for t in instant]),
            "ai_ms": mid([t.get("ms") for t in by_ai]), "prompt_chars": mid([t.get("prompt_chars") for t in by_ai])}


def of(rt: Any) -> ConversationQuality:
    """The house's conversation measures (made on first use)."""
    found = getattr(rt, "conversation_quality", None)
    if found is None:
        found = ConversationQuality()
        rt.conversation_quality = found
    return found
