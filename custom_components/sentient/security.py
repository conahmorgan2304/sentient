"""Security check: what's open, unlocked or unarmed — read-only, never changes anything.

Used by the "is the house locked up?" voice tool, a briefing section, the goodnight-style
routine check and the Security card on the sidebar page. Alarms are only reported, never
armed or disarmed.
"""

from __future__ import annotations

from collections.abc import Callable
from typing import Any

from homeassistant.core import HomeAssistant

OPEN_CLASSES = {"door", "window", "garage_door", "opening"}


def check(hass: HomeAssistant, ignore: set[str] | None = None, *,
          visible: Callable[[str], bool] | None = None) -> dict[str, Any]:
    ignore = ignore or set()
    perimeter_classes = {"binary_sensor": OPEN_CLASSES, "cover": {"garage", "gate", "door"}}
    configured = [st for st in hass.states.async_all(("binary_sensor", "cover", "lock", "alarm_control_panel"))
                  if st.entity_id not in ignore and (st.domain in ("lock", "alarm_control_panel")
                      or st.attributes.get("device_class") in perimeter_classes.get(st.domain, ()))]
    relevant = configured if visible is None else [st for st in configured if visible(st.entity_id)]
    limited = len(relevant) != len(configured)
    current = [st for st in relevant if not st.attributes.get("restored")]
    open_items = [
        {"entity_id": st.entity_id, "name": st.name, "kind": st.attributes.get("device_class")}
        for st in current if (st.domain == "binary_sensor" and st.state == "on")
        or (st.domain == "cover" and st.state in ("open", "opening"))
    ]
    unlocked = [
        {"entity_id": st.entity_id, "name": st.name, "state": st.state}
        for st in current if st.domain == "lock" and st.state in ("unlocked", "open", "opening", "jammed")
    ]
    alarms = [
        {"entity_id": st.entity_id, "name": st.name, "state": "unknown" if st.attributes.get("restored") else st.state}
        for st in relevant if st.domain == "alarm_control_panel"
    ]
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
        if st.attributes.get("restored"):
            row.update(state="unknown", status="unknown", reason="Waiting for a current device report")
        elif st.state not in stable[st.domain]:
            row["status"] = "unknown"
            row["reason"] = "No settled security state is reported"
    unknown = [e for e in evidence if e["status"] == "unknown"]
    perimeter = [st for st in relevant if st.domain != "alarm_control_panel"]
    alarm_alert = any(a["state"] in ("triggered", "pending") for a in alarms)
    from .cognitive import assess_security

    assessment = assess_security(
        open_count=len(open_items),
        unlocked_count=len(unlocked),
        alarm_triggered=alarm_alert,
        unknown_count=len(unknown),
        perimeter_present=bool(perimeter),
        access_limited=limited,
    )
    uncertain = assessment.verdict == "unknown"
    if open_items or unlocked:
        text = summary(open_items, unlocked, alarms)
    elif uncertain:
        text = "I can't confirm the whole perimeter." + alarm_summary(alarms)
    else:
        closed = any(st.domain in perimeter_classes for st in perimeter)
        locked = any(st.domain == "lock" for st in perimeter)
        reports = []
        if closed:
            reports.append("the monitored openings report closed")
        if locked:
            reports.append("the monitored locks report locked")
        text = _join(reports).capitalize() + "." + alarm_summary(alarms)
    if unknown:
        text += " No settled reading from " + ", ".join(e.get("name", e["entity_id"]) for e in unknown) + "."
    if not perimeter:
        text += " No door, window, cover or lock readings are available."
    if not relevant:
        text = "No security sensors are available to confirm the house is secure."
    if limited:
        text += " Some security readings are outside this request's access."
    from .local_mind import explain_security_offline

    offline_report = {"summary": text, "assessment": assessment.as_dict()}
    return {
        "secure": assessment.secure,
        "assessment": assessment.as_dict(),
        "offline_explanation": explain_security_offline(offline_report),
        "unknown": unknown,
        "evidence": evidence,
        "open": open_items,
        "unlocked": unlocked,
        "alarms": alarms,
        "summary": text,
    }


async def check_for_voice(hass, context):
    from homeassistant.auth.permissions.const import POLICY_READ
    from homeassistant.components.homeassistant.exposed_entities import async_should_expose

    user_id = context.user_id if context else None
    user = await hass.auth.async_get_user(user_id) if user_id else None

    def visible(eid):
        if user_id and (not user or not user.is_active or not user.permissions.check_entity(eid, POLICY_READ)):
            return False
        try:
            return async_should_expose(hass, "conversation", eid)
        except KeyError:  # Assist exposure data is unavailable during startup
            return False

    return check(hass, visible=visible)


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
    text = "; ".join(p[0].upper() + p[1:] for p in parts) + "." if parts else ""
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
