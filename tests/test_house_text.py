"""Sentient's own sentences in the house's language: the catalogue, the matcher and where it's used."""

import json
from pathlib import Path
import re

import pytest
from homeassistant.core import HomeAssistant

from custom_components.sentient import language, phrases
from custom_components.sentient.const import DOMAIN
from tests.test_language import _setup

DIR = Path(phrases.DIR)
EN = json.loads((DIR / "en.json").read_text(encoding="utf-8"))
LANGS = sorted(p.stem for p in DIR.glob("*.json") if p.stem != "en")
SLOTS = re.compile(r"\{[a-z]+\}")


def test_catalogue_is_a_sorted_list_of_unique_templates() -> None:
    assert isinstance(EN, list) and len(EN) > 500
    assert len(set(EN)) == len(EN)
    assert sorted(EN) == EN


@pytest.mark.parametrize("lang", LANGS)
def test_every_language_has_every_template_with_its_slots(lang: str) -> None:
    tr = json.loads((DIR / f"{lang}.json").read_text(encoding="utf-8"))
    assert set(tr) == set(EN)
    for en, text in tr.items():
        assert isinstance(text, str) and text.strip(), en
        # a translation may drop a plural ending's slot, but never invent one
        assert set(SLOTS.findall(text)) <= set(SLOTS.findall(en)), en


def test_all_languages_are_there() -> None:
    assert LANGS == [
        "ar", "bn", "cs", "da", "de", "el", "es", "fi",
        "fr", "he", "hi", "hu", "id", "it", "ja", "ko",
        "nb", "nl", "pl", "pt", "ro", "ru", "sv", "tr",
        "uk", "zh", "zh-hant",
    ]


D = phrases.compile_dictionary({
    "The washing machine's finished.": "Pralka skończyła.",
    "{a} is offline": "{a} jest offline",
    "The AI": "AI",
    "Kitchen": "Kuchnia",
    "{a} thing{b} to look at": "{a} rzeczy do sprawdzenia",
    "Running now: ": "Teraz działa: ",
    " (someone was up)": " (ktoś nie spał)",
    "It's down to {a} degrees in {b}": "Spadło do {a} stopni w {b}",
    " — it's clear now": " — już czysto",
    "Awake": "Czuwa",
})


@pytest.mark.parametrize(("text", "want"), [
    ("The washing machine's finished.", "Pralka skończyła."),
    ("THE WASHING MACHINE'S FINISHED.", "PRALKA SKOŃCZYŁA."),
    ("The AI is offline", "AI jest offline"),
    ("1 thing to look at", "1 rzeczy do sprawdzenia"),
    ("3 things to look at", "3 rzeczy do sprawdzenia"),
    ("Running now: The washing machine's finished.", "Teraz działa: Pralka skończyła."),
    ("Running now: something new", "Teraz działa: something new"),
    ("Lights on (someone was up)", "Lights on (ktoś nie spał)"),
    # the ending stays an ending rather than vanishing into the last slot
    ("It's down to 2 degrees in Kitchen — it's clear now", "Spadło do 2 stopni w Kuchnia — już czysto"),
    ("Awake · The AI is offline · Else", "Czuwa · AI jest offline · Else"),
    ("Awake · The AI is offline", "Czuwa · AI jest offline"),
    ("Not one of ours", None),
])
def test_matcher(text: str, want: str | None) -> None:
    assert phrases._one(text, D) == want


def test_translate_keeps_unknown_text_and_spacing(monkeypatch) -> None:
    monkeypatch.setitem(phrases._loaded, "xx", D)
    assert phrases.translate("  The AI is offline ", "xx") == "  AI jest offline "
    assert phrases.translate("Something else", "xx") == "Something else"
    assert phrases.translate("42", "xx") == "42"
    assert phrases.translate("The AI is offline", "en") == "The AI is offline"
    assert phrases.translate("The AI is offline", "zz") == "The AI is offline"


def test_real_polish_file() -> None:
    assert phrases.load("pl-PL") is not None
    assert phrases.translate("Cancelled. No action was taken.", "pl-pl") == "Anulowano. Nic nie zostało zrobione."
    assert phrases.translate("Lights on (someone was up)", "pl-pl") == "Lights on (ktoś nie spał)"
    assert phrases.load("en") is None and phrases.load("zz") is None


async def test_written_and_spoken_language_follow_the_settings(hass: HomeAssistant) -> None:
    await _setup(hass, house_language="pl", speak_house_language=False)
    rt = hass.config_entries.async_entries(DOMAIN)[0].runtime_data
    assert language.written_language(hass, rt.options) == "pl"
    assert language.spoken_language(hass, rt.options) is None
    assert "Polish" in language.write_in_line(hass, rt.options, spoken=False)
    assert language.write_in_line(hass, rt.options, spoken=True) == ""
    off = {**rt.options, "house_language_text": False}
    assert language.written_language(hass, off) is None
    assert language.write_in_line(hass, off, spoken=False) == ""


async def test_english_house_writes_nothing_extra(hass: HomeAssistant) -> None:
    await _setup(hass, house_language="en")
    rt = hass.config_entries.async_entries(DOMAIN)[0].runtime_data
    assert language.written_language(hass, rt.options) is None
    assert language.write_in(hass, rt.options, spoken=False) == "British English"
    assert language.write_in_line(hass, rt.options, spoken=False) == ""


async def test_notifications_use_the_catalogue(hass: HomeAssistant) -> None:
    await _setup(hass, house_language="pl")
    rt = hass.config_entries.async_entries(DOMAIN)[0].runtime_data
    got = await phrases.async_written(hass, rt.options, "Cancelled. No action was taken.\nSomething new")
    assert got == "Anulowano. Nic nie zostało zrobione.\nSomething new"
    off = {**rt.options, "house_language_text": False}
    assert await phrases.async_written(hass, off, "Cancelled. No action was taken.") == "Cancelled. No action was taken."


async def test_speech_uses_the_catalogue_without_ai(hass: HomeAssistant) -> None:
    calls = await _setup(hass, house_language="pl")
    rt = hass.config_entries.async_entries(DOMAIN)[0].runtime_data
    said = await language.async_in_house_language(hass, rt.options, "Cancelled. No action was taken.")
    assert said == "Anulowano. Nic nie zostało zrobione."
    assert not calls


async def test_mind_sensor_tells_the_page(hass: HomeAssistant) -> None:
    await _setup(hass, house_language="pl", house_language_text=False)
    states = [s for s in hass.states.async_all("sensor") if "house_language_text" in s.attributes]
    assert states and states[0].attributes["house_language_text"] is False


async def test_voice_reply_in_the_conversation_language(hass: HomeAssistant) -> None:
    from dataclasses import replace

    from tests.test_voice_sessions_control import agent, request, response

    obj, _ = agent(hass, {})
    polish = replace(request("anuluj"), language="pl")
    out = await obj._localise_result(response("Cancelled. No action was taken."), polish)
    assert out.response.speech["plain"]["speech"] == "Anulowano. Nic nie zostało zrobione."
    out = await obj._localise_result(response("Cancelled. No action was taken."), request("cancel"))
    assert out.response.speech["plain"]["speech"] == "Cancelled. No action was taken."
    obj, _ = agent(hass, {"house_language_text": False})
    out = await obj._localise_result(response("Cancelled. No action was taken."), polish)
    assert out.response.speech["plain"]["speech"] == "Cancelled. No action was taken."


async def test_safety_alert_wording_comes_before_general_sentences(hass: HomeAssistant) -> None:
    calls = await _setup(hass, house_language="fi")
    rt = hass.config_entries.async_entries(DOMAIN)[0].runtime_data
    await phrases.async_load(hass, "fi")
    said = await language.async_in_house_language(hass, rt.options, "Smoke in the kitchen!", urgent=True)
    assert said == "Savua havaittu: kitchen!"
    assert not calls
