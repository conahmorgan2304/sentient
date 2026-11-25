"""Names said the way they're pronounced, not the way they're spelt.

A text-to-speech voice reads a name from its spelling ("Siobhan" comes out as "See-ob-han"). Telling the AI how to say it
doesn't help: it still writes the name correctly, and the voice still reads the spelling. So the spoken words are
changed on their way to the voice, and only there; what's written (the chat, the timeline, phones) keeps the real
spelling.

Pronunciations come from two places:

* Settings → Voice: one per line, "Siobhan = Shivawn";
* memories that say so: "Siobhan is pronounced Shivawn", "Siobhan's name is pronounced Shivawn",
  "Always pronounce Siobhan's name as Shivawn".

Used for replies on speakers (streamed or not), announcements, questions and reading. Typed chats keep the
spelling. Switch: "Say names as they're pronounced" (on by default).

The same step writes numbers as words for voices in other languages (numbers.py; "Say numbers as words"). That one
also applies to typed Assist chats, since Home Assistant can read those replies aloud too.
"""
from __future__ import annotations

import re
from typing import Any, Callable

CONF_PRONOUNCE = "pronounce_names"
CONF_PRONUNCIATIONS = "pronunciations"
CONF_SAY_NUMBERS = "say_numbers_as_words"
_NAME = r"([A-Z][\w'’-]{1,30})"
_SAID = r"\"?“?([A-Za-z][A-Za-z' -]{1,30}?)\"?”?"
_FROM_MEMORY = [
    re.compile(rf"\b{_NAME}(?:['’]s name)?\s+is\s+pronounced\s+(?:as\s+|like\s+)?{_SAID}\s*(?:[.,;!]|$)"),
    re.compile(rf"\bpronounce\s+{_NAME}(?:['’]s name)?\s+(?:as|like)\s+{_SAID}\s*(?:[.,;!]|$)", re.I),
    re.compile(rf"\b{_NAME}(?:['’]s name)?\s+(?:is\s+)?(?:said|sounds)\s+(?:as|like)\s+{_SAID}\s*(?:[.,;!]|$)"),
]


def parse_setting(text: str | None) -> dict[str, str]:
    out = {}
    for line in re.split(r"[\n;]", text or ""):
        if "=" in line:
            written, said = (part.strip().strip('"') for part in line.split("=", 1))
            if written and said and written.casefold() != said.casefold():
                out[written] = said
    return out


def from_memories(texts: list[str]) -> dict[str, str]:
    out: dict[str, str] = {}
    for text in texts:
        for pattern in _FROM_MEMORY:
            if m := pattern.search(text):
                written, said = m.group(1), m.group(2).strip()
                if written.casefold() != said.casefold() and len(said.split()) <= 3:
                    out[written] = said
                break
    return out


def apply(text: str, table: dict[str, str]) -> str:
    """Every whole-word match of a written name swapped for how it's said (keeping "'s")."""
    if not text or not table:
        return text
    for written, said in sorted(table.items(), key=lambda kv: -len(kv[0])):
        text = re.sub(rf"(?<![\w-]){re.escape(written)}(?![\w-])", said, text, flags=re.I)
    return text


class Pronouncer:
    """The table, rebuilt when the settings or the memories change."""

    def __init__(self, options: Callable[[], dict[str, Any]], memories: Callable[[], list[str]],
                 language: Callable[[], str | None] = lambda: None) -> None:
        self._options = options
        self._memories = memories
        self._language = language  # what announcements are said in (None: English)
        self._key: tuple | None = None
        self._table: dict[str, str] = {}

    def table(self) -> dict[str, str]:
        opts = self._options() or {}
        if not opts.get(CONF_PRONOUNCE, True):
            return {}
        texts = self._memories()
        key = (opts.get(CONF_PRONUNCIATIONS), tuple(texts))
        if key != self._key:
            self._key = key
            self._table = {**from_memories(texts), **parse_setting(opts.get(CONF_PRONUNCIATIONS))}
        return self._table

    def numbers(self, language: str | None = None) -> str | None:
        """The number language for *language* (or the announcements' language), when numbers become words."""
        if not (self._options() or {}).get(CONF_SAY_NUMBERS, True):
            return None
        from .numbers import converter

        return converter(language if language is not None else self._language())

    def __call__(self, text: str, language: str | None = None) -> str:
        text = apply(text, self.table())
        if self.numbers(language):
            from .numbers import words

            text = words(text, language if language is not None else self._language())
        return text

    def speaking(self, language: str | None, names: bool = True) -> "Speaking":
        return Speaking(self, language, names)


class Speaking:
    """A pronouncer for one reply: its language, and whether names change (only on speakers)."""

    def __init__(self, pronouncer: Pronouncer, language: str | None, names: bool) -> None:
        self._p = pronouncer
        self.language = language or "en"
        self._names = names

    def table(self) -> dict[str, str]:
        return self._p.table() if self._names else {}

    def numbers(self) -> bool:
        return self._p.numbers(self.language) is not None

    def active(self) -> bool:
        return bool(self.table()) or self.numbers()

    def __call__(self, text: str) -> str:
        if self._names:
            return self._p(text, self.language)
        if self.numbers():
            from .numbers import words

            return words(text, self.language)
        return text


_NUMBER_TAIL = re.compile(r"-?[\d.,:  %°–-]*\d[\d.,:  %°–-]*$")


class StreamPronouncer:
    """For a reply spoken as it's written. A word at the end of a piece that could be the start of a name
    ("Con", waiting for "ah") is held back until the next piece, or until the reply ends (``flush``)."""

    def __init__(self, pronouncer: Callable[[str], str], table: Callable[[], dict[str, str]],
                 numbers: Callable[[], bool] = lambda: False) -> None:
        self._pronounce = pronouncer
        self._table = table
        self._numbers = numbers
        self._held = ""

    def feed(self, content: str) -> str:
        text = self._held + content
        self._held = ""
        if self._numbers() and (tail := _NUMBER_TAIL.search(text)) and any(c.isdigit() for c in tail[0]):
            # "4," may be "4,4°" in the next piece: said once it's whole
            self._held, text = text[tail.start():], text[:tail.start()]
        names = [k.casefold() for k in self._table()]
        if not names:
            return self._pronounce(text) if text and self._numbers() else text
        cut = len(text)
        while cut > 0 and (text[cut - 1].isalnum() or text[cut - 1] in "'’-"):
            cut -= 1
        tail = text[cut:].casefold()
        if tail and any(name.startswith(tail) for name in names):
            self._held = text[cut:]
            text = text[:cut]
        return self._pronounce(text) if text else ""

    def flush(self) -> str:
        text, self._held = self._held, ""
        return self._pronounce(text) if text else ""
