"""Camera checks: a standing yes-or-no question for a camera, asked when its own sensor sees
something — "is there a delivery van?" when the front camera spots a car. A yes goes on the
timeline (and so to phones) and can be said out loud. One AI look per trigger, with a cooldown,
counted against the daily AI limit.
"""

from __future__ import annotations

from datetime import timedelta
import hashlib
import json
import logging
from typing import Any, Callable

from homeassistant.const import EVENT_STATE_CHANGED
from homeassistant.core import Event, HomeAssistant, callback
from homeassistant.util import dt as dt_util

from .ai import async_generate
from .const import CONF_AI_TASK_ENTITY
from .items import Items
from .kinds import in_windows
from .numeric import finite_float

_LOGGER = logging.getLogger(__name__)


def triggered(new, old) -> bool:
    if new is None or old is None or old.state in ("unavailable", "unknown") or new.state in ("unavailable", "unknown"):
        return False
    if new.domain == "event":
        return new.state != old.state
    if new.domain == "sensor":  # a count of objects (e.g. Frigate's "car count") going up
        before, after = finite_float(old.state), finite_float(new.state)
        return before is not None and after is not None and after > before
    return new.state == "on" and old.state != "on"


class CameraChecks:
    def __init__(self, hass: HomeAssistant, items: Items, runtime: Callable[[], Any]) -> None:
        self.hass = hass
        self.items = items
        self._rt = runtime
        self._busy: set[str] = set()
        self._unsub = None
        self._generation = 0
        self._stopped = False

    @callback
    def async_start(self) -> None:
        self._stopped = False
        self._unsub = self.hass.bus.async_listen(EVENT_STATE_CHANGED, self._changed)

    @callback
    def async_stop(self) -> None:
        self._stopped = True
        self._generation += 1
        if self._unsub:
            self._unsub()
            self._unsub = None

    @callback
    def _changed(self, event: Event) -> None:
        entity_id = event.data.get("entity_id")
        for check in self.items.enabled():
            if entity_id in (check.get("triggers") or []) and triggered(event.data.get("new_state"), event.data.get("old_state")):
                if self._ready(check):
                    self.hass.async_create_task(self.async_check(check))

    def _ready(self, check: dict[str, Any]) -> bool:
        now = dt_util.utcnow()
        if self._stopped or not check.get('enabled', True) or check["id"] in self._busy or not in_windows(check.get("times"), dt_util.as_local(now)):
            return False
        last = dt_util.parse_datetime(check.get("last") or "")
        return not (last and now - last < timedelta(minutes=float(check.get("cooldown_minutes") or 0)))

    async def async_check(self, check: dict[str, Any], force: bool = False) -> dict[str, Any]:
        rt = self._rt()
        camera = check.get('camera')
        identity = json.dumps([camera, check.get('spot'), check.get('question')], sort_keys=True, default=str)
        prefilter_key = f"check:{check['id']}:{hashlib.sha256(identity.encode()).hexdigest()[:16]}"
        evaluated = False
        generation, options = self._generation, rt.options
        ai_generation = getattr(getattr(rt, 'ai', None), '_generation', 0)
        privacy = getattr(rt.speech, '_privacy_generation', 0)

        def valid():
            return (not self._stopped and generation == self._generation and rt.options is options
                    and getattr(getattr(rt, 'ai', None), '_generation', 0) == ai_generation
                    and getattr(rt.speech, '_privacy_generation', 0) == privacy
                    and self.items.items.get(check['id']) is check
                    and (force or (check.get('enabled', True) and not getattr(rt.speech, 'guest_mode', False)
                                   and in_windows(check.get('times'), dt_util.now())))
                    and check.get('camera') == camera and self.hass.states.get(camera) is not None)

        if not isinstance(camera, str) or self.hass.states.get(camera) is None:
            return {'error': 'That camera is not available'}
        if not valid():
            return {'skipped': 'camera check is no longer active or available'}
        if check['id'] in self._busy:
            return {'skipped': 'camera check already running'}
        if not force and not self._ready(check):
            return {'skipped': 'camera check is cooling down'}
        gate = getattr(rt.watches, 'gate', None)
        if not force and gate is not None and (gate.capped or gate.over_budget('camera checks')):
            return {'skipped': 'daily AI limit reached'}
        self._busy.add(check['id'])  # reserve before fingerprint/wake awaits
        try:
            if not force and await rt.watches.unchanged(prefilter_key, camera, check.get('spot') or None):
                if valid():
                    self.items.update(check['id'], last=dt_util.utcnow().isoformat())
                evaluated = valid()
                return {'skipped': 'the picture has not changed since the last look'}
            if not valid():
                return {'skipped': 'camera check changed while preparing'}
            attachment = await rt.watches._attachment(camera, check.get('spot') or None)
            if not valid():
                return {'skipped': 'camera check changed while preparing'}
            if not rt.watches.spend('camera checks', force=force):
                self.items.update(check['id'], last=dt_util.utcnow().isoformat(), last_answer='unknown: daily AI limit reached')
                return {'skipped': 'daily AI limit reached'}
            self.items.update(check['id'], last=dt_util.utcnow().isoformat(), last_answer='unknown: no current result')
            data = {
                'task_name': f"Sentient check: {check['name']}"[:100],
                'instructions': f"Look at this home camera picture and answer: {check.get('question')} "
                                'Set match to true only if you can clearly see it. Describe what you see in a few plain words.',
                'structure': {
                    'match': {'description': 'True only if clearly yes', 'required': True, 'selector': {'boolean': {}}},
                    'description': {'description': 'A few plain words', 'required': False, 'selector': {'text': {}}},
                },
                'attachments': [attachment],
            }
            if ai := rt.options.get(CONF_AI_TASK_ENTITY):
                data['entity_id'] = ai
            response = await async_generate(self.hass, data)
            if not valid():
                return {'skipped': 'camera check changed while waiting for the AI'}
            result = response.get('data') if isinstance(response, dict) else None
            if (not isinstance(result, dict) or type(result.get('match')) is not bool
                    or not isinstance(result.get('description', ''), (str, type(None)))):
                self.items.update(check['id'], last_answer='unknown: invalid AI result')
                return {'error': 'The camera check returned an invalid result; nothing was announced', 'match': None}
            match, what = result['match'], (result.get('description') or '').strip()
            self.items.update(check['id'], last_answer=('yes: ' if match else 'no: ') + what)
            evaluated = True
            if match:
                state = self.hass.states.get(camera)
                text = f"{check['name']}: {what.rstrip('.') or 'seen'}"
                rt.timeline.add('watch', text, state.name)
                if check.get('say'):
                    speakers = [s for s in check.get('speakers') or [] if self.hass.states.get(s)] or None
                    await rt.speech.async_say(f"Heads up: {what.rstrip('.') or check['name']}.", 'normal', speakers,
                                              source=f"check:{check['name']}")
            return {'check': check['name'], 'match': match, 'seen': what}
        except Exception as err:
            if valid():
                self.items.update(check['id'], last_answer='unknown: check failed')
                _LOGGER.warning('Camera check %s failed: %s', check['name'], err)
                return {'error': (str(err) or type(err).__name__)[:200]}
            return {'skipped': 'camera check was invalidated'}
        finally:
            if not evaluated or force:
                getattr(rt.watches, '_prints', {}).pop(prefilter_key, None)
            self._busy.discard(check['id'])
