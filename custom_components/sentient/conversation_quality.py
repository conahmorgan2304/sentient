"""How conversations go, measured: are follow-ups carried on, understood first time, and answered quickly?

Every voice or chat turn is counted with what can be seen without AI: whether it carried on the last conversation
(a new wake word within "Carry on a conversation for"), what kind of follow-up it was ("what about the shed?",
"why?", "make it warmer"), whether the reply had to ask which thing was meant, whether the person then corrected
it ("no, I meant…"), whether it failed, and how long it took. No words are kept — only those labels and counts —
and nothing is kept in guest mode. Shown on the Mind tab with the voice timings (setting "Show voice timings").

These are the Phase 2 measures: a change to how conversations work is judged on them.
"""
from __future__ import annotations

from collections import Counter, deque
from contextvars import ContextVar
from statistics import median
from typing import Any

from homeassistant.util import dt as dt_util

TURN: ContextVar[dict | None] = ContextVar("sentient_turn_quality", default=None)  # set for the turn being answered
KEEP = 300  # turns
MIN_TURNS = 10  # before rates are shown


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


def _shows_in(memory: str, text: str, need: int = 2) -> bool:
    """Whether a memory's own words show up in a piece of text (at least *need* of its words)."""
    words = {w for w in _WORD.findall((memory or "").casefold()) if w not in _COMMON}
    return bool(words) and len(words & set(_WORD.findall((text or "").casefold()))) >= min(need, len(words))


class ConversationQuality:
    def __init__(self) -> None:
        self.turns: deque[dict[str, Any]] = deque(maxlen=KEEP)

    def clear(self) -> None:
        self.turns.clear()

    def record(self, *, text: str, reply: str, carried: bool, asked_back: bool, error: bool,
               ms: int | None = None, memories: list[str] | None = None, recalled: bool = False,
               corrected: bool = False) -> dict[str, Any]:
        kind = classify(text)
        offered = list(memories or [])
        turn = {"time": dt_util.utcnow().isoformat(), "carried": bool(carried), "kind": kind,
                "asked_back": bool(asked_back), "error": bool(error), "ms": ms,
                # memory recall: how many memories the voice was given, how many show in the reply, whether it had
                # to search for more, and whether it was corrected (counts only — no memory text is kept)
                "memories_offered": len(offered), "memories_used": sum(_shows_in(m, reply) for m in offered),
                "relevant_offered": sum(_shows_in(m, text, 1) for m in offered),
                "recalled": bool(recalled), "memory_corrected": bool(corrected)}
        # a correction straight after an answer means that answer was wrong: count it against the turn before
        if kind == "correction" and self.turns:
            self.turns[-1]["corrected"] = True
        self.turns.append(turn)
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
            "median_ms": round(median(times)) if len(times) >= MIN_TURNS else None,
            "note": "Counted from what's said, not what was meant: a correction is someone saying \"no, I meant…\" straight after.",
        }


def of(rt: Any) -> ConversationQuality:
    """The house's conversation measures (made on first use)."""
    found = getattr(rt, "conversation_quality", None)
    if found is None:
        found = ConversationQuality()
        rt.conversation_quality = found
    return found
