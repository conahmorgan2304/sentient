"""Security check: what's open, unlocked or unarmed — read-only, never changes anything.

Used by the "is the house locked up?" voice tool, a briefing section, the goodnight-style
routine check and the Security card on the sidebar page. Alarms are only reported, never
armed or disarmed.
"""

from __future__ import annotations

from typing import Any

from homeassistant.core import HomeAssistant

OPEN_CLASSES = {"door", "window", "garage_door", "opening"}


def check(hass: HomeAssistant, ignore: set[str] | None = None) -> dict[str, Any]:
    ignore = ignore or set()
    open_now = [
        {"entity_id": st.entity_id, "name": st.name, "kind": st.attributes.get("device_class")}
        for st in hass.states.async_all("binary_sensor")
        if st.state == "on" and st.attributes.get("device_class") in OPEN_CLASSES and st.entity_id not in ignore
    ]
    covers = [
        {"entity_id": st.entity_id, "name": st.name, "kind": st.attributes.get("device_class")}
        for st in hass.states.async_all("cover")
        if st.state in ("open", "opening") and st.attributes.get("device_class") in ("garage", "gate", "door")
        and st.entity_id not in ignore
    ]
    unlocked = [
        {"entity_id": st.entity_id, "name": st.name}
        for st in hass.states.async_all("lock")
        if st.state in ("unlocked", "open", "opening", "jammed") and st.entity_id not in ignore
    ]
    alarms = [
        {"entity_id": st.entity_id, "name": st.name, "state": st.state}
        for st in hass.states.async_all("alarm_control_panel")
        if st.entity_id not in ignore
    ]
    relevant = [st for st in hass.states.async_all() if st.entity_id not in ignore and
                (st.domain in ("lock", "alarm_control_panel") or st.domain == "binary_sensor" and
                 st.attributes.get("device_class") in OPEN_CLASSES or st.domain == "cover" and
                 st.attributes.get("device_class") in ("garage", "gate", "door"))]
    from .evidence import describe_state
    evidence = [describe_state(hass, st.entity_id) for st in relevant]
    unknown = [e for e in evidence if e["status"] == "unknown"]
    problems = len(open_now) + len(covers) + len(unlocked)
    text = summary(open_now + covers, unlocked, alarms)
    if unknown:
        text = ("Some openings or locks are unsafe. " if problems else "I can't confirm everything is shut and locked. ") +                "No current reading from " + ", ".join(e.get("name", e["entity_id"]) for e in unknown) + "."
    if not relevant:
        text = "No security sensors are available to confirm the house is secure."
    return {
        "secure": False if problems else None if unknown or not relevant else True,
        "unknown": unknown,
        "evidence": evidence,
        "open": open_now + covers,
        "unlocked": unlocked,
        "alarms": alarms,
        "summary": text,
    }


def _join(names: list[str]) -> str:
    return names[0] if len(names) == 1 else ", ".join(names[:-1]) + " and " + names[-1]


def summary(open_items: list[dict], unlocked: list[dict], alarms: list[dict]) -> str:
    parts = []
    if open_items:
        names = [i["name"] for i in open_items]
        parts.append(f"{_join(names)} {'is' if len(names) == 1 else 'are'} open")
    if unlocked:
        names = [i["name"] for i in unlocked]
        parts.append(f"{_join(names)} {'is' if len(names) == 1 else 'are'} unlocked")
    if not parts:
        text = "Everything's shut" + (" and locked" if not unlocked and alarms is not None else "") + "."
    else:
        text = "; ".join(p[0].upper() + p[1:] for p in parts) + "."
    armed = [a for a in alarms if a["state"].startswith("armed")]
    if alarms:
        text += " The alarm is " + ("set (" + armed[0]["state"].replace("armed_", "").replace("_", " ") + ")." if armed else "off.")
    return text
