"""Answer a direct speaker identification question using an optional local match."""

import re

from homeassistant.components.conversation import ConversationInput
from homeassistant.core import HomeAssistant

VOICE_RECOGNITION_DOMAIN = "sentient_voice_recognition"
SPEAKER_FOR_TURN = f"{VOICE_RECOGNITION_DOMAIN}.speaker_for_turn"

_IDENTITY_QUESTION = re.compile(
    r"(?:please |(?:can|could) you (?:please )?tell me )?"
    r"(?:who am i|what(?: is|'s) my name|"
    r"who(?: is|'s| you think is| do you think is) (?:speaking|talking)(?: to you)?)"
    r"(?: right now)?(?: please)?[?.!]*"
)


def speaker_identity_reply(
    hass: HomeAssistant, request: ConversationInput, *, guest: bool
) -> str | None:
    """Use the local result for an English identity check on this Assist turn."""
    lookup = hass.data.get(SPEAKER_FOR_TURN)
    if lookup is None or request.language.partition("-")[0].lower() != "en":
        return None

    text = " ".join(request.text.casefold().replace("’", "'").split())
    if not _IDENTITY_QUESTION.fullmatch(text) or (speaker := lookup(request)) is None:
        return None
    if guest:
        return "Voice identification is unavailable in guest mode."
    if speaker.speaker_id and speaker.display_name:
        return f"Your voice matches {speaker.display_name}'s profile."
    return "I couldn't match your voice to a profile this time."
