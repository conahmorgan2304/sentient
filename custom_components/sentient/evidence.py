"""Describe reported state and its age without equating silence with a stale sensor."""
from homeassistant.util import dt as dt_util
from homeassistant.core import callback


def describe_state(hass, entity_id):
    state = hass.states.get(entity_id)
    if state is None:
        return {'entity_id': entity_id, 'status': 'unknown', 'reason': 'entity missing'}
    observed = getattr(state, 'last_reported', state.last_updated)
    age = max(0, int((dt_util.utcnow() - observed).total_seconds()))
    unavailable = state.state in ('unknown', 'unavailable')
    return {'entity_id': entity_id, 'name': state.name, 'state': state.state,
            'last_known': hass.data.get('sentient_last_evidence', {}).get(entity_id) if unavailable else None,
            'status': 'unknown' if unavailable else 'observed',
            'reported_at': observed.isoformat(), 'report_age_seconds': age,
            'changed_at': state.last_changed.isoformat(),
            'note': 'Unavailable readings cannot establish the current state.' if unavailable else
                    'This is the last reported state, not an independent physical verification. Age alone does not prove staleness.'}


class Evidence:
    """Remember the last usable security reading across temporary outages and restarts."""
    def __init__(self, hass, entry_id, options):
        from homeassistant.helpers.storage import Store
        self.hass, self.options = hass, options
        self._store = Store(hass, 1, f'sentient.{entry_id}.evidence')
        self.last = {}
        self._unsub = None

    async def async_start(self):
        self.last = (await self._store.async_load() or {}).get('last', {})
        self.hass.data['sentient_last_evidence'] = self.last
        for state in self.hass.states.async_all():
            self._remember(state)
        self._store.async_delay_save(lambda: {'last': self.last}, 10)
        self._unsub = self.hass.bus.async_listen('state_changed', self._changed)

    def async_stop(self):
        if self._unsub:
            self._unsub()
        if self.hass.data.get('sentient_last_evidence') is self.last:
            self.hass.data.pop('sentient_last_evidence', None)

    def _remember(self, state):
        if state is None or state.domain not in ('binary_sensor', 'cover', 'lock', 'alarm_control_panel') or state.state in ('unknown', 'unavailable'):
            return False
        if state.domain == 'binary_sensor' and state.attributes.get('device_class') not in ('door', 'window', 'garage_door', 'opening'):
            return False
        self.last[state.entity_id] = {'state': state.state, 'reported_at': getattr(state, 'last_reported', state.last_updated).isoformat()}
        while len(self.last) > 1000:
            self.last.pop(next(iter(self.last)))
        return True

    @callback
    def _changed(self, event):
        if self._remember(event.data.get('new_state')):
            self._store.async_delay_save(lambda: {'last': self.last}, 10)
