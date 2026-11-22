"""Knowing each person: who's probably talking when the voice wasn't matched, and their own things first. No AI.

Voice recognition (when installed) names the speaker. When it doesn't — a typed message in the Home Assistant app,
an Echo, a voice it didn't match, or a house without the add-on — two other things often settle it:

* **signed in** — a message typed in the app comes from a Home Assistant user, and that user belongs to a person;
* **their phone is here** — exactly one room-tracked phone is in the room the request came from.

The voice is told who it probably is and how that's known, and what that person told Sentient about themselves is
put first among the memories (household facts stay; nobody's things are hidden on a guess). A guess never allows
anything: confirmations, guest caution and private rooms work exactly as before, and nothing is saved under the
guessed name. In guest mode or a private room nothing is guessed. Off: nothing changes.
"""

from __future__ import annotations

from contextvars import ContextVar
from typing import Any

from homeassistant.core import HomeAssistant

CONF_KNOW_PEOPLE = "know_people"
# who this turn is probably from (casefolded), for putting their own memories first; set while the prompt is built
GUESS: ContextVar[str | None] = ContextVar("sentient_likely_speaker", default=None)


def _person_for_user(hass: HomeAssistant, user_id: str | None) -> str | None:
    if not user_id:
        return None
    for st in hass.states.async_all("person"):
        if st.attributes.get("user_id") == user_id:
            return st.name
    return None


def _owner_of_tracker(hass: HomeAssistant, tracker: str) -> str | None:
    """'Conah's phone' -> 'Conah', when that is a person in Home Assistant."""
    from .room_trackers import whose

    state = hass.states.get(tracker)
    label = whose(state.name if state else tracker)
    people = {(st.name or "").casefold(): st.name for st in hass.states.async_all("person")}
    for name_cf, name in people.items():
        if name_cf and label.casefold().startswith(name_cf):
            return name
    return None


def _phone_in_room(hass: HomeAssistant, options: dict[str, Any], area_id: str | None) -> str | None:
    if not area_id:
        return None
    from .room_trackers import CONF_ROOM_TRACKERS, CONF_ROOM_TRACKING, _area_for

    if not options.get(CONF_ROOM_TRACKING):
        return None
    owners = set()
    for tracker in options.get(CONF_ROOM_TRACKERS) or []:
        state = hass.states.get(tracker)
        if state is not None and _area_for(hass, state) == area_id and (owner := _owner_of_tracker(hass, tracker)):
            owners.add(owner)
    return owners.pop() if len(owners) == 1 else None


def likely_speaker(hass: HomeAssistant, options: dict[str, Any], *, user_id: str | None, area_id: str | None,
                   voice_matched: bool, guest: bool, private: bool) -> tuple[str, str] | None:
    """(name, how it's known), or None. Never when voice recognition heard the voice (matched or not, it says more
    than a phone nearby), in guest mode or in a private room."""
    if not options.get(CONF_KNOW_PEOPLE) or voice_matched or guest or private:
        return None
    if name := _person_for_user(hass, user_id):
        return name, "they're signed in to Home Assistant as them"
    if name := _phone_in_room(hass, options, area_id):
        return name, "their phone is the only tracked phone in this room"
    return None


def prompt_line(guess: tuple[str, str] | None) -> list[str]:
    if guess is None:
        return []
    name, how = guess
    return [f"Probably {name} talking ({how}) — a good guess, not proof. Use it to pick their own preferences and "
            "what they've told you about themselves; if they say they're someone else, believe them. It never "
            "allows anything that needs a confirmation, and don't save anything under their name because of it."]
