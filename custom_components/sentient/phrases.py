"""Sentient's own fixed sentences in the house's language.

Sentient writes its sentences in English ("The washing machine's finished.", "Didn't run: Wake-up").
frontend/i18n/house/en.json lists them as templates, with {a}, {b}… where the code puts a name or a
number, and frontend/i18n/house/<language>.json gives each one in that language. The same files are
used by the page (sentient-i18n.js, for the timeline, House health and the Mind tab, in the viewer's
language) and here, for what's said, sent to phones and replied to the voice. Nothing is stored and no
AI is used: a sentence that isn't in the list stays in English, and the AI translation (language.py)
can still take it if that's switched on.

How a sentence is matched, first hit wins (the same as the page):
  1. as it is, or in capitals
  2. a template with {slots}; what fills a slot is itself translated if it can be ("{a} is offline"
     with a = "The AI")
  3. a known opening ("Running now: ", "A thought from overnight: ") and the rest on its own
  4. a known ending (" — it's clear now", " (someone was up)") and the rest on its own
  5. split at " · " and each part on its own
"""

from __future__ import annotations

from dataclasses import dataclass, field
import json
import logging
from pathlib import Path
import re

_LOGGER = logging.getLogger(__name__)

DIR = Path(__file__).parent / "frontend" / "i18n" / "house"
_SLOT = re.compile(r"(\{[a-z]+\})")
# a template that starts with one of these is an ending; one that ends with one of these is an opening
_ENDING_START = (" ", ",", ";", ".", ":", "!", "?", "—", "(")
_OPENING_END = (" ", ":", "—")


@dataclass
class Dictionary:
    exact: dict[str, str] = field(default_factory=dict)
    upper: dict[str, str] = field(default_factory=dict)
    slots: list[tuple[re.Pattern, re.Pattern, list[str], str]] = field(default_factory=list)
    openings: list[tuple[re.Pattern, list[str], str]] = field(default_factory=list)
    endings: list[tuple[re.Pattern, list[str], str]] = field(default_factory=list)


def _pattern(template: str, caps: bool, anchor: str) -> tuple[re.Pattern, list[str]]:
    parts = _SLOT.split(template)
    names: list[str] = []
    body = ""
    for i, part in enumerate(parts):
        if _SLOT.fullmatch(part):
            names.append(part[1:-1])
            # a slot straight after a letter is a word ending ("thing{b}" for "s"), which may be empty
            before = parts[i - 1] if i else ""
            body += "(.*?)" if before[-1:].isalpha() else "(.+?)"
        else:
            body += re.escape(part.upper() if caps else part)
    if anchor == "whole":
        rx = "^" + body + "$"
    elif anchor == "start":
        rx = "^" + body
    else:
        rx = body + "$"
    return re.compile(rx, re.S), names


def compile_dictionary(raw: dict[str, str]) -> Dictionary:
    d = Dictionary()
    for en, tr in raw.items():
        if not isinstance(tr, str) or not tr:
            continue
        d.exact[en] = tr
        d.upper.setdefault(en.upper(), tr)
        has_slot = bool(_SLOT.search(en))
        if en[:1] in _ENDING_START or en[:2] == " —":
            rx, names = _pattern(en, False, "end")
            d.endings.append((rx, names, tr))
        elif en[-1:] in _OPENING_END:
            rx, names = _pattern(en, False, "start")
            d.openings.append((rx, names, tr))
        elif has_slot:
            rx, names = _pattern(en, False, "whole")
            up, _ = _pattern(en, True, "whole")
            d.slots.append((rx, up, names, tr))
    # most literal text first, so "{a} is offline" is tried before "{a} {b}"
    literal = lambda t: len(_SLOT.sub("", t))  # noqa: E731
    d.slots.sort(key=lambda s: -literal(s[0].pattern))
    d.openings.sort(key=lambda s: -len(s[0].pattern))
    d.endings.sort(key=lambda s: -len(s[0].pattern))
    return d


def _caps(text: str) -> bool:
    return any(c.isalpha() for c in text) and text == text.upper()


def _fill(tr: str, names: list[str], values: tuple[str, ...], d: Dictionary, caps: bool) -> str:
    out = tr.upper() if caps else tr
    for name, got in zip(names, values):
        inner = _one(got, d, split=False, depth=1) if got and any(c.isalpha() for c in got) else None
        value = inner if inner is not None else got
        for token in ("{" + name + "}", "{" + name.upper() + "}"):
            out = out.replace(token, value)
    return out


def _one(text: str, d: Dictionary, split: bool = True, depth: int = 0) -> str | None:
    if depth > 4 or not text:
        return None
    if text in d.exact:
        return d.exact[text]
    caps = _caps(text)
    if caps and text in d.upper:
        return d.upper[text].upper()
    for rx, up, names, tr in d.slots:
        m = (up if caps else rx).match(text)
        if m:
            return _fill(tr, names, m.groups(), d, caps)
    for rx, names, tr in d.openings:
        m = rx.match(text)
        if m and m.end() < len(text):
            rest = text[m.end():]
            return _fill(tr, names, m.groups(), d, False) + (_one(rest, d, depth=depth + 1) or rest)
    for rx, names, tr in d.endings:
        m = rx.search(text)
        if m and m.start() > 0:
            head = text[:m.start()]
            return (_one(head, d, depth=depth + 1) or head) + _fill(tr, names, m.groups(), d, False)
    if split and " · " in text:
        parts = [p for p in text.split(" · ")]
        done = [_one(p.strip(), d, split=False, depth=depth + 1) for p in parts]
        if any(x is not None for x in done):
            return " · ".join(x if x is not None else p for x, p in zip(done, parts))
    return None


_loaded: dict[str, Dictionary | None] = {}


def _file_for(lang: str) -> Path | None:
    lang = lang.lower().replace("_", "-")
    for name in (lang, lang.split("-")[0]):
        if re.fullmatch(r"[a-z]{2,3}(-[a-z0-9]{2,8})*", name) and (DIR / f"{name}.json").is_file():
            return DIR / f"{name}.json"
    return None


def load(lang: str | None) -> Dictionary | None:
    """Read a language's file (call from an executor the first time); None for English or no file."""
    if not lang or lang.lower().split("-")[0] == "en":
        return None
    key = lang.lower()
    if key not in _loaded:
        path = _file_for(key)
        try:
            _loaded[key] = compile_dictionary(json.loads(path.read_text(encoding="utf-8"))) if path else None
        except (OSError, ValueError) as err:
            _LOGGER.warning("Sentient couldn't read %s: %s", path, err)
            _loaded[key] = None
    return _loaded[key]


def loaded(lang: str | None) -> Dictionary | None:
    """Only what's already been read: safe in the event loop."""
    if not lang:
        return None
    return _loaded.get(lang.lower())


async def async_load(hass, lang: str | None) -> Dictionary | None:
    if not lang or lang.lower().split("-")[0] == "en" or lang.lower() in _loaded:
        return loaded(lang)
    return await hass.async_add_executor_job(load, lang)


def translate(text: str, lang: str | None) -> str:
    """*text* in *lang* when it's one of Sentient's own sentences; otherwise *text* unchanged.

    Uses only a language already read (async_load); never blocks."""
    d = loaded(lang)
    if d is None or not text or not isinstance(text, str):
        return text
    core = text.strip()
    if not core or not any(c.isalpha() for c in core):
        return text
    hit = _one(core, d)
    return text if hit is None else text.replace(core, hit, 1)


def known(text: str, lang: str | None) -> bool:
    """Whether *text* is one of Sentient's own sentences with a translation in *lang*."""
    d = loaded(lang)
    return d is not None and bool(text) and _one(text.strip(), d) is not None


async def async_written(hass, options, text):
    """*text* (one line or several) for reading — phones, notifications — in the house's written language."""
    from .language import written_language

    lang = written_language(hass, options)
    if not lang or not isinstance(text, str) or not text:
        return text
    await async_load(hass, lang)
    return "\n".join(translate(line, lang) for line in text.split("\n"))


async def async_written_data(hass, options, data):
    """The same for a notification's data: button titles and the like."""
    if not isinstance(data, dict):
        return data
    out = dict(data)
    if isinstance(out.get("actions"), list):
        out["actions"] = [
            {**a, "title": await async_written(hass, options, a.get("title"))} if isinstance(a, dict) else a
            for a in out["actions"]
        ]
    return out
