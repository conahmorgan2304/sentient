"""Grade its own answers: each night, check what the voice said about devices against what the house recorded.

For every reply in the last day that plainly states one device's state — "the back door's closed", "the kitchen
light is on" — the recorder is asked what that device actually was at that moment (and twenty seconds later, so a
device still catching up doesn't count). A wrong one goes into "Where the voice went wrong" on the Mind page and
becomes a lesson ("Check the back door on the device before saying whether it's open or closed."), once per device.

Only clear statements are checked: one device and one state in a sentence, no question, no "if", no negation, and
not a report of something it just did (those are checked when they happen). No AI. Setting: "Grade its own answers"
(off by default); needs the reliability log.
"""
from __future__ import annotations

from datetime import datetime, timedelta
import logging
import re
from typing import Any, Callable

from homeassistant.core import HomeAssistant, callback
from homeassistant.helpers.event import async_track_time_change
from homeassistant.util import dt as dt_util

_LOGGER = logging.getLogger(__name__)

CONF_SELF_GRADING = "self_grading"
SETTLE = timedelta(seconds=20)
_OPENING = {"door", "window", "garage_door", "opening", "gate"}
_SENTENCE = re.compile(r"[^.!?]+[.!?]?")
_SKIP = re.compile(r"\?|\b(?:if|when|whenever|should|shall|would|could|might|maybe|probably|not|n't|no longer|"
                   r"never|was|were|earlier|ago|i've|i have|i'll|i will|i've just|turned|switched|turning|switching|"
                   r"done)\b|n't\b", re.IGNORECASE)
# claim word -> (kind of device, state it means)
_CLAIMS = [
    (re.compile(r"\b(?:is|are|'s|’s) (?:still |now )?(?:wide )?open\b", re.I), "opening", "on"),
    (re.compile(r"\b(?:is|are|'s|’s) (?:still |now )?(?:closed|shut)\b", re.I), "opening", "off"),
    (re.compile(r"\b(?:is|are|'s|’s) (?:still |now )?unlocked\b", re.I), "lock", "unlocked"),
    (re.compile(r"\b(?:is|are|'s|’s) (?:still |now )?locked\b", re.I), "lock", "locked"),
    (re.compile(r"\b(?:is|are|'s|’s) (?:still |now )?on\b", re.I), "switch", "on"),
    (re.compile(r"\b(?:is|are|'s|’s) (?:still |now )?off\b", re.I), "switch", "off"),
]
_COVER = {"on": "open", "off": "closed"}
_WORDS = {"on": "on or off", "off": "on or off", "locked": "locked", "unlocked": "locked"}


def _name(state: Any) -> str:
    return re.sub(r"\s+(sensor|contact|state)$", "", (state.name or "").strip(), flags=re.I).lower()


def _candidates(hass: HomeAssistant) -> list[tuple[str, str, str]]:
    """(name, entity_id, kind) for every device the voice may talk about, longest names first."""
    from .acting import exposed

    out = []
    for st in hass.states.async_all(("binary_sensor", "cover", "lock", "light", "switch", "fan")):
        domain = st.entity_id.split(".", 1)[0]
        if domain == "binary_sensor" and st.attributes.get("device_class") not in _OPENING:
            continue
        kind = "opening" if domain in ("binary_sensor", "cover") else "lock" if domain == "lock" else "switch"
        name = _name(st)
        if len(name) >= 4 and exposed(hass, st.entity_id):
            out.append((name, st.entity_id, kind))
    return sorted(out, key=lambda c: len(c[0]), reverse=True)


def claims(hass: HomeAssistant, text: str, candidates: list[tuple[str, str, str]] | None = None) -> list[tuple[str, str]]:
    """(entity_id, expected state) for each plain statement about one device in *text*."""
    candidates = _candidates(hass) if candidates is None else candidates
    found = []
    for sentence in _SENTENCE.findall(text or ""):
        s = sentence.strip()
        if not s or _SKIP.search(s):
            continue
        low = s.lower()
        named = []
        rest = low
        for name, entity_id, kind in candidates:
            if re.search(rf"\b{re.escape(name)}\b", rest):
                named.append((entity_id, kind))
                rest = re.sub(rf"\b{re.escape(name)}\b", " ", rest)  # "kitchen light" isn't also "light"
        said = [(kind, state) for pattern, kind, state in _CLAIMS if pattern.search(s)]
        if len(named) != 1 or len(said) != 1:
            continue  # two devices or two states in one sentence: not plain enough to judge
        entity_id, kind = named[0]
        claim_kind, state = said[0]
        if kind != claim_kind:
            if not (kind == "opening" and claim_kind == "switch"):
                continue
        if entity_id.startswith("cover."):
            state = _COVER.get(state, state)
        found.append((entity_id, state))
    return found


async def _states_at(hass: HomeAssistant, entity_ids: list[str], when: datetime) -> dict[str, tuple[str | None, str | None]]:
    """entity -> (state at *when*, state SETTLE later), from the recorder."""
    from homeassistant.components.recorder import get_instance, history

    def _read() -> dict[str, tuple[str | None, str | None]]:
        out = {}
        for moment in (when, when + SETTLE):
            rows = history.get_significant_states(hass, moment, moment + timedelta(seconds=1), entity_ids,
                                                  include_start_time_state=True, significant_changes_only=False,
                                                  minimal_response=False, no_attributes=True)
            for eid in entity_ids:
                state = rows.get(eid, [None])[0] if rows.get(eid) else None
                value = getattr(state, "state", None) if state is not None and not isinstance(state, dict) else (
                    state.get("state") if isinstance(state, dict) else None)
                before = out.get(eid, (None, None))
                out[eid] = (value, None) if moment == when else (before[0], value)
        return out

    return await get_instance(hass).async_add_executor_job(_read)


async def async_grade(hass: HomeAssistant, rt: Any, hours: float = 24) -> dict[str, Any]:
    """Check the last day's replies. Returns {'checked': n, 'wrong': [...], 'lessons': [...]}."""
    if not rt.options.get(CONF_SELF_GRADING) or not rt.reliability.enabled:
        return {"skipped": "self-grading or the reliability log is off"}
    candidates = _candidates(hass)
    statements = []  # (when, room, asked, said, entity_id, expected)
    for ep in rt.conversations.recent(hours=hours, limit=200):
        asked = ""
        for turn in ep.get("turns") or []:
            if turn.get("who") == "them":
                asked = turn.get("text") or ""
                continue
            when = dt_util.parse_datetime(str(turn.get("at") or ""))
            if when is None:
                continue  # kept before turns had a time: can't tell what it was then
            for entity_id, expected in claims(hass, turn.get("text") or "", candidates):
                statements.append((when, ep.get("room"), asked, turn["text"], entity_id, expected))
    wrong, lessons = [], []
    for when, room, asked, said, entity_id, expected in statements:
        try:
            then, later = (await _states_at(hass, [entity_id], when)).get(entity_id, (None, None))
        except Exception as err:  # noqa: BLE001 - no recorder, no grading
            _LOGGER.debug("Couldn't grade %s: %s", entity_id, err)
            continue
        if then in (None, "unavailable", "unknown") or then == expected or later == expected:
            continue
        state = hass.states.get(entity_id)
        name = state.name if state is not None else entity_id
        kind = "open or closed" if entity_id.split(".", 1)[0] in ("binary_sensor", "cover") else _WORDS.get(expected, "on or off")
        if rt.reliability.add_graded(time=when.isoformat(), room=room, asked=asked, said=said,
                                     detail=f"{name} was {then.replace('_', ' ')}, not {expected.replace('_', ' ')}"):
            wrong.append({"entity_id": entity_id, "said": expected, "was": then, "when": when.isoformat()})
            lesson = f"Check the {name.lower()} on the device before saying whether it's {kind}."
            try:
                before = {m.id for m in rt.memory.items}
                memory = rt.memory.add(lesson, "correction", source="reflection")
                if memory.id not in before:
                    lessons.append(memory.text)
            except ValueError:
                pass
    result = {"checked": len(statements), "wrong": wrong, "lessons": lessons}
    if statements:
        rt.timeline.add("mind", f"Checked {len(statements)} thing{'s' if len(statements) != 1 else ''} the voice said: "
                        + (f"{len(wrong)} wrong" if wrong else "all right"), "grading its own answers")
    return result


class SelfGrading:
    """Runs the check once a night (04:10), only while the setting is on."""

    def __init__(self, hass: HomeAssistant, rt_getter: Callable[[], Any]) -> None:
        self.hass = hass
        self._rt = rt_getter
        self._unsub = None
        self.last: dict[str, Any] | None = None

    @callback
    def async_start(self) -> None:
        self._unsub = async_track_time_change(self.hass, self._tick, hour=4, minute=10, second=0)

    @callback
    def async_stop(self) -> None:
        if self._unsub:
            self._unsub()
            self._unsub = None

    @callback
    def _tick(self, _now: datetime) -> None:
        rt = self._rt()
        if rt is not None and rt.options.get(CONF_SELF_GRADING):
            self.hass.async_create_background_task(self._async_run(rt), "sentient_self_grading")

    async def _async_run(self, rt: Any) -> None:
        try:
            self.last = {"time": dt_util.utcnow().isoformat(), **await async_grade(self.hass, rt)}
        except Exception as err:  # noqa: BLE001 - a missed night is no loss
            _LOGGER.debug("Self-grading skipped: %s", err)
