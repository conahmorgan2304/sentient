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
        {"entity_id": st.entity_id, "name": st.name, "state": st.state}
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
    stable = {
        "lock": {"locked", "unlocked", "open", "opening", "jammed"},
        "cover": {"open", "closed", "opening"},
        "binary_sensor": {"on", "off"},
        "alarm_control_panel": {"disarmed", "triggered", "pending", "armed_home", "armed_away",
                                "armed_night", "armed_vacation", "armed_custom_bypass"},
    }
    for st, row in zip(relevant, evidence):
        if st.state not in stable[st.domain]:
            row["status"] = "unknown"
            row["reason"] = "No settled security state is reported"
    unknown = [e for e in evidence if e["status"] == "unknown"]
    perimeter = [st for st in relevant if st.domain != "alarm_control_panel"]
    alarm_alert = any(a["state"] in ("triggered", "pending") for a in alarms)
    problems = len(open_now) + len(covers) + len(unlocked) + int(alarm_alert)
    text = summary(open_now + covers, unlocked, alarms)
    if unknown or not perimeter:
        if open_now or covers or unlocked:
            # Keep known hazards visible instead of replacing them with a generic warning.
            text = summary(open_now + covers, unlocked, alarms)
        else:
            text = "I can't confirm everything is shut and locked." + alarm_summary(alarms)
        if unknown:
            text += " No settled reading from " + ", ".join(e.get("name", e["entity_id"]) for e in unknown) + "."
        if not perimeter:
            text += " No door, window, cover or lock readings are available."
    if not relevant:
        text = "No security sensors are available to confirm the house is secure."
    return {
        "secure": False if problems else None if unknown or not perimeter else True,
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
    for state in ("unlocked", "open", "opening", "jammed"):
        names = [i["name"] for i in unlocked if i.get("state", "unlocked") == state]
        if names:
            parts.append(f"{_join(names)} {'is' if len(names) == 1 else 'are'} {state}")
    if not parts:
        text = "Everything's shut" + (" and locked" if not unlocked and alarms is not None else "") + "."
    else:
        text = "; ".join(p[0].upper() + p[1:] for p in parts) + "."
    return text + alarm_summary(alarms)


def alarm_summary(alarms: list[dict]) -> str:
    parts = []
    for alarm in alarms:
        state = alarm["state"]
        label = "The alarm" if len(alarms) == 1 else alarm["name"]
        if state == "disarmed":
            status = "off"
        elif state.startswith("armed_"):
            status = "set (" + state.removeprefix("armed_").replace("_", " ") + ")"
        elif state in ("triggered", "pending", "arming", "disarming"):
            status = state
        else:
            status = "reporting an unknown state"
        parts.append(f"{label} is {status}.")
    return (" " + " ".join(parts)) if parts else ""
