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


# a camera on one of these devices is the gadget's own (a voice tablet's front camera, a vacuum's map)
GADGET_DOMAINS = ("assist_satellite", "vacuum")  # not media_player: many real cameras have a speaker


def on_gadget(hass: HomeAssistant, entity_id: str) -> bool:
    """True when the camera belongs to a device that is really something else (a tablet, a vacuum)."""
    reg = er.async_get(hass)
    entry = reg.async_get(entity_id)
    if entry is None or not entry.device_id:
        return False
    return any(e.domain in GADGET_DOMAINS and not e.disabled_by and e.entity_id != entity_id for e in er.async_entries_for_device(reg, entry.device_id))


# the main stream of a camera ("MainStream" on ONVIF, "HD Stream" on Tapo): the camera itself when nothing plainer exists
PRIMARY_VIEW = re.compile(r"\b(?:hd|main|high)[ _-]?(?:stream|res|resolution|quality)\b", re.IGNORECASE)
# pictures that are never a camera, whatever else the device offers
NON_CAMERA_VIEW = re.compile(r"\bscreenshot\b|\blive[ _-]?map\b|\bsnapshots?\b", re.IGNORECASE)


def _names(hass: HomeAssistant, entity_id: str, entry: er.RegistryEntry | None, state: State | None = None) -> str:
    st = state or hass.states.get(entity_id)
    text = " ".join(filter(None, (entity_id.split(".", 1)[-1], st.name if st else None, entry.original_name if entry else None)))
    return text.replace("_", " ")


def is_duplicate_view(hass: HomeAssistant, entity_id: str, state: State | None = None) -> bool:
    """True for sub-streams, screenshots, maps and a gadget's own camera (matched on the id, name and device).

    A stream name alone isn't enough (#228): Home Assistant often disables a device's other streams already, and
    what's left ("Tor - Kamera MainStream", "Esszimmer HD Stream") is the camera itself.
    """
    if on_gadget(hass, entity_id):
        return True
    reg = er.async_get(hass)
    entry = reg.async_get(entity_id)
    text = _names(hass, entity_id, entry, state)
    if NON_CAMERA_VIEW.search(text):
        return True
    if not DUPLICATE_VIEW.search(text):
        return False
    if entry is None or not entry.device_id:
        # Without registry metadata, only suppress a named stream when a
        # corresponding base camera is actually present. Preserve ambiguous views.
        object_id = entity_id.split(".", 1)[-1].replace("_", " ")
        base = DUPLICATE_VIEW.sub("", object_id).strip(" -_")
        if base and base != object_id:
            return any(st.entity_id != entity_id and
                       st.entity_id.split(".", 1)[-1].replace("_", " ") == base
                       for st in hass.states.async_all("camera"))
        return False
    others = [e for e in er.async_entries_for_device(reg, entry.device_id)
              if e.domain == "camera" and e.entity_id != entity_id and not e.disabled_by
              and hass.states.get(e.entity_id) is not None
              and not NON_CAMERA_VIEW.search(_names(hass, e.entity_id, e))]
    if not others:
        return False  # the only camera its device still offers
    if any(not DUPLICATE_VIEW.search(_names(hass, e.entity_id, e)) for e in others):
        return True  # the device has a plainly named camera: this is an extra view of it
    if PRIMARY_VIEW.search(text):
        return False  # multiple primary streams may be separate lenses
    if any(PRIMARY_VIEW.search(_names(hass, e.entity_id, e)) for e in others):
        return True
    # No primary or plain view exists. Keep one stable representative.
    return entity_id != min([entity_id, *(e.entity_id for e in others)])


def inventory(hass: HomeAssistant, options: dict[str, Any]) -> list[dict[str, Any]]:
    """Shared camera classification; explicit security selections remain independent."""
    reg = er.async_get(hass)
    skip = ignored(options)
    rows = []
    for state in hass.states.async_all("camera"):
        entry = reg.async_get(state.entity_id)
        rows.append({"state": state, "entity_id": state.entity_id,
                     "device_id": entry.device_id if entry else None,
                     "extra": is_duplicate_view(hass, state.entity_id, state),
                     "ignored": state.entity_id in skip,
                     "available": state.state not in ("unknown", "unavailable"),
                     "supported_features": state.attributes.get("supported_features", 0)})
    return rows


def ignored(options: dict[str, Any]) -> set[str]:
    return set(options.get(CONF_IGNORE_CAMERAS) or [])


def cameras(hass: HomeAssistant, options: dict[str, Any]) -> list[State]:
    """The cameras Sentient should count, check and offer: not ignored, not an extra view."""
    hide = options.get(CONF_HIDE_SUBSTREAMS, True)
    return [row["state"] for row in inventory(hass, options)
            if not row["ignored"] and not (hide and row["extra"])]


def hidden(hass: HomeAssistant, options: dict[str, Any]) -> list[str]:
    """Camera entities Sentient is leaving out (for the settings page and diagnostics)."""
    keep = {st.entity_id for st in cameras(hass, options)}
    return sorted(st.entity_id for st in hass.states.async_all("camera") if st.entity_id not in keep)
