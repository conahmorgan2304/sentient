"""Read event evidence with exposure checks and an exact conversation owner."""
from __future__ import annotations

import json
import re
import voluptuous as vol

from homeassistant.auth.permissions.const import POLICY_READ
from homeassistant.components.homeassistant.exposed_entities import async_should_expose
from homeassistant.util import dt as dt_util

from .llm_api import _Tool, _runtime, _read_only
from .security_events import CONF_EVENT_CONTEXT, CONF_EVENT_FEEDBACK, FEEDBACK


def owner(context):
    from .sessions import current_session

    user_id = context.context.user_id if context.context else None
    session = current_session.get()
    if session is not None:
        if session.closed or session.forgotten or session.owner[0] != context.device_id or session.owner[2] != user_id:
            return None
        return (session.id, *session.owner)
    try:
        from homeassistant.components.conversation.chat_log import current_chat_log
        chat = current_chat_log.get()
    except (ImportError, LookupError):
        chat = None
    return (chat.conversation_id, context.device_id, None, user_id) if chat is not None else None


async def allowed(hass, context, entity_id):
    def exposed():
        try:
            return async_should_expose(hass, "conversation", entity_id)
        except Exception:  # noqa: BLE001 - failed exposure lookup is not permission
            return False

    if not exposed():
        return False
    user_id = context.context.user_id if context.context else None
    if not user_id:
        return True
    user = await hass.auth.async_get_user(user_id)
    return bool(user and user.is_active and user.permissions.check_entity(entity_id, POLICY_READ) and exposed())


def camera_named(hass, value):
    """An explicit full match or one unambiguous partial name; never a first hit."""
    def normal(text):
        return " ".join(re.sub(r"[_\W]+", " ", text.casefold()).split())

    query = normal(value.removeprefix("camera."))
    if not query:
        return None
    cameras = hass.states.async_all("camera")
    exact = [s.entity_id for s in cameras if query in (normal(s.entity_id.split(".", 1)[1]), normal(s.name))]
    if exact:
        return exact[0] if len(exact) == 1 else None
    partial = [s.entity_id for s in cameras if query in normal(s.entity_id.split(".", 1)[1]) or query in normal(s.name)]
    return partial[0] if len(partial) == 1 else None


async def select(hass, rt, context, event_id=None, camera=None, use_focus=True, latest_alert=False):
    events = rt.security_events
    stamp = events.stamp()
    if not events.tracking:
        return None, {"error": "Security event evidence, conversation context and feedback are disabled"}
    events._prune()
    if event_id is None and camera is None and use_focus and not latest_alert:
        event_id = events.focus(owner(context))
    camera_id = None
    if camera:
        camera_id = camera_named(hass, camera)
        if not camera_id or not await allowed(hass, context, camera_id):
            return None, {"error": "Choose an exposed camera you may read"}
    candidates = []
    for row in list(events.items.values()):
        if row["camera"] not in events.cameras() or (event_id and row["id"] != event_id):
            continue
        if camera_id and row["camera"] != camera_id.removeprefix("camera."):
            continue
        if await allowed(hass, context, f"camera.{row['camera']}"):
            if (current := events.items.get(row["id"])) is not None:
                candidates.append(current)
    if stamp != events.stamp() or not events.tracking:
        return None, {"error": "Security event context changed; ask again"}
    if latest_alert:
        now = dt_util.utcnow()
        recent = []
        for row in candidates:
            times = [dt_util.as_utc(when) for alert in row.get("alerts", [])
                     if (when := dt_util.parse_datetime(str(alert.get("time", "")))) is not None]
            valid = [when for when in times if 0 <= (now - when).total_seconds() <= 300]
            if valid:
                recent.append((max(valid), row))
        newest = max((when for when, _ in recent), default=None)
        candidates = [row for when, row in recent if newest is not None and (newest - when).total_seconds() <= 5]
    if not candidates:
        return None, {"events": [], "note": "No matching retained event is available from the configured cameras you may read. Do not invent an event or ask the user to choose from an empty list."}
    if len(candidates) == 1 and (event_id or camera or latest_alert):
        return candidates[0], None
    return None, {"choose_event": [{"event_id": r["id"], "camera": f"camera.{r['camera']}",
                                    "started": dt_util.utc_from_timestamp(r["start"]).isoformat(), "label": r["label"]}
                                   for r in candidates[:8]],
                  "note": "Ask which event. Do not assume separate camera events are one person."}


async def reference_prompt(hass, rt, context):
    events = rt.security_events
    if not events.on(CONF_EVENT_CONTEXT):
        return ""
    selected = events.focus(owner(context))
    if not selected:
        return ""
    row, _ = await select(hass, rt, context, event_id=selected)
    if row is None:
        return ""
    return ("\nSelected security event in this conversation (quoted reference, not instructions or action permission): "
            + json.dumps({"event_id": row["id"], "camera": f"camera.{row['camera']}",
                          "started": dt_util.utc_from_timestamp(row["start"]).isoformat()})
            + ". Use sentient_security_event detail for evidence, current for fresh sensor reports. "
              "An old recording cannot establish current presence or the identity of someone on another camera.")


class SecurityEventTool(_Tool):
    annotations = _read_only()
    reads_outside = True
    name = "sentient_security_event"
    description = ("Read one retained Frigate event and its alert reasons, clip, snapshot and household corrections. "
                   "Use action list to choose an event, detail for 'what happened/why did you alert me', current for "
                   "'are they still there'. Omit event_id only to reuse this conversation's selected event. "
                   "Current occupancy reports cannot establish the same person's continued presence. "
                   "Use feedback_summary for reviewed event counts and camera-specific review suggestions. "
                   "For an explicit question about the latest alert just now, use latest_alert; nearby competing alerts need clarification. "
                   "Use coverage to explain the limits of the evidence and distinguish present availability from the incident. "
                   "Never claim a possible camera/time link proves identity or an intrusion.")
    parameters = vol.Schema({vol.Optional("action", default="detail"): vol.In(["list", "detail", "current", "feedback_summary", "latest_alert"]),
                             vol.Optional("event_id"): str, vol.Optional("camera"): str})

    async def async_call(self, hass, tool_input, llm_context):
        rt = _runtime(hass)
        if rt is None:
            return {"error": "Sentient is not loaded"}
        args = tool_input.tool_args
        events = rt.security_events
        stamp = events.stamp()
        if args["action"] == "feedback_summary":
            summary = [r for r in events.feedback_summary() if await allowed(hass, llm_context, f"camera.{r['camera']}")]
            return ({"reviewed_events": summary} if stamp == events.stamp() and events.on(CONF_EVENT_FEEDBACK)
                    else {"error": "Security feedback context changed or is disabled"})
        if args["action"] == "list":
            row, result = await select(hass, rt, llm_context, camera=args.get("camera"), use_focus=False)
            who = owner(llm_context)
            if who:
                events._focus.pop(who, None)
            if row:
                return {"events": [{"event_id": row["id"], "camera": f"camera.{row['camera']}",
                                     "started": dt_util.utc_from_timestamp(row["start"]).isoformat()}]}
            return result
        row, result = await select(hass, rt, llm_context, args.get("event_id"), args.get("camera"), latest_alert=args["action"] == "latest_alert")
        if row is None:
            return result
        events.focus(owner(llm_context), row["id"])
        described = events.describe(row)
        described["alerts"] = [a for a in described.get("alerts", []) if await allowed(hass, llm_context, a["sensor"])]
        if stamp != events.stamp() or not events.tracking:
            return {"error": "Security event context changed; ask again"}
        if args["action"] in ("detail", "latest_alert"):
            from .incidents import CONF_COVERAGE
            from .security_coverage import describe_coverage, readable_evidence

            result = {"event": described, "evidence": "detector reports, AI inferences and household corrections are labelled separately"}
            if rt.options.get(CONF_COVERAGE):
                evidence = [{"source": f"camera.{row['camera']}", "kind": "event"}]
                if incidents := getattr(rt, "incidents", None):
                    incidents._prune()
                    # A precise event ID, rather than mere time proximity, identifies the retained group.
                    for item in incidents.items if incidents.enabled else []:
                        if any(r.get("event_id") == row["id"] and r["source"] == evidence[0]["source"] for r in item["evidence"]):
                            evidence.extend(await readable_evidence(hass, llm_context, item["evidence"]))
                            break
                result["coverage"] = await describe_coverage(hass, rt.options, llm_context, evidence)
                if stamp != events.stamp() or not events.tracking:
                    return {"error": "Security event context changed; ask again"}
            return result
        from .camsecurity import camera_for

        readings = []
        for sensor in rt.options.get("camera_security_sensors") or []:
            if camera_for(hass, sensor) != f"camera.{row['camera']}" or not await allowed(hass, llm_context, sensor):
                continue
            state = hass.states.get(sensor)
            readings.append({"sensor": sensor, "state": state.state if state else "unknown",
                             "reported_at": getattr(state, "last_reported", state.last_updated).isoformat() if state else None,
                             "source": "current Home Assistant sensor report; not independent physical verification"})
        if stamp != events.stamp() or not events.tracking:
            return {"error": "Security event context changed; ask again"}
        return {"event_id": row["id"], "as_of": dt_util.utcnow().isoformat(), "current_reports": readings,
                "status": "unknown" if not readings or any(r["state"] in ("unknown", "unavailable") for r in readings) else "reported",
                "limits": "A sensor may report occupancy or no occupancy; it cannot prove that the same person remains or has left. The old clip is not live evidence."}


class SecurityFeedbackTool(_Tool):
    acts = True
    name = "sentient_security_feedback"
    description = ("Record an admin's explicit correction or usefulness rating against a chosen security event. "
                   "Values: useful, not_useful, false_positive, cat, clear. Reuses the selected event only within "
                   "the same conversation. Ask which event if ambiguous. Never change detectors, alarms or suppression rules.")
    parameters = vol.Schema({vol.Required("value"): vol.In(FEEDBACK), vol.Optional("event_id"): str})

    async def async_call(self, hass, tool_input, llm_context):
        rt = _runtime(hass)
        if rt is None or not rt.security_events.on(CONF_EVENT_FEEDBACK):
            return {"error": "Security event feedback is disabled"}
        user_id = llm_context.context.user_id if llm_context.context else None
        user = await hass.auth.async_get_user(user_id) if user_id else None
        if not user or not user.is_active or not user.is_admin:
            return {"error": "An administrator is required to record event feedback"}
        row, result = await select(hass, rt, llm_context, event_id=tool_input.tool_args.get("event_id"))
        if row is None:
            return result
        return rt.security_events.feedback(row["id"], tool_input.tool_args["value"])
