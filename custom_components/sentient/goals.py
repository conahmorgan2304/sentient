"""Goals: something the household wants to change over days or weeks ("use less heating this month").

Set by voice (sentient_goal) or kept from the page. A goal can follow a sensor: when it's set, the
usual daily value is taken from the last week of history; every night yesterday's value is compared
with it and noted, and the voice is told how it's going so it can mention progress or suggest
something when it fits. When the time is up it's closed with a summary on the timeline. No AI of
its own; nothing is changed in the house. Off until switched on.
"""

from __future__ import annotations

from copy import deepcopy
from datetime import datetime, timedelta
from typing import Any, Callable
import uuid

from homeassistant.core import HomeAssistant, callback
from homeassistant.helpers.event import async_track_time_change
from homeassistant.helpers.storage import Store
from homeassistant.util import dt as dt_util

from .const import CONF_GOALS, DOMAIN
from .goal_statistics import daily_values, VERSION

STORAGE_VERSION = 1
MAX_OPEN = 8


def _num(v: Any) -> float | None:
    from .numeric import finite_float
    return finite_float(v)




class Goals:
    def __init__(self, hass: HomeAssistant, entry_id: str, options: dict[str, Any], rt_getter: Callable[[], Any]) -> None:
        self.hass = hass
        self.options = options
        self._rt = rt_getter
        self._store: Store[dict[str, Any]] = Store(hass, STORAGE_VERSION, f"{DOMAIN}.{entry_id}.goals")
        self.items: list[dict[str, Any]] = []
        self._unsub = None
        self._stopped = False
        self._checking = False

    @property
    def enabled(self) -> bool:
        return bool(self.options.get(CONF_GOALS))

    async def async_start(self) -> None:
        self._stopped = False
        self.items = list((await self._store.async_load() or {}).get("items", []))
        self._unsub = async_track_time_change(self.hass, self._nightly, hour=4, minute=50, second=0)

    @callback
    def async_stop(self) -> None:
        self._stopped = True
        if self._unsub:
            self._unsub()
            self._unsub = None

    @callback
    def _save(self) -> None:
        self._store.async_delay_save(lambda: {"items": self.items[-40:]}, 5)

    def open(self) -> list[dict[str, Any]]:
        return [g for g in self.items if not g.get("closed")]

    def _stamp(self):
        speech = getattr(self._rt(), 'speech', None)
        return (getattr(speech, '_privacy_generation', 0), getattr(speech, '_guest_generation', 0))

    def _metric(self, entity_id):
        state = self.hass.states.get(entity_id) if entity_id else None
        return {'unit': state.attributes.get('unit_of_measurement') if state else None,
                'state_class': state.attributes.get('state_class') if state else None,
                'device_class': state.attributes.get('device_class') if state else None}

    async def async_set(self, title: str, entity_id: str | None = None, aim: str = "lower", days: int = 30) -> dict[str, Any]:
        if self._stopped or not self.enabled:
            return {'error': 'Goals are switched off'}
        if not title.strip():
            return {'error': 'A goal needs a title'}
        options, values, stamp = self.options, deepcopy(self.options), self._stamp()
        if len(self.open()) >= MAX_OPEN:
            return {"error": f"there are already {MAX_OPEN} goals; close one first"}
        days = max(1, min(int(days), 120))
        goal = {"id": uuid.uuid4().hex[:8], "title": title.strip()[:120], "entity_id": entity_id, "aim": aim if aim in ("lower", "higher") else "lower",
                "created": dt_util.utcnow().isoformat(), "ends": (dt_util.utcnow() + timedelta(days=days)).isoformat(),
                "baseline": None, "checks": [], "closed": None,
                "statistics_version": VERSION, "baseline_days": 0, **self._metric(entity_id)}
        if entity_id:
            if self.hass.states.get(entity_id) is None:
                return {"error": f"no sensor called {entity_id}"}
            week = await daily_values(self.hass, entity_id, 7)
            if week:
                valid = [v for _, raw in week if (v := _num(raw)) is not None]
                baseline = _num(sum(valid) / len(valid)) if valid else None
                goal['baseline'] = baseline
                goal['baseline_days'] = len(valid)
        if (self._stopped or not self.enabled or self.options is not options or self.options != values or self._stamp() != stamp):
            return {'error': 'Goal settings changed while reading history; nothing was added'}
        if len(self.open()) >= MAX_OPEN:
            return {'error': f'there are already {MAX_OPEN} goals; close one first'}
        if self._metric(entity_id) != {key: goal[key] for key in ('unit', 'state_class', 'device_class')}:
            return {'error': 'The sensor unit or type changed while reading history; please try again'}
        if entity_id and goal['baseline'] is None:
            goal['statistics_note'] = 'No comparable baseline history is available'
        self.items.append(goal)
        self._save()
        if (rt := self._rt()) is not None:
            rt.timeline.add("mind", f"New goal: {goal['title']}", f"until {dt_util.as_local(dt_util.parse_datetime(goal['ends'])):%d %b}")
        return {"goal": goal["title"], "usual_per_day": goal["baseline"], "baseline_days": goal['baseline_days'], "ends": goal["ends"]}

    def close(self, words: str) -> dict[str, Any] | None:
        low = words.casefold().strip()
        if not low:
            return None
        exact = [g for g in self.open() if g['id'] == low or g['title'].casefold() == low]
        matches = exact or [g for g in self.open() if low in g['title'].casefold()]
        if len(matches) != 1:
            return None
        goal = matches[0]
        if goal.get('entity_id') and self._metric(goal['entity_id']) != {key: goal.get(key) for key in ('unit', 'state_class', 'device_class')}:
            goal['needs_rebaseline'] = True
            goal['statistics_note'] = 'Sensor unit or type changed; the old comparison is not used'
        goal['closed'] = dt_util.utcnow().isoformat()
        self._save()
        return goal

    @staticmethod
    def progress(goal: dict[str, Any]) -> str | None:
        if goal.get('statistics_version') != VERSION or goal.get('needs_rebaseline'):
            return None
        baseline = _num(goal.get('baseline'))
        recent = [value for c in goal.get('checks', [])[-7:] if (value := _num(c.get('value'))) is not None]
        if not recent or baseline in (None, 0):
            return None
        avg = _num(sum(recent) / len(recent))
        change = _num((avg - baseline) / abs(baseline) * 100) if avg is not None else None
        if change is None:
            return None
        better = change < 0 if goal["aim"] == "lower" else change > 0
        return (f"{abs(round(change))}% {'lower' if change < 0 else 'higher'} than usual across {len(recent)} recorded day"
                f"{'s' if len(recent) > 1 else ''} — {'going the right way' if better and abs(change) >= 3 else 'about the same' if abs(change) < 3 else 'going the wrong way'}")

    @callback
    def _nightly(self, _now) -> None:
        if self.enabled and self.open():
            self.hass.async_create_task(self.async_check(), eager_start=False)

    async def async_check(self) -> None:
        if self._checking or self._stopped or not self.enabled:
            return
        self._checking = True
        options, values, stamp = self.options, deepcopy(self.options), self._stamp()
        def current():
            return not self._stopped and self.enabled and self.options is options and self.options == values and self._stamp() == stamp
        try:
            await self._async_check(current)
        finally:
            self._checking = False

    def _finish(self, goal, now, rt):
        end = dt_util.parse_datetime(goal.get('ends') or '')
        if end and now >= dt_util.as_utc(end):
            goal['closed'] = now.isoformat()
            if rt is not None:
                rt.timeline.add('mind', f"Goal finished: {goal['title']}",
                    goal.get('statistics_note') or self.progress(goal) or 'no readings to compare')

    async def _async_check(self, current):
        rt = self._rt()
        now = dt_util.utcnow()
        for g in self.open():
            before = deepcopy(g)
            entity = g.get('entity_id')
            metric = self._metric(entity)
            state = self.hass.states.get(entity) if entity else None
            if entity and (state is None or state.state in ('unknown', 'unavailable')):
                g['statistics_note'] = 'Sensor is unavailable; comparison is paused'
                self._finish(g, now, rt)
                continue
            if entity and g.get('statistics_version') == VERSION and metric != {key: g.get(key) for key in ('unit', 'state_class', 'device_class')}:
                g['needs_rebaseline'] = True
                g['statistics_note'] = 'Sensor unit or type changed; recreate this goal for a comparable baseline'
            if g.get('needs_rebaseline'):
                self._finish(g, now, rt)
                continue
            try:
                created = dt_util.parse_datetime(g['created'])
                created_day = dt_util.as_local(created).date() if created else dt_util.now().date()
                migrate = g.get('statistics_version') != VERSION
                baseline = g.get('baseline')
                if migrate and entity:
                    week = await daily_values(self.hass, entity, 7, end=created)
                    good = [v for _, raw in week if (v := _num(raw)) is not None]
                    baseline = _num(sum(good) / len(good)) if good else None
                if not current():
                    return
                if not any(row is g for row in self.items) or g != before or g.get('closed'):
                    continue
                end = dt_util.parse_datetime(g['ends'])
                history_end = min(now, dt_util.as_utc(end)) if end else now
                history_day = dt_util.as_local(history_end).date()
                lookback = max(1, min(120, (history_day - created_day).days)) if migrate else 1
                vals = await daily_values(self.hass, entity, lookback, history_end) if entity else []
            except Exception:
                if current() and any(row is g for row in self.items) and g == before:
                    g['statistics_note'] = 'Daily history is unavailable; no new reading was recorded'
                    self._finish(g, now, rt)
                continue  # one failed recorder read must not prevent checking other goals
            if not current():
                return
            if not any(row is g for row in self.items) or g != before or g.get('closed'):
                continue
            if self._metric(entity) != metric:
                g['statistics_note'] = 'The sensor changed during the history read; comparison is paused'
                self._finish(g, now, rt)
                continue
            if migrate:
                g.update(statistics_version=VERSION, baseline=baseline, baseline_days=len(good) if entity else 0, checks=[], **metric)
            g.pop('statistics_note', None)
            if entity and g.get('baseline') is None:
                g['statistics_note'] = 'No comparable baseline history is available'
            checks = {c['day']: c for c in g['checks']}
            end = dt_util.parse_datetime(g['ends'])
            today = min(dt_util.now().date(), dt_util.as_local(end).date()).isoformat() if end else dt_util.now().date().isoformat()
            for day, raw in vals:
                value = _num(raw)
                if value is not None and created_day.isoformat() <= day < today:
                    checks[day] = {'day': day, 'value': value}
            g['checks'] = [checks[day] for day in sorted(checks)][-120:]
            if entity and g.get('baseline') == 0:
                g['statistics_note'] = 'Baseline is zero; a percentage comparison is not meaningful'
            elif entity and g.get('baseline') is not None:
                expected = (datetime.fromisoformat(today).date() - timedelta(days=1)).isoformat()
                if expected < created_day.isoformat():
                    g['statistics_note'] = 'No completed goal day is available yet'
                elif expected not in checks:
                    g['statistics_note'] = 'The latest completed day has no comparable reading'
            self._finish(g, now, rt)
        self._save()

    def listing(self) -> list[dict[str, Any]]:
        out = []
        for g in self.open():
            entity = g.get('entity_id')
            st = self.hass.states.get(entity) if entity else None
            known_method = g.get('statistics_version') == VERSION
            matching = not entity or self._metric(entity) == {key: g.get(key) for key in ('unit', 'state_class', 'device_class')}
            available = not entity or st is not None and st.state not in ('unknown', 'unavailable')
            comparable = known_method and matching and available and not g.get('needs_rebaseline')
            note = g.get('statistics_note')
            if not known_method:
                note = 'Daily statistics are awaiting refresh'
            elif not available:
                note = 'Sensor is unavailable; comparison is paused'
            elif not matching or g.get('needs_rebaseline'):
                note = 'Sensor unit or type changed; recreate this goal for a comparable baseline'
            out.append({'id': g['id'], 'title': g['title'], 'follows': st.name if st else None,
                        'usual': g.get('baseline') if comparable else None, 'baseline_days': g.get('baseline_days'),
                        'unit': st.attributes.get('unit_of_measurement') if st else None, 'baseline_unit': g.get('unit'),
                        'progress': self.progress(g) if comparable else None, 'statistics_note': note,
                        'ends': g['ends'], 'days_checked': len(g['checks'])})
        return out

    def prompt_line(self) -> str | None:
        goals = self.listing()
        if not goals:
            return None
        parts = [g['title'] + (' (' + '; '.join(s for s in (g.get('progress'), g.get('statistics_note')) if s) + ')'
                 if g.get('progress') or g.get('statistics_note') else '') for g in goals]
        return ("The household's goals (mention progress or suggest something that helps only when it fits naturally): "
                + "; ".join(parts) + ".")
