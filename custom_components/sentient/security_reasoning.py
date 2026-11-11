"""Weighing a camera-security alert against everything else the house knows, before it is said out loud.

A person sensor on its own can't tell a burglar from someone popping out to the bins. Before a camera-security
alert is spoken, this looks at the other evidence it already has, with no AI:

* the alarm: armed or going off means the alert is always said;
* everyone out: always said;
* an entry door or gate that opened just *before* the person was seen: most likely someone from the house
  going out, so the alert is kept quiet (still on the timeline and phones);
* the same camera alerting again and again in half an hour with nothing else new: said once, then kept quiet;
* indoor movement or a Frigate event on that camera: recorded as supporting evidence.

It only ever decides whether to *speak*. The alert itself, the timeline entry and phone notifications are never
dropped, and nothing here claims who the person is or which way they went. Setting: "Weigh alerts against the
rest of the house" (Settings → Camera security), off by default.
"""
from __future__ import annotations

from typing import Any

CONF_SECURITY_REASONING = "security_reasoning"
EXIT_WINDOW = 180  # seconds: an entry opened this soon before the sighting suggests someone going out
REPEAT_WINDOW = 1800
REPEAT_QUIET_AFTER = 2  # alerts already said for this camera in the window before the rest are kept quiet
ARMED = {"armed_away", "armed_night", "armed_vacation", "armed_custom_bypass", "triggered", "pending", "arming"}


def weigh(*, reason: str, alarm_state: str | None, entries_opened: list[tuple[str, float]], indoor_activity: bool,
          everyone_away: bool, repeats: int, frigate_labels: list[str]) -> dict[str, Any]:
    """Return whether to say the alert, the reasons, and a short plain explanation."""
    reasons: list[str] = []
    notes: list[str] = []
    if alarm_state in ARMED:
        reasons.append("alarm_armed")
        notes.append(f"the alarm is {alarm_state.replace('_', ' ')}")
    if everyone_away:
        reasons.append("everyone_away")
        notes.append("everyone is out")
    exits = [(name, age) for name, age in entries_opened if 0 <= age <= EXIT_WINDOW]
    if exits:
        reasons.append("entry_opened_just_before")
        name, age = min(exits, key=lambda row: row[1])
        notes.append(f"{name} opened {max(1, round(age / 60))} minute{'s' if round(age / 60) > 1 else ''} before")
    if indoor_activity:
        reasons.append("indoor_activity")
        notes.append("there was movement inside")
    if frigate_labels:
        reasons.append("frigate_event")
        notes.append("Frigate reported " + ", ".join(sorted(set(frigate_labels))[:3]))
    if repeats >= REPEAT_QUIET_AFTER:
        reasons.append("repeated")
        notes.append(f"this camera has alerted {repeats} times in the last half hour")
    must_say = "alarm_armed" in reasons or "everyone_away" in reasons or reason == "away"
    likely_household = bool(exits) and reason in ("night", "loitering")
    announce = must_say or not (likely_household or "repeated" in reasons)
    if must_say:
        verdict = "said: " + ("the alarm is set" if "alarm_armed" in reasons else "everyone is out")
    elif likely_household:
        verdict = "kept quiet: probably someone from the house going out"
    elif "repeated" in reasons:
        verdict = "kept quiet: already said for this camera"
    else:
        verdict = "said"
    return {"announce": announce, "reasons": reasons, "verdict": verdict,
            "explanation": "; ".join(notes) if notes else "nothing else in the house to go on",
            "identity_confirmed": False, "route_confirmed": False}
