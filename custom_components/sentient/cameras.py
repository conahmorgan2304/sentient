"""Which cameras count as real ones.

Home Assistant lists every picture source as a camera: a doorbell's HD and SD streams, tablet
screenshots, a vacuum's map. Counting them all makes "12 cameras" for a house with five.
This one place decides what Sentient treats as a camera; everything else asks it.
Hiding a camera here only stops Sentient counting and checking it — nothing is deleted.
"""

from __future__ import annotations

import re
from typing import Any

from homeassistant.core import HomeAssistant, State
from homeassistant.helpers import entity_registry as er

from .const import CONF_HIDE_SUBSTREAMS, CONF_IGNORE_CAMERAS

# a second view of a camera that already exists, or a picture that isn't a camera at all
DUPLICATE_VIEW = re.compile(
    r"\b(?:hd|sd|sub|main|low|high|alt)[ _-]?(?:stream|res|resolution|quality)\b"
    r"|\bsub[ _-]?streams?\b|\bscreenshot\b|\blive[ _-]?map\b|\bsnapshots?\b",
    re.IGNORECASE,
)


def is_duplicate_view(hass: HomeAssistant, entity_id: str, state: State | None = None) -> bool:
    """True for sub-streams, screenshots and maps (matched on the id, name and original name)."""
    entry = er.async_get(hass).async_get(entity_id)
    st = state or hass.states.get(entity_id)
    text = " ".join(filter(None, (entity_id.split(".", 1)[-1], st.name if st else None, entry.original_name if entry else None)))
    return bool(DUPLICATE_VIEW.search(text.replace("_", " ")))


def ignored(options: dict[str, Any]) -> set[str]:
    return set(options.get(CONF_IGNORE_CAMERAS) or [])


def cameras(hass: HomeAssistant, options: dict[str, Any]) -> list[State]:
    """The cameras Sentient should count, check and offer: not ignored, not an extra view."""
    skip = ignored(options)
    hide = options.get(CONF_HIDE_SUBSTREAMS, True)
    out = []
    for st in hass.states.async_all("camera"):
        if st.entity_id in skip or (hide and is_duplicate_view(hass, st.entity_id, st)):
            continue
        out.append(st)
    return out


def hidden(hass: HomeAssistant, options: dict[str, Any]) -> list[str]:
    """Camera entities Sentient is leaving out (for the settings page and diagnostics)."""
    keep = {st.entity_id for st in cameras(hass, options)}
    return sorted(st.entity_id for st in hass.states.async_all("camera") if st.entity_id not in keep)
