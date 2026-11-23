"""Capability awareness: what this house can and can't do by voice, so the voice never offers the impossible.

On 14 Nov it offered to feed the rabbits; offers no device can do are counted on the Mind page. The voice is told,
from the entities exposed to voice assistants, what kinds of things it can control (and how many), and which common
kinds this house simply doesn't have (no locks, no blinds), so "shall I lock the back door?" isn't offered in a
house with no smart lock. Worked out at most every five minutes; no AI. Off: nothing is added.
"""

from __future__ import annotations

import re
import time
from typing import Any

from homeassistant.core import HomeAssistant

CONF_CAPABILITIES = "capability_awareness"
CACHE_SECONDS = 300

KINDS = [  # (domain, one, many)
    ("light", "light", "lights"),
    ("climate", "heating or cooling control", "heating and cooling controls"),
    ("switch", "switch or plug", "switches and plugs"),
    ("fan", "fan", "fans"),
    ("cover", "blind, curtain or garage door", "blinds, curtains and garage doors"),
    ("lock", "lock", "locks"),
    ("vacuum", "robot vacuum", "robot vacuums"),
    ("media_player", "speaker or TV", "speakers and TVs"),
    ("camera", "camera (to look, not to move)", "cameras (to look, not to move)"),
    ("valve", "valve", "valves"),
    ("water_heater", "water heater", "water heaters"),
    ("humidifier", "humidifier", "humidifiers"),
    ("lawn_mower", "lawn mower", "lawn mowers"),
]
# common things people ask for: said when this house has none, so they're never offered
# "smart": 22 Nov, asked to lock the back door it said "this house doesn't have locks on the doors"
ABSENT_WORTH_SAYING = {"lock": "smart locks", "cover": "motorised blinds, curtains or garage doors",
                       "valve": "smart water valves", "lawn_mower": "robot mower", "water_heater": "smart water heater"}

CACHE_KEY = "sentient_capabilities"


def _count(hass: HomeAssistant) -> dict[str, int]:
    from homeassistant.helpers import entity_registry as er

    from .const import DOMAIN
    from .llm_api import _exposed

    registry = er.async_get(hass)
    counts: dict[str, int] = {}
    wanted = {k[0] for k in KINDS}
    for st in hass.states.async_all():
        if (entry := registry.async_get(st.entity_id)) is not None and entry.platform == DOMAIN:
            continue  # Sentient's own switches (hush, guest mode…) aren't things in the house
        if st.domain in wanted and _exposed(hass, st.entity_id):
            counts[st.domain] = counts.get(st.domain, 0) + 1
    return counts


def line(hass: HomeAssistant, options: dict[str, Any]) -> str | None:
    if not options.get(CONF_CAPABILITIES):
        return None
    now = time.monotonic()
    cache = hass.data.setdefault(CACHE_KEY, {"at": 0.0, "line": None})
    if cache["line"] is not None and now - cache["at"] < CACHE_SECONDS:
        return cache["line"]
    counts = _count(hass)
    have = [f"{n} {many if n > 1 else one}" for domain, one, many in KINDS if (n := counts.get(domain))]
    missing = [words for domain, words in ABSENT_WORTH_SAYING.items() if not counts.get(domain)]
    if not have:
        return None
    text = ("What you can control in this house by voice: " + ", ".join(have) + ". Only offer things these can do.")
    if missing:
        listed = ", ".join(missing[:-1]) + " or " + missing[-1] if len(missing) > 1 else missing[0]
        text += (f" This house has no {listed} you can control. The ordinary ones are still there, just not "
                 "connected: never offer to work them, and if asked, say you can't, e.g. \"there's no smart lock I "
                 "can use\" — never that the house has no locks.")
    cache.update(at=now, line=text)
    return text


# 22 Nov: "Shall I close the bedroom window or leave it?" — said with a confirmed memory and the capability line
# both saying it can't. A rule in the prompt can be missed, so the sentence is taken out of the reply.
_OFFER_START = (r"\b(?:shall i|should i|do you want me to|would you like me to|want me to|i can\b(?!['’]t)|"
                r"i could\b(?!['’]?n)|let me|i'?ll|i will|happy to)\b[^.?!]{0,40}?")
MOVED_THINGS = ("window", "door", "gate", "blind", "curtain", "shutter")


def impossible_offer(hass: HomeAssistant, options: dict[str, Any]) -> re.Pattern[str] | None:
    """An offer to open, close or lock something no device here can move, or None if there's nothing like that."""
    if not options.get(CONF_CAPABILITIES):
        return None
    now = time.monotonic()
    cache = hass.data.setdefault(CACHE_KEY + "_offers", {"at": 0.0, "pattern": None, "set": False})
    if cache["set"] and now - cache["at"] < CACHE_SECONDS:
        return cache["pattern"]
    from .llm_api import _exposed

    movable = " ".join((st.name or st.entity_id).casefold() for st in hass.states.async_all("cover")
                       if _exposed(hass, st.entity_id))
    things = [t for t in MOVED_THINGS if t not in movable]
    parts = []
    if things:
        parts.append(r"\b(?:open|close|shut)\b[^.?!]{0,30}?\b(?:" + "|".join(things) + r")s?\b")
        if {"window", "door"} <= set(things):
            # 23 Nov self-test: "The bedroom window is open. Shall I close it?" — nothing here closes anything
            parts.append(r"\b(?:open|close|shut)\s+(?:it|them|that|those|one)\b(?!\s+(?:app|programme|program))")
    if not any(_exposed(hass, st.entity_id) for st in hass.states.async_all("lock")):
        parts.append(r"\b(?:lock|unlock)\b")
    pattern = re.compile(_OFFER_START + "(?:" + "|".join(parts) + ")", re.I) if parts else None
    cache.update(at=now, pattern=pattern, set=True)
    return pattern
