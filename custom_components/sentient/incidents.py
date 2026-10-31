"""Bounded security episodes. Time correlation is explicitly not identity tracking."""
from __future__ import annotations

from datetime import timedelta
import uuid

from homeassistant.const import EVENT_STATE_CHANGED
from homeassistant.core import callback
from homeassistant.helpers.storage import Store
from homeassistant.util import dt as dt_util

from .sessions import CONF_INCIDENTS

GAP = timedelta(minutes=3)
ALERT_LEVELS = {'night': 1, 'fence': 2, 'loitering': 3}


class Incidents:
    def __init__(self, hass, entry_id, options):
        self.hass, self.options = hass, options
        self._store = Store(hass, 1, f'sentient.{entry_id}.incidents')
        self.items = []
        self._pending_alerts = {}
        self._unsub = None

    @property
    def enabled(self):
        return bool(self.options.get(CONF_INCIDENTS))

    async def async_start(self):
        data = await self._store.async_load() or {}
        self.items = data.get('items', [])
        self._prune()
        self._unsub = self.hass.bus.async_listen(EVENT_STATE_CHANGED, self._changed)

    @callback
    def async_stop(self):
        if self._unsub:
            self._unsub()
            self._unsub = None

    def _prune(self):
        cutoff = (dt_util.utcnow() - timedelta(days=7)).isoformat()
        self.items = sorted((i for i in self.items if i.get('end', '') >= cutoff),
                            key=lambda i: i['end'], reverse=True)[:100]
        live = {i['id'] for i in self.items}
        self._pending_alerts = {k: v for k, v in self._pending_alerts.items() if k in live}

    def _save(self):
        self._prune()
        self._store.async_delay_save(lambda: {'items': self.items}, 5)

    @callback
    def _changed(self, event):
        if not self.enabled:
            return
        eid = event.data.get('entity_id')
        new, old = event.data.get('new_state'), event.data.get('old_state')
        if new is None or old is None or new.state == old.state:
            return
        from .const import CONF_CAMSEC_ENTRIES, CONF_CAMSEC_SENSORS
        contacts = self.options.get(CONF_CAMSEC_ENTRIES) or []
        sensors = self.options.get(CONF_CAMSEC_SENSORS) or []
        if eid in contacts:
            self.add(eid, f'{new.name}: {new.state}', 'contact',
                     evidence='unknown' if new.state in ('unknown', 'unavailable') else 'observed')
        elif eid in sensors and new.state == 'on' and old.state not in ('unknown', 'unavailable'):
            from .camsecurity import camera_for
            self.add(camera_for(self.hass, eid) or eid, f'Person reported at {new.name}', 'detection')

    def add(self, source, text, kind, *, evidence='observed', when=None, event_id=None):
        if not self.enabled:
            return None
        now = when or dt_util.utcnow()
        self._prune()
        if now < dt_util.utcnow() - timedelta(days=7):
            return None
        if event_id:
            existing = next((item for item in self.items if any(
                e.get('event_id') == event_id and e.get('kind') == kind and e.get('source') == source
                for e in item['evidence'])), None)
            if existing is not None:
                return existing
        possible = []
        for item in self.items:
            start, end = dt_util.parse_datetime(item['start']), dt_util.parse_datetime(item['end'])
            if start - GAP <= now <= end + GAP:
                if kind == 'clip' and source not in item['sources']:
                    continue  # delayed clips must also match a known camera
                possible.append(item)
        item = possible[0] if len(possible) == 1 else None
        if item is None:
            item = {'id': uuid.uuid4().hex[:12], 'start': now.isoformat(), 'end': now.isoformat(),
                    'sources': [], 'evidence': [], 'alert_level': 0,
                    'link': 'Events close in time; not proof of one person or a confirmed route.'}
            self.items.insert(0, item)
        if event_id and any(e.get('event_id') == event_id and e['kind'] == kind for e in item['evidence']):
            return item
        item['start'] = min(item['start'], now.isoformat())
        item['end'] = max(item['end'], now.isoformat())
        if source not in item['sources']:
            item['sources'].append(source)
        item['evidence'].append({'time': now.isoformat(), 'source': source, 'kind': kind,
                                 'status': evidence, 'text': text[:600], 'event_id': event_id})
        item['evidence'] = item['evidence'][-50:]
        self._save()
        return item

    def alert(self, sensor, text, reason):
        from .camsecurity import camera_for
        item = self.add(camera_for(self.hass, sensor) or sensor, text, 'alert', evidence='inferred')
        if item is None:
            return True, None
        level = ALERT_LEVELS.get(reason, 1)
        pending = self._pending_alerts.setdefault(item['id'], set())
        if level <= max([item['alert_level'], *pending]):
            return False, item['id']
        pending.add(level)  # reserve while the camera check runs; do not persist a delivered alert yet
        return True, item['id']

    def finish_alert(self, incident_id, level, accepted):
        pending = self._pending_alerts.get(incident_id, set())
        pending.discard(level)
        if not pending:
            self._pending_alerts.pop(incident_id, None)
        item = next((i for i in self.items if i['id'] == incident_id), None)
        if accepted and item is not None:
            item['alert_level'] = max(item['alert_level'], level)
            self._save()

    def recent(self, query=''):
        if not self.enabled:
            return []
        self._prune()
        query = query.lower().strip()
        return [dict(i, evidence=list(i['evidence'])) for i in self.items
                if not query or query in (' '.join(i['sources']) + ' ' + ' '.join(e['text'] for e in i['evidence'])).lower()][:8]
