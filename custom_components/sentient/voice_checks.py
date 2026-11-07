"""User-started observation of real conversation turns; never simulates microphone success."""
from __future__ import annotations

from copy import deepcopy
from datetime import timedelta
import re
import uuid

from homeassistant.util import dt as dt_util
from homeassistant.helpers import intent

from .const import CONF_LIVE_VOICE_CHECK
from .sessions import is_conversation_end, spoken_text

QUESTION = 'How is the system?'
ENDING = 'No thankyou.'


class VoiceChecks:
    def __init__(self, hass, options=None):
        self.hass = hass
        self.options = options if options is not None else {}
        self.run = None
        self.generation = 0

    def _notify(self):
        self.hass.bus.async_fire('sentient_voice_check')

    def start(self, mode="conversation", question=None, ending=None, language=None):
        if not self.options.get(CONF_LIVE_VOICE_CHECK, True):
            raise ValueError("Live voice check is disabled")
        if mode not in ('conversation', 'single', 'question', 'cancel'):
            raise ValueError('Unknown voice check')
        self.generation += 1
        question = question or {'conversation': QUESTION, 'single': 'Say ready.', 'question': 'Ask me if I need anything else.', 'cancel': 'Cancel that.'}[mode]
        ending = ending or ENDING
        self.run = {'mode': mode, 'question': question, 'ending': ending, 'language': language,
                    'id': uuid.uuid4().hex, 'status': 'waiting_for_question',
                    'started': dt_util.utcnow().isoformat(),
                    'expires': (dt_util.utcnow() + timedelta(minutes=5)).isoformat(),
                    'steps': [], 'heard_replies': None,
                    'instructions': f'End any previous discussion, then start with your wake word and say “{question}”.' + (f' Then say “{ending}”.' if mode in ('conversation', 'question') else ' It should finish without listening again.'),
                    'limits': 'Observes the text and result reaching the Sentient agent. It cannot independently verify microphone capture, audibility, or the hardware stopping.'}
        self._owner = None
        self._notify()
        return self.status()

    async def async_start(self, mode='conversation'):
        from .language import async_in_house_language, async_prepare_language, selected_language

        if not self.options.get(CONF_LIVE_VOICE_CHECK, True):
            raise ValueError('Live voice check is disabled')
        generation = self.generation
        question = {'conversation': QUESTION, 'single': 'Say ready.', 'question': 'Ask me if I need anything else.', 'cancel': 'Cancel that.'}[mode]
        question = await async_in_house_language(self.hass, self.options, question, known_english=True)
        pack = await async_prepare_language(self.hass, self.options)
        ending = pack['end'][0] if pack else await async_in_house_language(self.hass, self.options, 'No thank you.')
        if generation != self.generation or not self.options.get(CONF_LIVE_VOICE_CHECK, True):
            raise ValueError('That voice check was cancelled while preparing')
        return self.start(mode, question, ending, selected_language(self.hass, self.options))

    def status(self):
        if self.run and self.run['status'].startswith('waiting') and self.run['expires'] < dt_util.utcnow().isoformat():
            self.run['status'] = 'expired'
        return deepcopy(self.run)

    def stop(self):
        self.generation += 1
        self.run = None
        self._notify()

    def confirm_heard(self, run_id, heard):
        if not self.run or self.run['id'] != run_id or self.run['status'] not in ('passed_agent_checks', 'failed_agent_checks'):
            raise ValueError('That voice check is no longer available')
        self.run['heard_replies'] = heard
        self._notify()
        return self.status()

    def observe(self, user_input, result):
        if not self.options.get(CONF_LIVE_VOICE_CHECK, True):
            return
        run = self.status()
        if not run or not run['status'].startswith('waiting'):
            return
        owner = (user_input.device_id, user_input.satellite_id, user_input.context.user_id)
        text = user_input.text.strip()
        if run['status'] == 'waiting_for_question':
            from .voice_locale import clean

            matches = clean(text) == clean(run['question'])
            if run['mode'] == 'conversation' and run['question'] == QUESTION:
                matches = matches or clean(text) in ('how is the system', "how's the system", 'check system health')
            if not matches:
                return
            self._owner = owner
            expected = run["mode"] in ("conversation", "question")
        else:
            if owner != self._owner:
                return
            from .voice_locale import clean

            if clean(text) != clean(run['ending']) and not is_conversation_end(text, self.run['steps'][-1]['reply'], language=run.get('language')):
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
