"""Numbers said as words for voices in other languages (#226), and the Alexa agent chosen in Assist."""

import asyncio
from dataclasses import replace
from types import SimpleNamespace
from unittest.mock import AsyncMock, patch

import pytest
from homeassistant.core import HomeAssistant

from custom_components.sentient import numbers
from custom_components.sentient.language import numbers_prompt
from custom_components.sentient.pronounce import Pronouncer, StreamPronouncer
from tests.test_search_alexa import _alexa_agent, _input, _result, _setup


@pytest.mark.parametrize(("lang", "text", "want"), [
    ("pl", "4,4 stopnia celsjusza", "cztery przecinek cztery stopnia celsjusza"),
    ("pl", "Jutro 3°C, deszcz 40%.", "Jutro trzy stopnie, deszcz czterdzieści procent."),
    ("pl", "1°C, 22°C, 25°", "jeden stopień, dwadzieścia dwa stopnie, dwadzieścia pięć stopni"),
    ("pl", "W nocy -3 stopnie", "W nocy minus trzy stopnie"),
    ("pl", "Budzik na 6:05, spotkanie o 14:30. Jest 20:00.", "Budzik na szóstą zero pięć, spotkanie o czternastej trzydzieści. Jest dwudziesta."),
    ("pl", "Między 5-10 minut", "Między pięć do dziesięć minut"),
    ("pl", "Rachunek 1 234,50 zł albo 1.500 kroków", "Rachunek tysiąc dwieście trzydzieści cztery złote i pięćdziesiąt groszy albo tysiąc pięćset kroków"),
    ("pl", "Data 2026-11-25", "Data dwudziesty piąty listopada dwa tysiące dwudziestego szóstego roku"),
    ("pl", "Data 3.12.2026", "Data trzeci grudnia dwa tysiące dwudziestego szóstego roku"),
    ("pl", "Teraz −2,5°C. Potem 4.2°C.", "Teraz minus dwa przecinek pięć stopnia. Potem cztery przecinek dwa stopnia."),
    ("ru", "Завтра 21°C", "Завтра двадцать один градус"),
    ("cs", "Vlhkost 45%", "Vlhkost čtyřicet pět procent"),
    ("de", "Morgen 7,5 Grad", "Morgen sieben Komma fünf Grad"),
    ("pt-BR", "Faz 21°C", "Faz vinte e um graus"),
    ("tr", "Sıcaklık 4,4 derece", "Sıcaklık dört virgül dört derece"),
])
def test_numbers_become_words(lang: str, text: str, want: str) -> None:
    assert numbers.words(text, lang) == want


@pytest.mark.parametrize("text", ["Wersja v1.2", "czujnik sensor_2", "WD-40", "data 2026-02-31", "mp3", "pokój #3"])
def test_codes_names_and_invalid_dates_are_left_alone(text: str) -> None:
    assert numbers.words(text, "pl") == text


@pytest.mark.parametrize("lang", ["en", "en-GB", "ja", "zh-Hans", "ko", None, "xx"])
def test_languages_whose_voices_read_digits_are_left_alone(lang) -> None:
    assert numbers.converter(lang) is None
    assert numbers.words("It is 4.4 degrees at 14:30", lang) == "It is 4.4 degrees at 14:30"


def _pronouncer(**options) -> Pronouncer:
    return Pronouncer(lambda: {"pronunciations": "Siobhan = Shivawn", **options}, lambda: [], lambda: "pl")


def test_pronouncer_says_numbers_in_the_reply_language() -> None:
    p = _pronouncer()
    assert p("Siobhan, 3°C") == "Shivawn, trzy stopnie"  # announcements: the house's spoken language
    assert p.speaking("de")("Siobhan, 3°C") == "Shivawn, drei Grad"
    typed = p.speaking("pl", names=False)
    assert typed("Siobhan, 3°C") == "Siobhan, trzy stopnie" and typed.active()
    assert not p.speaking("en", names=False).active()


def test_switch_off_leaves_digits() -> None:
    p = _pronouncer(say_numbers_as_words=False)
    assert p.speaking("pl")("Siobhan, 3°C") == "Shivawn, 3°C"
    assert not p.speaking("pl", names=False).active()
    assert numbers_prompt({"say_numbers_as_words": False}, "pl") == ""


def test_a_number_split_across_streamed_pieces_is_said_whole() -> None:
    say = _pronouncer().speaking("pl", names=False)
    stream = StreamPronouncer(say, say.table, say.numbers)
    out = stream.feed("Teraz 4,") + stream.feed("4°C i ") + stream.feed("wiatr 15") + stream.feed(" km/h.") + stream.flush()
    assert out == "Teraz cztery przecinek cztery stopnia i wiatr piętnaście km/h."


def test_the_ai_is_asked_for_words_only_where_it_helps() -> None:
    assert "Polish" in numbers_prompt({}, "pl") and "as words" in numbers_prompt({}, "pl")
    assert numbers_prompt({}, "en") == "" and numbers_prompt({}, "ja") == ""


def _assist():
    """The chat log an Assist pipeline runs the agent inside: it always listens to the reply as it's written."""
    from homeassistant.components.conversation.chat_log import current_chat_log

    return current_chat_log.set(SimpleNamespace(delta_listener=lambda *a: None))


async def test_alexa_agent_chosen_in_assist_waits_for_the_answer(hass: HomeAssistant) -> None:
    from homeassistant.components.conversation.chat_log import current_chat_log

    await _setup(hass, alexa_skill=False, alexa_wait=1, voice_agent="conversation.ai")
    agent = await _alexa_agent(hass)

    async def slow(*_):
        await asyncio.sleep(0.2)
        return _result("Jutro cztery stopnie.")

    token = _assist()
    try:
        with patch.object(agent, "_converse", side_effect=slow), \
             patch("custom_components.sentient.conversation.DEFAULT_ALEXA_WAIT", 0.01):
            agent._entry.runtime_data.options = {**agent._entry.runtime_data.options, "alexa_wait": 0.01}
            result = await agent.async_process(replace(_input("a jutro jaka będzie?"), language="pl"))
    finally:
        current_chat_log.reset(token)
    assert result.response.speech["plain"]["speech"] == "Jutro cztery stopnie."


async def test_alexa_replies_are_in_the_conversation_language(hass: HomeAssistant) -> None:
    await _setup(hass, voice_agent="conversation.ai")
    agent = await _alexa_agent(hass)
    with patch.object(agent, "_converse", AsyncMock(return_value=_result("x"))):
        result = await agent.async_process(replace(_input("jak tam dom"), language="pl"))
    assert result.response.speech["plain"]["speech"] == "Pytanie przez Alexę jest wyłączone w ustawieniach Sentient."
