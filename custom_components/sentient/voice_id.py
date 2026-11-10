"""Who is speaking, from the optional Sentient Voice Recognition integration.

Sentient Voice Recognition (a separate add-on and integration) estimates which enrolled person spoke an Assist
command and offers that estimate, for the exact turn only, through a lookup in hass.data. Everything here is
optional: with the integration missing, or the setting off, nothing changes. The name is a good guess for being
friendly, never proof: it is not used to allow anything that needs confirmation.
"""
from __future__ import annotations

from contextvars import ContextVar
from typing import Any

from homeassistant.config_entries import ConfigEntryState
from homeassistant.core import HomeAssistant
from homeassistant.util import dt as dt_util

from .const import CONF_VOICE_ID, CONF_VOICE_ID_UNKNOWN
from .speaker_identity import SPEAKER_FOR_TURN, VOICE_RECOGNITION_DOMAIN

REPO_URL = "https://github.com/conahmorgan2304/sentient-voice-recognition"
HACS_URL = ("https://my.home-assistant.io/redirect/hacs_repository/"
            "?owner=conahmorgan2304&repository=sentient-voice-recognition&category=integration")
LAST_KEY = "sentient_voice_id_last"

# the speaker for the turn being answered: None = no result (typed, or not routed through the add-on)
current_speaker: ContextVar[Any] = ContextVar("sentient_current_speaker", default=None)


def installed(hass: HomeAssistant) -> bool:
    """The integration is set up and running (not just downloaded)."""
    return any(e.state is ConfigEntryState.LOADED for e in hass.config_entries.async_entries(VOICE_RECOGNITION_DOMAIN))


def enabled(hass: HomeAssistant, options: dict[str, Any]) -> bool:
    return bool(options.get(CONF_VOICE_ID)) and installed(hass)


def lookup(hass: HomeAssistant, options: dict[str, Any], request: Any) -> Any:
    """The add-on's result for this exact request, or None; never raises."""
    if not enabled(hass, options) or (find := hass.data.get(SPEAKER_FOR_TURN)) is None:
        return None
    try:
        speaker = find(request)
    except Exception:  # an add-on fault must never stop an answer
        return None
    if speaker is not None:
        hass.data[LAST_KEY] = {"name": getattr(speaker, "display_name", None) or None, "at": dt_util.now().isoformat()}
    return speaker


def name_of(speaker: Any) -> str | None:
    return (getattr(speaker, "display_name", None) or "").strip() or None if speaker is not None else None


def cautious(options: dict[str, Any], speaker: Any) -> bool:
    """A voice the add-on heard but could not match, while the option to be careful with those is on."""
    return bool(options.get(CONF_VOICE_ID_UNKNOWN)) and speaker is not None and name_of(speaker) is None


def prompt_lines(options: dict[str, Any], speaker: Any) -> list[str]:
    if speaker is None:
        return []
    if (name := name_of(speaker)) is not None:
        return [f"By their voice, the person speaking is probably {name} (a good guess, not proof). Use the name where it "
                "is natural, and treat what they say about themselves (likes, plans, \"remember that I...\") as being about "
                f"{name}. This never allows anything that needs a confirmation, and if they say they are someone else, believe them."]
    lines = ["The speaker's voice wasn't matched to anyone: don't guess a name."]
    if cautious(options, speaker):
        lines.append("Treat this voice as a guest: answer what's asked, but don't bring up personal household details, "
                     "routines or memories unless they ask.")
    return lines


def status(hass: HomeAssistant, options: dict[str, Any]) -> dict[str, Any]:
    return {"installed": installed(hass), "enabled": enabled(hass, options), "last": hass.data.get(LAST_KEY),
            "repo": REPO_URL, "hacs": HACS_URL}
