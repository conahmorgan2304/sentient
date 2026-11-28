"""Discover media files under a configured directory."""

import os
from collections.abc import Iterator
from pathlib import Path


def walk_media(folder: str) -> Iterator[tuple[str, list[str]]]:
    """Yield each directory and its sorted regular files, relative to the catalog root."""
    if os.path.islink(folder):
        return
    for path, dirs, files in os.walk(folder):
        dirs[:] = sorted(name for name in dirs if not name.startswith("."))
        relative = os.path.relpath(path, folder)
        names = []
        for name in sorted(files):
            full = os.path.join(path, name)
            if not name.startswith(".") and not os.path.islink(full) and os.path.isfile(full):
                names.append(name)
        yield "" if relative == "." else relative, names


def media_file(folder: str, relative: str) -> Path | None:
    """Validate a catalog path again when reading it, including its parent directories."""
    if not isinstance(relative, str) or any(part.startswith(".") or not part for part in relative.split("/")):
        return None
    try:
        if os.path.islink(folder):
            return None
        path = Path(folder).resolve() / relative
        if path.resolve() == path and path.is_file():
            return path
    except (OSError, RuntimeError, ValueError):
        return None
    return None
