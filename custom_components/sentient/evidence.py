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


# readings that normally report often: one not heard from in hours is probably stuck, not steady
REPORTS_OFTEN = {"temperature", "humidity", "illuminance", "pressure", "atmospheric_pressure", "carbon_dioxide",
                 "pm25", "pm10", "power", "voltage", "current"}
STALE_AFTER_HOURS = 6


def stale_readings(hass, allowed=None, hours=STALE_AFTER_HOURS):
    """Measurement sensors not heard from for *hours* — [(name, hours since)] oldest first.

    Uses last_reported (any report, changed or not), so a steady reading that keeps reporting isn't stale."""
    now = dt_util.utcnow()
    out = []
    for state in hass.states.async_all("sensor"):
        if state.attributes.get("device_class") not in REPORTS_OFTEN or state.state in ("unknown", "unavailable"):
            continue
        observed = getattr(state, "last_reported", state.last_updated)
        age = (now - observed).total_seconds() / 3600
        if age >= hours and (allowed is None or allowed(state.entity_id)):
            out.append((state.name, int(age)))
    return sorted(out, key=lambda r: -r[1])


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
