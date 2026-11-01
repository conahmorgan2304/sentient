"""User-started observation of real conversation turns; never simulates microphone success."""
from __future__ import annotations

from copy import deepcopy
from datetime import timedelta
import re
import uuid

from homeassistant.util import dt as dt_util
from homeassistant.helpers import intent

from .sessions import is_conversation_end, spoken_text

QUESTION = 'How is the system?'
ENDING = 'No thankyou.'


class VoiceChecks:
    def __init__(self, hass):
        self.hass = hass
        self.run = None

    def _notify(self):
        self.hass.bus.async_fire('sentient_voice_check')

    def start(self):
        self.run = {'id': uuid.uuid4().hex, 'status': 'waiting_for_question',
                    'started': dt_util.utcnow().isoformat(),
                    'expires': (dt_util.utcnow() + timedelta(minutes=5)).isoformat(),
                    'steps': [], 'heard_replies': None,
                    'instructions': f'Use your voice device: ask “{QUESTION}”, then say “{ENDING}”.',
                    'limits': 'Observes the text and result reaching the Sentient agent. It cannot independently verify microphone capture, audibility, or the hardware stopping.'}
        self._owner = None
        self._notify()
        return self.status()

    def status(self):
        if self.run and self.run['status'].startswith('waiting') and self.run['expires'] < dt_util.utcnow().isoformat():
            self.run['status'] = 'expired'
        return deepcopy(self.run)

    def stop(self):
        self.run = None
        self._notify()

    def confirm_heard(self, run_id, heard):
        if not self.run or self.run['id'] != run_id or self.run['status'] not in ('passed_agent_checks', 'failed_agent_checks'):
            raise ValueError('That voice check is no longer available')
        self.run['heard_replies'] = heard
        self._notify()
        return self.status()

    def observe(self, user_input, result):
        run = self.status()
        if not run or not run['status'].startswith('waiting'):
            return
        owner = (user_input.device_id, user_input.satellite_id, user_input.context.user_id)
        text = user_input.text.strip()
        if run['status'] == 'waiting_for_question':
            clean = re.sub(r"[^a-z' ]", '', text.lower()).strip()
            if clean not in ('how is the system', "how's the system", 'check system health'):
                return
            self._owner = owner
            expected = True
        else:
            if owner != self._owner:
                return
            if not is_conversation_end(text, self.run['steps'][-1]['reply']):
                return
            expected = False
        step = {'time': dt_util.utcnow().isoformat(), 'input_text': text[:300],
                'reply': spoken_text(result.response)[:500], 'agent': user_input.agent_id,
                'device_id': user_input.device_id, 'satellite_id': user_input.satellite_id,
                'language': user_input.language, 'continue_listening': result.continue_conversation,
                'expected_listening': expected,
                'passed': bool(spoken_text(result.response)) and result.continue_conversation == expected and result.response.response_type != intent.IntentResponseType.ERROR}
        self.run['steps'].append(step)
        if expected:
            self.run['status'] = 'waiting_for_ending'
        else:
            self.run['status'] = 'passed_agent_checks' if all(s['passed'] for s in self.run['steps']) else 'failed_agent_checks'
        self._notify()
