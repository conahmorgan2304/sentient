"""Who is speaking, from the optional Sentient Voice Recognition integration.

Sentient Voice Recognition (a separate add-on and integration) estimates which enrolled person spoke an Assist
command and offers that estimate, for the exact turn only, through a lookup in hass.data. Everything here is
optional: with the integration missing, or the setting off, nothing changes. The name is a good guess for being
friendly, never proof: it is not used to allow anything that needs confirmation.
"""
from __future__ import annotations

import re
import math
from collections import deque
from statistics import median
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
HEARD_KEY = "sentient_voice_id_heard"  # what the add-on reported for the latest turn (for the page, never the audio)
RECENT_KEY = "sentient_voice_id_recent"  # the last few turns, in memory only (gone on restart, never any audio)
RECENT_MAX = 8
EVENT_TURN = f"{VOICE_RECOGNITION_DOMAIN}_turn"
ENROL_DOMAIN = VOICE_RECOGNITION_DOMAIN
REASONS = {
    "matched": "matched a profile",
    "carried_over": "too short to judge, so the voice matched a moment ago on this speaker was kept",
    "below_threshold": "too different from every saved voice (below the match level)",
    "ambiguous": "too close to two saved voices to be sure",
    "no_profiles": "no voice profiles are saved yet",
    "insufficient_speech": "too little speech to judge (try a longer sentence)",
    "recognition_busy": "the engine was busy",
    "engine_unavailable": "the voice engine didn't answer",
    "unsupported_audio": "this microphone's audio format can't be checked",
    "profile_removed": "that profile has since been removed",
}

# the speaker for the turn being answered: None = no result (typed, or not routed through the add-on)
current_speaker: ContextVar[Any] = ContextVar("sentient_current_speaker", default=None)
UNKNOWN_SPEAKER = object()


def installed(hass: HomeAssistant) -> bool:
    """The integration is set up and running (not just downloaded)."""
    return any(e.state is ConfigEntryState.LOADED for e in hass.config_entries.async_entries(VOICE_RECOGNITION_DOMAIN))


def enabled(hass: HomeAssistant, options: dict[str, Any]) -> bool:
    return bool(options.get(CONF_VOICE_ID)) and installed(hass)


def lookup(hass: HomeAssistant, options: dict[str, Any], request: Any) -> Any:
    """The add-on's result for this exact request, or None; never raises."""
    if not options.get(CONF_VOICE_ID):
        return None
    speaker = None
    if enabled(hass, options) and (find := hass.data.get(SPEAKER_FOR_TURN)) is not None:
        try:
            speaker = find(request)
        except Exception:  # a recognition fault must not stop an answer
            pass
    if speaker is None and options.get(CONF_VOICE_ID_UNKNOWN) and getattr(request, "satellite_id", None):
        return UNKNOWN_SPEAKER
    if speaker is not None:
        hass.data[LAST_KEY] = {"name": getattr(speaker, "display_name", None) or None, "at": dt_util.now().isoformat()}
    return speaker


def name_of(speaker: Any) -> str | None:
    return (getattr(speaker, "display_name", None) or "").strip() or None if speaker is not None else None


def cautious(options: dict[str, Any], speaker: Any) -> bool:
    """A voice the add-on heard but could not match, while the option to be careful with those is on."""
    return bool(options.get(CONF_VOICE_ID_UNKNOWN)) and speaker is not None and name_of(speaker) is None


def cautious_turn(options: dict[str, Any]) -> bool:
    """Return whether the current unmatched voice needs privacy protections."""
    return cautious(options, current_speaker.get())


def private_history(rt: Any, device_id: str | None) -> bool:
    """Keep household history out of guest and private-room responses."""
    profiles = getattr(rt, "profiles", None)
    profile = profiles.for_device(device_id) if profiles is not None else None
    return (bool((profile or {}).get("private")) or cautious_turn(getattr(rt, "options", {}))
            or bool(getattr(getattr(rt, "speech", None), "guest_mode", False)))


CONF_SPEAKER_MEMORIES = "speaker_memories"
_OWNER = re.compile(r"^\s*([^:\n]{1,40}):\s+\S")


def owner_of(text: str, people: set[str]) -> str | None:
    """The person a memory is about, when it is filed as "Name: ..." under a known person's name."""
    found = _OWNER.match(text or "")
    if found and found.group(1).strip().casefold() in people:
        return found.group(1).strip().casefold()
    return None


def known_people(hass: HomeAssistant) -> set[str]:
    """The household's people: Home Assistant person entities and the voices heard lately."""
    names = {(st.name or "").strip().casefold() for st in hass.states.async_all("person")}
    for row in hass.data.get(RECENT_KEY) or []:
        if isinstance(row, dict) and row.get("name"):
            names.add(str(row["name"]).strip().casefold())
    return {n for n in names if n}


def speaker_scope(hass: HomeAssistant, options: dict[str, Any], memories: list[Any], speaker: Any = None) -> list[Any]:
    """Speaker-aware memories (setting "speaker_memories", off by default; needs voice recognition running).

    A matched voice gets their own memories first and other people's personal ones are left out; a voice that was
    heard but not matched gets none of anyone's personal memories. With no voice to go on (typed in the app, an
    Echo), nothing changes. Household facts are never touched.
    """
    speaker = current_speaker.get() if speaker is None else speaker
    if not options.get(CONF_SPEAKER_MEMORIES) or not enabled(hass, options) or speaker is None:
        return memories
    people = known_people(hass)
    me = (name_of(speaker) or "").casefold() if speaker is not UNKNOWN_SPEAKER else ""
    if me:
        people.add(me)
    own, shared = [], []
    for m in memories:
        # New memories use durable owner metadata. Legacy memories retain
        # their historical "Name: ..." attribution until migrated.
        explicit_owner = getattr(m, "owner", None)
        whose = (str(explicit_owner).strip().casefold() if explicit_owner
                 else owner_of(getattr(m, "text", ""), people))
        if whose is None:
            shared.append(m)
        elif me and whose == me:
            own.append(m)
    return own + shared


def standing_lines(hass: HomeAssistant, options: dict[str, Any]) -> list[str]:
    """Said every turn while voice recognition is running, so the assistant never denies it exists."""
    if not enabled(hass, options):
        return []
    return ["This house has speaker recognition (Sentient Voice Recognition): it matches a voice against saved household voice "
            "profiles. If asked whether you can recognise voices or know who is speaking, say yes plainly \u2014 never say you don't "
            "identify or track voices. You only know a name when one is given to you for this turn, and a match is a good guess, "
            "not proof; when none is given, say you couldn't tell this time."]


def prompt_lines(options: dict[str, Any], speaker: Any) -> list[str]:
    if speaker is None:
        return []
    if (name := name_of(speaker)) is not None:
        if not getattr(speaker, "share_name", False):
            return []
        return [f"Sentient Voice Recognition matched their voice to the saved profile for {name}, so the person speaking is probably "
                f"{name} (a good guess, not proof); if asked how you know, say that. Use the name where it "
                "is natural \u2014 unless the house rules say not to address people by name, in which case never say it \u2014 and treat what "
                f"they say about themselves (likes, plans, \"remember that I...\") as being about {name}. This never allows anything "
                "that needs a confirmation, and if they say they are someone else, believe them."]
    lines = ["The speaker's voice wasn't matched to anyone: don't guess a name."]
    if cautious(options, speaker):
        lines.append("Treat this voice as a guest: answer what's asked, but don't bring up personal household details, "
                     "routines or memories.")
    return lines


def record_turn(hass: HomeAssistant, event: Any, options: dict[str, Any] | None = None) -> None:
    """Remember why the add-on matched or didn't match the latest voice (its own report; no audio).

    Nothing is kept when the setting is off or the add-on isn't running."""
    if options is not None and not enabled(hass, options):
        return
    data = event.data or {}
    reason = str(data.get("reason") or "")
    heard = {
        "at": dt_util.now().isoformat(), "name": data.get("speaker_name") or None, "reason": reason,
        "why": REASONS.get(reason, reason.replace("_", " ")), "similarity": data.get("similarity"),
        "gap": data.get("runner_up_gap"),
    }
    hass.data[HEARD_KEY] = heard
    recent = hass.data.setdefault(RECENT_KEY, deque(maxlen=RECENT_MAX))
    recent.appendleft(heard)


async def start_enrolment(hass: HomeAssistant, name: str, satellite_id: str) -> dict[str, Any]:
    """Ask the add-on to open its speaker recording window for this person."""
    if not installed(hass):
        raise ValueError("Sentient Voice Recognition isn't running")
    return await hass.services.async_call(
        ENROL_DOMAIN, "start_speaker_enrolment", {"name": name, "satellite_id": satellite_id, "consent": True},
        blocking=True, return_response=True)


async def cancel_enrolment(hass: HomeAssistant) -> None:
    if installed(hass):
        await hass.services.async_call(ENROL_DOMAIN, "cancel_speaker_enrolment", {}, blocking=True)


_FIRST_PERSON = re.compile(r"\b(i|i'm|i've|i'll|i'd|my|mine|me|myself)\b", re.I)


def attribute(options: dict[str, Any], speaker: Any, request: str, text: str) -> str:
    """Make a first-person memory say whose it is ("Alex: likes tea"), when the voice was matched.

    Only applies to something the speaker said about themselves; household facts are left alone."""
    name = name_of(speaker)
    if not name or not getattr(speaker, "share_name", False) or not options.get(CONF_VOICE_ID) or not _FIRST_PERSON.search(request or ""):
        return text
    if name.casefold() in text.casefold():
        return text
    return f"{name}: {text}"


def status(hass: HomeAssistant, options: dict[str, Any]) -> dict[str, Any]:
    return {"installed": installed(hass), "enabled": enabled(hass, options), "last": hass.data.get(LAST_KEY),
            "heard": hass.data.get(HEARD_KEY), "recent": list(hass.data.get(RECENT_KEY) or []),
            "repo": REPO_URL, "hacs": HACS_URL}


SIMILAR_VOICES = 0.8  # two saved voices this alike could be mistaken for each other
NEAR_MISS = 0.15  # turns this close under the match level are worth a suggestion
MIN_TURNS_FOR_ADVICE = 3


async def details(hass: HomeAssistant) -> dict[str, Any] | None:
    """The add-on's own status (engine reachable, saved voices, match level), or None if it can't be asked."""
    if not installed(hass):
        return None
    try:
        return await hass.services.async_call(ENROL_DOMAIN, "status", {}, blocking=True, return_response=True)
    except Exception:  # noqa: BLE001 - an add-on fault must never break a page
        return None


def advice(detail: dict[str, Any] | None, recent: list[dict[str, Any]]) -> dict[str, Any] | None:
    """Suggest a lower match level when several recent voices only just missed it. The person decides."""
    if not detail:
        return None
    # A damaged or non-finite engine score must never produce an invalid
    # match-level recommendation. Ambiguous voices are not threshold misses.
    try:
        level = float(detail.get("threshold") or 0)
    except (TypeError, ValueError, OverflowError):
        return None
    if not math.isfinite(level) or not 0 < level <= 1:
        return None
    misses = []
    for row in recent:
        if not isinstance(row, dict) or row.get("reason") != "below_threshold":
            continue
        try:
            score = float(row.get("similarity"))
            gap = row.get("gap")
            gap = float(gap) if gap is not None else None
        except (TypeError, ValueError, OverflowError):
            continue
        if not math.isfinite(score) or not 0 <= score <= 1:
            continue
        # An uncertain runner-up requires better enrolment, not lowering the
        # threshold; a null runner-up is legitimate with a single profile.
        if gap is not None and (not math.isfinite(gap) or gap < 0.10):
            continue
        misses.append(score)
    if len(misses) < MIN_TURNS_FOR_ADVICE:
        return None
    middle = median(misses)
    if middle < level - NEAR_MISS:
        return None
    value = max(0.5, round(middle - 0.02, 2))
    if value >= level:
        return None
    return {"value": value, "level": level, "turns": len(misses), "median": round(middle, 2),
            "text": (f"The last {len(misses)} voices that weren't matched scored about {round(middle, 2)} against a match level of {level}. "
                     f"If those were enrolled people, lowering it to {value} would match them. "
                     "A lower level makes a wrong name a little more likely.")}


def health_rows(detail: dict[str, Any] | None) -> list[tuple[str, str, str, str | None]]:
    """(title, status, message, fix) rows for the house check; empty when all is well or nothing can be said."""
    if detail is None:
        return [("Voice recognition", "warn", "Sentient Voice Recognition didn't answer.",
                 "Settings → Devices & services → Sentient Voice Recognition: check it is running.")]
    rows: list[tuple[str, str, str, str | None]] = []
    if not detail.get("engine"):
        rows.append(("Voice engine", "bad", "The voice engine isn't answering, so voices can't be matched.",
                     "Settings → Apps → Sentient Voice Engine: check it is started."))
    profiles = detail.get("profiles") or []
    if not profiles:
        rows.append(("Voice profiles", "warn", "No voices are saved yet, so nobody can be matched.",
                     "Settings → Devices & services → Sentient Voice Recognition → Configure → Voice profiles."))
    for p in profiles:
        if (p.get("nearest_score") or 0) >= SIMILAR_VOICES:
            rows.append(("Voice profiles", "warn", f"{p['name']} and {p['nearest']} sound very alike to the engine, so one may be named as the other.",
                         "Record both again, each from the speaker they normally use."))
            break
    if not rows:
        rows.append(("Voice recognition", "ok", f"{len(profiles)} voice{'s' if len(profiles) != 1 else ''} saved and the engine is answering.", None))
    return rows
