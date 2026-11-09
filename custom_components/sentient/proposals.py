"""What I'd change: one or two small settings changes suggested from the self-check, applied only on a yes.

Each proposal comes from a finding with the numbers behind it ("I said the same remark 9 times this week"),
names the one setting it would change and what it is now, and waits on the Mind page. Nothing is changed
until someone says yes there; a no is remembered for a month so it isn't suggested again straight away.
Only a short list of everyday settings can ever be proposed — never safety, security, the alarm or what
the house controls.
"""

from __future__ import annotations

from datetime import timedelta
from copy import deepcopy
import re
from typing import Any, Callable

from homeassistant.config_entries import ConfigEntry
from homeassistant.core import HomeAssistant
from homeassistant.helpers.storage import Store
from homeassistant.util import dt as dt_util

from .const import (
    CHATTINESS_LIMITS,
    CONF_BRIEFING_SECTIONS,
    CONF_CHATTINESS,
    CONF_DAILY_AI_LIMIT,
    CONF_PROPOSALS,
    CONF_REMARK_REPEAT_HOURS,
    DEFAULT_BRIEFING_SECTIONS,
    DEFAULT_CHATTINESS,
    DEFAULT_DAILY_AI_LIMIT,
    DEFAULT_REMARK_REPEAT_HOURS,
    DOMAIN,
)
from .trust import KINDS as TRUST_KINDS

MAX_OPEN = 2
MAX_AGE = timedelta(days=7)
ASK_AGAIN_AFTER = timedelta(days=30)
# the only settings a proposal may touch
ALLOWED = {CONF_REMARK_REPEAT_HOURS, CONF_CHATTINESS, CONF_BRIEFING_SECTIONS, CONF_DAILY_AI_LIMIT, *TRUST_KINDS.values()}
_QUIETER = {"chatty": "normal", "normal": "quiet"}  # one step at a time, never to off
# what a question is about → the briefing section that would answer it
_SECTION_FOR = [
    (re.compile(r"\b(weather|rain|raining|temperature|cold|warm|hot|forecast|umbrella|coat)\b", re.I), "weather"),
    (re.compile(r"\b(calendar|appointment|diary|on today|plans|events?)\b", re.I), "calendar"),
    (re.compile(r"\b(to.?do|shopping|list)\b", re.I), "todo"),
    (re.compile(r"\b(reminders?)\b", re.I), "reminders"),
    (re.compile(r"\b(electricity|energy|power|kwh|bill)\b", re.I), "energy"),
    (re.compile(r"\b(locked|alarm|secure|security)\b", re.I), "security"),
    (re.compile(r"\b(open|shut|closed)\b", re.I), "open"),
    (re.compile(r"\b(visitors?|door ?bell|anyone (come|been))\b", re.I), "visitors"),
]


def section_for(question: str) -> str | None:
    return next((section for pattern, section in _SECTION_FOR if pattern.search(question)), None)


class Proposals:
    def __init__(self, hass: HomeAssistant, entry: ConfigEntry, rt_getter: Callable[[], Any]) -> None:
        self.hass = hass
        self.entry = entry
        self._rt = rt_getter
        self._store: Store[dict[str, Any]] = Store(hass, 1, f"{DOMAIN}.{entry.entry_id}.proposals")
        self.open: list[dict[str, Any]] = []
        self.answered: dict[str, dict[str, str]] = {}

    async def async_start(self) -> None:
        data = await self._store.async_load() or {}
        self.open = [p for p in data.get("open", []) if self._valid(p)]
        self.answered = dict(data.get("answered") or {})

    def _save(self) -> None:
        self._store.async_delay_save(lambda: {"open": self.open, "answered": self.answered}, 5)

    @property
    def options(self) -> dict[str, Any]:
        return {**self.entry.data, **self.entry.options}

    @property
    def enabled(self) -> bool:
        return bool(self.options.get(CONF_PROPOSALS, True))

    # ----- making them ---------------------------------------------------------------
    def candidates(self, findings: list[dict[str, Any]]) -> list[dict[str, Any]]:
        opts = self.options
        out: list[dict[str, Any]] = []
        for f in findings:
            kind = f.get("kind")
            if kind == "repeat":
                now = float(opts.get(CONF_REMARK_REPEAT_HOURS, DEFAULT_REMARK_REPEAT_HOURS) or 0)
                if 0 < now < 24:
                    new = float(min(24, max(12, now * 2)))
                    out.append(self._make("repeat", f["text"], CONF_REMARK_REPEAT_HOURS, now, new,
                                          f"Say the same remark at most once every {new:g} hours (now {now:g})"))
            elif kind == "chatty" and f.get("about") == "remarks":
                now = opts.get(CONF_CHATTINESS, DEFAULT_CHATTINESS)
                if (new := _QUIETER.get(now)) is not None:
                    out.append(self._make("chatty", f["text"], CONF_CHATTINESS, now, new,
                                          f"Make fewer remarks: up to {CHATTINESS_LIMITS[new]} an hour instead of {CHATTINESS_LIMITS[now]}"))
            elif kind == "question" and (section := section_for(str(f.get("about") or ""))):
                now = list(opts.get(CONF_BRIEFING_SECTIONS) or DEFAULT_BRIEFING_SECTIONS)
                if section not in now:
                    out.append(self._make(f"briefing:{section}", f["text"], CONF_BRIEFING_SECTIONS, now, [*now, section],
                                          f"Add {section} to the briefing, since it's asked about so often"))
            elif kind == "trust" and (key := TRUST_KINDS.get(str(f.get("about")))) and opts.get(key) == "ask":
                out.append(self._make(f"trust:{f['about']}", f["text"], key, "ask", "tell",
                                      f"Stop asking before the {f['about']} — just say what I'm doing"))
            elif kind == "ai" and f.get("about") == "limit":
                now = int(opts.get(CONF_DAILY_AI_LIMIT, DEFAULT_DAILY_AI_LIMIT) or 0)
                if now > 0:
                    new = min(5000, max(now + 1, int(round(now * 1.5 / 10) * 10)))
                    if new <= now:
                        continue
                    out.append(self._make("ai_limit", f["text"], CONF_DAILY_AI_LIMIT, now, new,
                                          f"Allow {new} background AI calls a day instead of {now} (your AI provider may charge for more)"))
        return out

    @staticmethod
    def _make(key: str, because: str, option: str, was: Any, value: Any, change: str) -> dict[str, Any]:
        return {"id": key, "because": because, "option": option, "was": deepcopy(was), "value": deepcopy(value), "change": change,
                "made": dt_util.utcnow().isoformat()}

    @staticmethod
    def _valid(p):
        if not isinstance(p, dict) or not isinstance(p.get('id'), str) or not isinstance(p.get('option'), str) or p['option'] not in ALLOWED:
            return False
        if not all(key in p for key in ('was', 'value', 'change', 'because', 'made')):
            return False
        if not isinstance(p['change'], str) or not isinstance(p['because'], str):
            return False
        when = dt_util.parse_datetime(p['made']) if isinstance(p['made'], str) else None
        return when is not None and timedelta(0) <= dt_util.utcnow() - dt_util.as_utc(when) < MAX_AGE

    def _effective(self, key):
        defaults = {CONF_REMARK_REPEAT_HOURS: DEFAULT_REMARK_REPEAT_HOURS, CONF_CHATTINESS: DEFAULT_CHATTINESS,
                    CONF_BRIEFING_SECTIONS: DEFAULT_BRIEFING_SECTIONS, CONF_DAILY_AI_LIMIT: DEFAULT_DAILY_AI_LIMIT,
                    **{k: 'do' for k in TRUST_KINDS.values()}}
        value = self.options.get(key)
        return defaults.get(key) if value is None else value

    def refresh(self, findings: list[dict[str, Any]]) -> list[dict[str, Any]]:
        """After the self-check: add new proposals (not ones turned down lately), drop ones that no longer fit."""
        if not self.enabled:
            if self.open:
                self.open = []
                self._save()
            return []
        now = dt_util.utcnow()
        self.open = [p for p in self.open if self._valid(p) and self._effective(p['option']) == p['was']]
        added = []
        for p in self.candidates(findings):
            if len(self.open) >= MAX_OPEN or any(o["id"] == p["id"] for o in self.open):
                continue
            last = self.answered.get(p["id"])
            if last and (when := dt_util.parse_datetime(last.get("time", ""))) and now - when < ASK_AGAIN_AFTER:
                continue
            self.open.append(p)
            added.append(p)
        self._save()
        return added

    def listing(self) -> list[dict[str, Any]]:
        return [{k: p[k] for k in ("id", "change", "because")} for p in self.open if self._valid(p)] if self.enabled else []

    # ----- answering -----------------------------------------------------------------
    def answer(self, proposal_id: str, yes: bool) -> dict[str, Any]:
        if not self.enabled:
            return {'error': 'Settings suggestions are switched off'}
        p = next((o for o in self.open if o["id"] == proposal_id), None)
        if p is None:
            return {"error": "That suggestion has gone — it may already have been answered"}
        if not self._valid(p) or self._effective(p['option']) != p['was']:
            self.open.remove(p)
            self._save()
            return {'error': 'That suggestion is out of date; no setting was changed'}
        from .numeric import finite_float
        key, value = p['option'], p['value']
        valid_value = (value == 'tell' and p['was'] == 'ask') if key in TRUST_KINDS.values() else (
            value == _QUIETER.get(p['was']) if key == CONF_CHATTINESS else
            isinstance(value, list) and all(isinstance(s, str) for s in value) if key == CONF_BRIEFING_SECTIONS else
            type(value) in (int, float) and finite_float(value) is not None and finite_float(p['was']) is not None
            and float(value) > float(p['was']) and float(value) <= (5000 if key == CONF_DAILY_AI_LIMIT else 24)
            and (key != CONF_DAILY_AI_LIMIT or type(value) is int))
        if not valid_value:
            return {'error': 'That suggestion contains an invalid change'}
        self.open.remove(p)
        self.answered[p["id"]] = {"answer": "yes" if yes else "no", "time": dt_util.utcnow().isoformat()}
        self._save()
        rt = self._rt()
        if not yes:
            if rt is not None:
                rt.timeline.add("mind", f"Left as it is: {p['change'][0].lower()}{p['change'][1:]}", "you said no")
            return {"changed": False}
        if p["option"] not in ALLOWED:
            return {"error": "That setting can't be changed this way"}
        self.hass.config_entries.async_update_entry(self.entry, options={**self.options, p["option"]: p["value"]})
        if rt is not None:
            rt.timeline.add("mind", f"Changed: {p['change'][0].lower()}{p['change'][1:]}", "you said yes")
        return {"changed": True}
