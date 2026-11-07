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
import logging
import re
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
        self.generation = 0

    def clear(self):
        self.generation += 1
        self.kept.clear()


def clear_translations(ai):
    if (cache := getattr(ai, 'speech_language_cache', None)) is not None:
        cache.clear()


def looks_english(text: str) -> bool:
    """A cheap guess, no AI: does this sentence read as English?"""
    if _CONTRACTION.search(text):
        return True
    words = [w.lower() for w in _WORD.findall(text)]
    if not words:
        return False
    hits = sum(1 for w in words if w in _ENGLISH)
    return hits >= 3 or (hits >= 1 and hits / len(words) >= 0.2)


def house_language(hass: HomeAssistant) -> str | None:
    """The house's language when it isn't English, else None."""
    lang = (hass.config.language or "en").replace("_", "-")
    return None if lang.lower().split("-")[0] == "en" else lang


async def async_in_house_language(
    hass: HomeAssistant, options: dict[str, Any], text: str, urgent: bool = False
) -> str:
    """What to actually say: *text* in the house's language, or *text* itself when that can't be done."""
    if not text or not options.get(CONF_SPEAK_HOUSE_LANGUAGE):
        return text
    if (lang := house_language(hass)) is None:
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

        return safety_sentence(text, lang) or cache.kept.get(key) or text
    if not looks_english(text):
        return text
    if (kept := cache.kept.get(key)) is not None:
        cache.kept.move_to_end(key)
        return kept
    if not owner.spend(PURPOSE):
        return text  # today's AI limit is used up: the English it is
    data: dict[str, Any] = {
        "task_name": "Sentient: speech language",
        "instructions": (
            f"A smart-home voice is about to say the sentence below out loud. Translate it into the language "
            f"with the code '{lang}', short and natural for speech. Keep the names of people, rooms and "
            "devices exactly as written. Reply with the translated sentence only, no quotes. If it is "
            "already in that language, reply with it unchanged.\n\n"
            f'Sentence: "{text}"'
        ),
    }
    if ai := options.get(CONF_AI_TASK_ENTITY):
        data["entity_id"] = ai
    try:
        response = await async_generate(hass, data, TIMEOUT)
    except AIUnavailable as err:
        _LOGGER.debug("Said in English, the AI couldn't translate it: %s", err)
        return text
    out = " ".join(str((response or {}).get("data") or "").split()).strip().strip("\"'«»“”")
    if not out or len(out) > 3 * len(text) + 40:
        return text
    if generation != cache.generation or not options.get(CONF_SPEAK_HOUSE_LANGUAGE):
        return text  # disabling/forgetting while the AI runs must not recreate deleted data
    cache.kept[key] = out
    while len(cache.kept) > KEEP:
        cache.kept.popitem(last=False)
    return out
