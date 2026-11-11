"""Small local keyword fallback for household plain-text documents.

No embedding service, AI call, permanent index or network request. PDF and
image files remain handled through the existing AI-task attachment path.
"""
from __future__ import annotations

import os
import re

_WORD = re.compile(r"[a-z0-9]{3,}")
STOP = {"what", "when", "where", "which", "does", "have", "with", "about", "from",
        "this", "that", "need", "tell", "please", "there", "could", "would"}
MAX_FILES = 40
MAX_BYTES = 131072


def _terms(text: str) -> set[str]:
    return set(_WORD.findall(text.casefold())) - STOP


def best_passage(folder: str, filename: str, question: str, limit: int = 500) -> str | None:
    """The paragraph of a local .txt/.md document that shares the most words with the question, or None.

    Used only when the AI can't be reached, so a question still gets the document's own words rather than nothing.
    The same path rules as the search apply; nothing outside the folder or behind a symlink is read.
    """
    query = _terms(question)
    if (not query or not isinstance(filename, str) or filename != os.path.basename(filename) or filename.startswith(".")
            or os.path.splitext(filename)[1].lower() not in (".md", ".txt")):
        return None
    path = os.path.join(folder, filename)
    if os.path.islink(path) or not os.path.isfile(path):
        return None
    try:
        with open(path, "rb") as handle:
            text = handle.read(MAX_BYTES).decode("utf-8", errors="replace")
    except OSError:
        return None
    best, score = None, 0
    for block in re.split(r"\n\s*\n", text):
        block = " ".join(block.split())
        hit = len(query & _terms(block))
        if hit > score:
            best, score = block, hit
    if not best:
        return None
    return best if len(best) <= limit else best[: limit - 1].rsplit(" ", 1)[0] + "…"


def search_local_text(folder: str, filenames: list[str], question: str) -> str | None:
    """Best matching local text filename, not a passage passed into an LLM.

    Reject unusual paths and symlinks; cap scan size and count to avoid blocking
    the HA executor. Files not matching the query are not selected.
    """
    query = _terms(question)
    if not query:
        return None
    matches: list[tuple[int, str]] = []
    for filename in sorted(filenames)[:MAX_FILES]:
        if (not isinstance(filename, str) or filename != os.path.basename(filename)
                or filename.startswith(".") or os.path.splitext(filename)[1].lower() not in (".md", ".txt")):
            continue
        path = os.path.join(folder, filename)
        if os.path.islink(path) or not os.path.isfile(path):
            continue
        try:
            with open(path, "rb") as handle:
                sample = handle.read(MAX_BYTES)
            content = sample.decode("utf-8", errors="replace").casefold()
        except OSError:
            continue
        terms = _terms(content)
        filename_terms = _terms(os.path.splitext(filename)[0])
        score = len(query & terms) + 3 * len(query & filename_terms)
        if score:
            matches.append((score, filename))
    if not matches:
        return None
    matches.sort(key=lambda row: (-row[0], row[1]))
    # An uncertain tie is better resolved by asking than guessing a source.
    if len(matches) > 1 and matches[0][0] == matches[1][0]:
        return None
    return matches[0][1]
