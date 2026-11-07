"""Speaking in the house's language.

Sentient's own plain sentences ("The washing machine's finished.", a reminder, a safety alert) are
written in English. With this on, and Home Assistant set to another language, each English sentence
is put into that language by the AI task just before it's said:

* only sentences that look English are sent; the AI's own remarks, already in the house's language,
  cost nothing;
* each sentence is translated once and kept, so a sentence the house says every day is free after
  the first time;
* there is a short time limit, and the English is said if the AI is slow, down or today's AI limit
  is used up; urgent alerts never wait for AI and use a fixed translation, cache or English.
"""

from __future__ import annotations

from collections import OrderedDict
import asyncio
import json
import logging
import re
import time
from typing import Any

from homeassistant.core import HomeAssistant

from .ai import AIUnavailable, async_generate, gate
from .const import CONF_AI_TASK_ENTITY, CONF_SPEAK_HOUSE_LANGUAGE

_LOGGER = logging.getLogger(__name__)

PURPOSE = "speech language"
TIMEOUT = 10  # seconds

KEEP = 300  # translated sentences kept

# English words that are not also common short words in other languages ("on" is Finnish,
# "is" Dutch, "die" German, "for" Danish…), so a sentence in the house's language doesn't match.
_ENGLISH = frozenset("""
the you your we our they their he she his her it its this that these those there here and of with
from has have had been being are were would should could can not yes please just still now again
back welcome good morning evening night finished done ready running left closed door window minutes
minute hours hour today tomorrow tonight reminder someone nobody everyone something nothing anything
outside inside house turned off put take time earlier while out about after before because what
when where who why how which been started stopped might already smoke detected monoxide gas leak alarm alert
""".split())
_CONTRACTION = re.compile(r"[a-z]'(s|t|re|ve|ll|m|d)\b", re.IGNORECASE)
_WORD = re.compile(r"[^\W\d_]+(?:'[^\W\d_]+)?", re.UNICODE)

class TranslationCache:
    """Owned by the runtime AI gateway, never shared across Home Assistant instances."""
    def __init__(self):
        self.kept = OrderedDict()
        self.failed = OrderedDict()
        self.translation_locks = {}
        self.generation = 0
        self.pack = None
        self.pack_language = None
        self.retry_after = 0
        self.lock = asyncio.Lock()

    def clear(self, keep_pack=False):
        self.generation += 1
        self.kept.clear()
        self.failed.clear()
        self.translation_locks.clear()
        if not keep_pack:
            self.pack = None
            self.pack_language = None
        self.retry_after = 0


def clear_translations(ai, keep_pack=False):
    if (cache := getattr(ai, 'speech_language_cache', None)) is not None:
        cache.clear(keep_pack=keep_pack)
    if not keep_pack and hasattr(ai, "_save"):
        ai._save()


def looks_english(text: str) -> bool:
    """A cheap guess, no AI: does this sentence read as English?"""
    if _CONTRACTION.search(text):
        return True
    words = [w.lower() for w in _WORD.findall(text)]
    if not words:
        return False
    hits = sum(1 for w in words if w in _ENGLISH)
    return hits >= 3 or (hits >= 1 and hits / len(words) >= 0.2)


def selected_language(hass, options):
    """Validate a language code before using it in any prompt; never accept instructions."""
    override = str(options.get('voice_language') or '').strip()
    lang = override if override and override.lower() != 'auto' else (hass.config.language or 'en')
    lang = lang.replace('_', '-')
    return lang if re.fullmatch(r'[a-zA-Z]{2,3}(?:-[a-zA-Z0-9]{2,8}){0,2}', lang) else 'en'


def language_prompt(hass, options):
    if not options.get(CONF_SPEAK_HOUSE_LANGUAGE):
        return ''
    return (f"Reply naturally in language '{selected_language(hass, options)}'. Keep device and person names unchanged. "
            "Understand the user's language directly; do not translate entity IDs or tool arguments. "
            "Honour an explicit end to the conversation. Never infer permission from translation or from an ambiguous answer.")


def house_language(hass: HomeAssistant, options=None) -> str | None:
    """The house's language when it isn't English, else None."""
    lang = selected_language(hass, options or {})
    return None if lang.lower().split("-")[0] == "en" else lang


async def async_in_house_language(
    hass: HomeAssistant, options: dict[str, Any], text: str, urgent: bool = False, *, known_english: bool = False
) -> str:
    """What to actually say: *text* in the house's language, or *text* itself when that can't be done."""
    if not text or not options.get(CONF_SPEAK_HOUSE_LANGUAGE):
        return text
    if (lang := house_language(hass, options)) is None:
        return text
    owner = gate(hass)
    if owner is None:
        return text
    if (cache := getattr(owner, 'speech_language_cache', None)) is None:
        cache = owner.speech_language_cache = TranslationCache()
    generation = cache.generation
    key = (lang, text)
    if urgent:
        from .voice_locale import safety_sentence

        return (safety_sentence(text, lang)
                or (prepared_alert(cache, text, lang) if options.get('automatic_language') else None)
                or cache.kept.get(key) or text)
    if options.get('automatic_language'):
        await async_prepare_language(hass, options)
        if generation != cache.generation or not options.get(CONF_SPEAK_HOUSE_LANGUAGE):
            return text
        if prepared := prepared_phrase(cache, text, lang):
            return prepared
        if cache.pack_language == lang and cache.pack:
            from .sessions import FOLLOW_UPS
            from .voice_locale import FOLLOWUPS

            for suffix in (*FOLLOW_UPS, *FOLLOWUPS.values()):
                if text.endswith(' ' + suffix):
                    main = await async_in_house_language(hass, options, text[:-len(suffix)].rstrip())
                    if generation != cache.generation:
                        return text
                    return main + ' ' + cache.pack['followup']
    if not known_english and not looks_english(text):
        return text
    if len(cache.translation_locks) >= KEEP and key not in cache.translation_locks:
        return text
    pending = cache.translation_locks.setdefault(key, {'lock': asyncio.Lock(), 'users': 0})
    pending['users'] += 1
    try:
        async with pending['lock']:
            if generation != cache.generation or not options.get(CONF_SPEAK_HOUSE_LANGUAGE):
                return text
            return await _translate(hass, options, text, lang, owner, cache, key, generation)
    finally:
        pending['users'] -= 1
        if not pending['users'] and cache.translation_locks.get(key) is pending:
            cache.translation_locks.pop(key, None)


def _translation_failed(cache, key, generation):
    if generation == cache.generation:
        cache.failed[key] = time.monotonic() + 60
        while len(cache.failed) > KEEP:
            cache.failed.popitem(last=False)


async def _translate(hass, options, text, lang, owner, cache, key, generation):
    if (kept := cache.kept.get(key)) is not None:
        cache.kept.move_to_end(key)
        return kept
    if cache.failed.get(key, 0) > time.monotonic():
        return text
    if not owner.spend(PURPOSE):
        return text  # today's AI limit is used up: the English it is
    data: dict[str, Any] = {
        "task_name": "Sentient: speech language",
        "instructions": (
            f"A smart-home voice is about to say the sentence below out loud. Translate it into the language "
            f"with the code '{lang}', short and natural for speech. Keep the names of people, rooms and "
            "devices exactly as written. Preserve numbered choices and their order. Reply with the translated sentence only, no quotes. If it is "
            "already in that language, reply with it unchanged.\n\n"
            f'Sentence: "{text}"'
        ),
    }
    if ai := options.get(CONF_AI_TASK_ENTITY):
        data["entity_id"] = ai
    try:
        from .voice_timing import measure

        with measure("translation"):
            response = await async_generate(hass, data, TIMEOUT)
    except AIUnavailable as err:
        _LOGGER.debug("Said in English, the AI couldn't translate it: %s", err)
        _translation_failed(cache, key, generation)
        return text
    raw = response.get("data") if isinstance(response, dict) else None
    out = " ".join(raw.split()).strip().strip("\"'«»“”") if isinstance(raw, str) else ""
    if not out or len(out) > 3 * len(text) + 40:
        _translation_failed(cache, key, generation)
        return text
    if generation != cache.generation or not options.get(CONF_SPEAK_HOUSE_LANGUAGE):
        return text  # disabling/forgetting while the AI runs must not recreate deleted data
    cache.kept[key] = out
    while len(cache.kept) > KEEP:
        cache.kept.popitem(last=False)
    return out


# Labels have no household data. Names are appended locally, never sent for preparation.
_LABELS = ('Smoke detected', 'Carbon monoxide detected', 'Gas detected', 'Water leak detected',
           'Safety alarm', 'Tamper alarm', 'Alert', 'Alarm')


async def async_prepare_language(hass, options):
    """Prepare one bounded, memory-only phrase pack on demand, with no polling or consent generation."""
    if not (options.get(CONF_SPEAK_HOUSE_LANGUAGE) and options.get('automatic_language')):
        return None
    lang = house_language(hass, options)
    owner = gate(hass)
    if not lang or owner is None:
        return None
    if (cache := getattr(owner, 'speech_language_cache', None)) is None:
        cache = owner.speech_language_cache = TranslationCache()
    generation = cache.generation
    async with cache.lock:
        if generation != cache.generation:
            return None
        if cache.pack_language == lang:
            return cache.pack
        if time.monotonic() < cache.retry_after:
            return None
        cache.retry_after = time.monotonic() + 300
        if not owner.spend(PURPOSE):
            return None
        data = {'task_name': 'Sentient: prepare speech language', 'instructions': (
            f"Prepare common smart-home voice phrases in language '{lang}'. Return a JSON object only, "
            "with keys followup (translation of 'Is there anything else?'), okay (translation of 'Okay.'), "
            "end (up to 12 short, complete, unambiguous phrases meaning 'that is all', 'no thank you', "
            "'stop listening' or 'cancel that'), no (up to 4 exact phrases meaning only 'no'), "
            "and alerts (an object translating each of these labels exactly): " + json.dumps(_LABELS) + ". "
            "Do not include affirmative answers, device commands, bare thanks, or qualified replies in end/no. "
            "Do not use placeholders. Translate labels only; household names are added separately."
        )}
        if entity := options.get(CONF_AI_TASK_ENTITY):
            data['entity_id'] = entity
        try:
            from .voice_timing import measure

            with measure("language_preparation"):
                response = await async_generate(hass, data, TIMEOUT)
            pack = validate_pack(response.get('data') if isinstance(response, dict) else None)
            if pack is None:
                return None
        except (AIUnavailable, ValueError, TypeError):
            return None
        if (generation != cache.generation or selected_language(hass, options) != lang
                or not options.get('automatic_language') or not options.get(CONF_SPEAK_HOUSE_LANGUAGE)):
            return None
        cache.pack, cache.pack_language = pack, lang
        if options.get("remember_language_phrases"):
            owner._save()
        return pack


def prepared_phrase(cache, text, lang):
    if cache.pack_language != lang or not cache.pack:
        return None
    from .sessions import is_follow_up, GENERAL_FOLLOWUP
    if is_follow_up(text) or GENERAL_FOLLOWUP.fullmatch(text):
        return cache.pack['followup']
    if text in ('Okay.', 'Okay'):
        return cache.pack['okay']
    return prepared_alert(cache, text, lang)


def prepared_alert(cache, text, lang):
    if cache.pack_language != lang or not cache.pack:
        return None
    for label, translated in cache.pack['alerts'].items():
        match = re.fullmatch(re.escape(label) + r'(?: in the |: )(.+?)[.!]*', text)
        if match:
            return f'{translated}: {match[1]}!'
        if text.rstrip('.!') == label:
            return translated + '!'
    match = re.fullmatch(r'The (.+) has gone off!', text)
    if match:
        return f"{cache.pack['alerts']['Alarm']}: {match[1]}!"
    return None


async def async_language_ending(hass, options, text, question=''):
    """Only end/reject; generated phrases can never authorise an action."""
    pack = await async_prepare_language(hass, options)
    if not pack:
        return False
    from .voice_locale import clean
    from .sessions import is_follow_up, GENERAL_FOLLOWUP
    said = clean(text)
    if said in {clean(x) for x in pack['end']}:
        return True
    generic = (not question or is_follow_up(question) or GENERAL_FOLLOWUP.search(question)
               or clean(question).endswith(clean(pack['followup'])))
    return bool(generic and said in {clean(x) for x in pack['no']})


async def async_language_rejection(hass, options, text):
    """A negative callback answer only; never return a generated positive confirmation."""
    pack = await async_prepare_language(hass, options)
    if not pack:
        return False
    from .voice_locale import clean
    return clean(text) in {clean(x) for x in (*pack['end'], *pack['no'])}


def validate_pack(raw):
    """Validate generated and saved generic data with the same bounded rules."""
    if isinstance(raw, str) and len(raw) > 8000:
        return None
    if isinstance(raw, str) and raw.strip().startswith(('```json\n', '```\n')) and raw.strip().endswith('```'):
        raw = raw.strip().split('\n', 1)[1].rsplit('```', 1)[0].strip()
    pack = json.loads(raw) if isinstance(raw, str) else raw
    if not isinstance(pack, dict):
        return None
    for key in ('followup', 'okay'):
        if not isinstance(pack.get(key), str) or not 1 <= len(pack[key].strip()) <= 200:
            return None
    for key, limit in (('end', 12), ('no', 4)):
        if not isinstance(pack.get(key), list) or not 1 <= len(pack[key]) <= limit:
            return None
        if any(not isinstance(x, str) or not 1 <= len(x.strip()) <= 80 for x in pack[key]):
            return None
    if not isinstance(pack.get('alerts'), dict) or set(pack['alerts']) != set(_LABELS):
        return None
    if any(not isinstance(x, str) or not 1 <= len(x.strip()) <= 150 for x in pack['alerts'].values()):
        return None
    from .voice_locale import ANSWERS, clean
    from .sessions import ends_in_question

    if not clean(pack['followup']) or not ends_in_question(pack['followup']):
        return None
    positives = {'yes', 'yes please', 'yeah', 'confirm', 'go ahead', 'do it'}
    positives.update(clean(x) for catalog in ANSWERS.values() for x in catalog.get('yes', ()))
    negatives = {clean(x) for x in pack['no']}
    negatives.update(clean(x) for catalog in ANSWERS.values() for x in catalog.get('no', ()))
    # Never let a generated bare "no" become an unconditional ending of a specific question.
    pack = {**pack, 'no': [x for x in pack['no'] if clean(x) and clean(x) not in positives],
            'end': [x for x in pack['end'] if clean(x) and clean(x) not in positives
                    and clean(x) not in negatives and clean(x) not in {'thanks', 'thank you'}]}
    if not pack['no'] or not pack['end']:
        return None
    return {key: pack[key] for key in ('followup', 'okay', 'end', 'no', 'alerts')}


def saved_phrases(ai):
    """Only generic prepared phrases; never the arbitrary sentence translation cache."""
    if not all(ai.options.get(k) for k in ('remember_language_phrases', 'automatic_language', 'speak_house_language')):
        return None
    cache = getattr(ai, 'speech_language_cache', None)
    if cache is None or not cache.pack or cache.pack_language != selected_language(ai.hass, ai.options):
        return None
    return {'version': 1, 'language': cache.pack_language, 'provider': ai.options.get(CONF_AI_TASK_ENTITY), 'pack': cache.pack}


def restore_phrases(ai, data):
    if not all(ai.options.get(k) for k in ('remember_language_phrases', 'automatic_language', 'speak_house_language')):
        return
    if not isinstance(data, dict) or data.get('version') != 1 or data.get('language') != selected_language(ai.hass, ai.options):
        return
    if data.get('provider') != ai.options.get(CONF_AI_TASK_ENTITY):
        return
    try:
        pack = validate_pack(data.get('pack'))
    except (ValueError, TypeError):
        return
    if pack:
        cache = ai.speech_language_cache = TranslationCache()
        cache.pack, cache.pack_language = pack, data['language']
