"""Guarded multi-step routines: plan, check, carry out, verify, and say what didn't happen.

A voice routine ("goodnight", "leaving") is a list of ordinary Home Assistant actions. Before it runs, each step
that switches something is read into a plan — which device should end up how — and checked: is the device
there, is it on the never-touch list. The routine then runs as before; afterwards each planned step is checked
against the device's real state for a few seconds, and the answer says what didn't happen ("Done, except the
landing light, which is still on"). The plan and its outcome go on the timeline as one decision.

Steps that can't be read into an expected state (a script, a scene, a notification) are carried out but reported
as "not checked", never as done. Nothing is retried or undone automatically.
Switch: "Check routines worked" (Settings → Voice), on by default.
"""
from __future__ import annotations

import asyncio
from typing import Any

from homeassistant.core import HomeAssistant

CONF_VERIFY_ROUTINES = "routine_verify"
VERIFY_SECONDS = 8
DEAD = ("unavailable", "unknown")

# (domain, service) -> the state the target should end in
EXPECT = {
    ("light", "turn_on"): "on", ("light", "turn_off"): "off",
    ("switch", "turn_on"): "on", ("switch", "turn_off"): "off",
    ("fan", "turn_on"): "on", ("fan", "turn_off"): "off",
    ("input_boolean", "turn_on"): "on", ("input_boolean", "turn_off"): "off",
    ("homeassistant", "turn_on"): "on", ("homeassistant", "turn_off"): "off",
    ("lock", "lock"): "locked", ("lock", "unlock"): "unlocked",
    ("cover", "open_cover"): "open", ("cover", "close_cover"): "closed",
    ("media_player", "turn_off"): "off", ("media_player", "media_pause"): "paused",
    ("climate", "turn_off"): "off", ("vacuum", "start"): "cleaning", ("vacuum", "return_to_base"): "returning",
}
SETTLED = {"returning": ("returning", "docked"), "cleaning": ("cleaning",)}


def _targets(step: dict[str, Any]) -> list[str]:
    found: list[str] = []
    for where in (step.get("target") or {}, step.get("data") or {}, step):
        ids = where.get("entity_id") if isinstance(where, dict) else None
        if isinstance(ids, str):
            found += [i.strip() for i in ids.split(",") if i.strip()]
        elif isinstance(ids, list):
            found += [i for i in ids if isinstance(i, str)]
    return list(dict.fromkeys(found))


def plan(hass: HomeAssistant, actions: list[dict[str, Any]]) -> dict[str, Any]:
    """What the routine should leave each device as, and what can't be checked."""
    steps, unchecked, missing = [], 0, []
    # A Home Assistant script may stop before later steps when a top-level
    # condition fails. Final-state matching alone cannot establish execution.
    conditional = any(isinstance(step, dict) and any(k in step for k in ("condition", "choose", "if", "repeat"))
                      for step in (actions or []))
    for step in actions or []:
        if not isinstance(step, dict):
            unchecked += 1
            continue
        service = step.get("action") or step.get("service")
        if not isinstance(service, str) or "." not in service:
            unchecked += 1  # a delay, a condition, a choose: carried out, not checked
            continue
        domain, name = service.split(".", 1)
        targets = _targets(step)
        expected = None
        for entity_id in targets:
            dom = entity_id.split(".", 1)[0]
            if domain == "homeassistant":
                expected = EXPECT.get((dom, name)) or EXPECT.get((domain, name))
            elif domain == dom:
                expected = EXPECT.get((domain, name))
            else:
                expected = None
            if expected is None:
                unchecked += 1
                continue
            if hass.states.get(entity_id) is None or hass.states.get(entity_id).state in DEAD:
                missing.append(entity_id)
            steps.append({"entity_id": entity_id, "expect": expected, "service": service,
                          "before": getattr(hass.states.get(entity_id), "state", None)})
        if not targets:
            unchecked += 1
    return {"steps": steps, "unchecked": unchecked, "unavailable": missing,
            "conditional": conditional}


def _reached(state: str | None, expected: str) -> bool:
    return state in SETTLED.get(expected, (expected,))


async def async_verify(hass: HomeAssistant, planned: dict[str, Any], seconds: float | None = None) -> dict[str, Any]:
    """Wait a little for each planned device to reach its state; what did and didn't."""
    seconds = VERIFY_SECONDS if seconds is None else seconds
    steps = planned.get("steps") or []
    deadline = asyncio.get_running_loop().time() + seconds
    while True:
        pending = [s for s in steps if not _reached(getattr(hass.states.get(s["entity_id"]), "state", None), s["expect"])]
        if not pending or asyncio.get_running_loop().time() >= deadline:
            break
        await asyncio.sleep(0.5)
    already = [s for s in steps if s not in pending and _reached(s.get("before"), s["expect"])]
    # Reaching a final state does not prove a conditional branch executed.
    # Never count an already-satisfied device as an action that was performed.
    done = [s for s in steps if s not in pending and s not in already]
    unverified = [s["entity_id"] for s in done] if planned.get("conditional") else []
    if planned.get("conditional"):
        done = []  # state changed, but no execution trace proves which branch ran
    return {"done": [s["entity_id"] for s in done],
            "unverified": unverified,
            "already_satisfied": [s["entity_id"] for s in already],
            "not_done": [{"entity_id": s["entity_id"], "expect": s["expect"],
                          "now": getattr(hass.states.get(s["entity_id"]), "state", None)} for s in pending],
            "unchecked": planned.get("unchecked", 0) + len(unverified)}


def describe(hass: HomeAssistant, outcome: dict[str, Any]) -> str:
    """'Done, except the landing light, which is still on.' — or '' when everything checked happened."""
    misses = outcome.get("not_done") or []
    if not misses:
        return ""
    parts = []
    for miss in misses[:4]:
        st = hass.states.get(miss["entity_id"])
        name = st.name if st is not None else miss["entity_id"]
        now = miss.get("now")
        if now in DEAD or now is None:
            parts.append(f"the {name.lower()} didn't respond")
        else:
            parts.append(f"the {name.lower()} is still {now.replace('_', ' ')}")
    more = len(misses) - len(parts)
    text = ", ".join(parts[:-1]) + (" and " if len(parts) > 1 else "") + parts[-1]
    return f"Done, except {text}" + (f" (and {more} more)" if more > 0 else "") + "."
