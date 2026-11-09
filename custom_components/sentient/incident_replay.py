"""Permission-checked replay of the observations already retained by Sentient."""
from __future__ import annotations

import re
from urllib.parse import urlencode

from homeassistant.auth.permissions.const import POLICY_READ
from homeassistant.exceptions import HomeAssistantError
from homeassistant.helpers.network import NoURLAvailableError, get_url
from homeassistant.util import dt as dt_util

from .incidents import CONF_COVERAGE, configured_cameras
from .security_events import CONF_EVENT_EVIDENCE, safe_id

CONF_REPLAY = "incident_replay"
CONF_REPLAY_IMAGE = "incident_replay_image"
CONF_REPLAY_POINTS = "incident_replay_points"
CONF_REPLAY_TABLET = "incident_replay_tablet"
REPLAY_REQUEST = re.compile(r"^show me (?:the (?:latest )?(?:security )?incident|what happened)(?: (?:at|in|by|near) (.+?))?[.!?]*$", re.I)
LOCAL_IMAGE = re.compile(r"/local/(?:[a-z0-9_-]+/)*[a-z0-9_.-]+\.(?:png|webp|jpe?g)\Z", re.I)


def replay_match(options, text):
    return REPLAY_REQUEST.fullmatch(text.strip()) if options.get(CONF_REPLAY) else None


def replay_data(hass, rt, user, incident_id=None, query=""):
    """Return a list of incident choices and one selected replay for the signed-in viewer."""
    opts = rt.options
    if not opts.get(CONF_REPLAY) or not rt.incidents.enabled or rt.speech.guest_mode:
        return {"enabled": False}
    cameras = configured_cameras(hass, opts)
    permitted = user and user.is_active
    query = query.strip().casefold()
    if query.startswith("the "):
        query = query[4:]

    def can_read(eid):
        return permitted and user.permissions.check_entity(eid, POLICY_READ)

    def name(eid):
        return state.name if (state := hass.states.get(eid)) else eid.split(".", 1)[-1].replace("_", " ").title()

    rt.incidents._prune()
    choices = []
    selected = None
    for item in rt.incidents.items:
        visible = sorted((row for row in item["evidence"] if can_read(row["source"])
                          and (not row.get("read_source") or can_read(row["read_source"]))), key=lambda row: row["time"])
        if not visible:
            continue
        if query and not any(query in f"{r['source']} {name(r['source'])} {r['text']}".casefold() for r in visible):
            continue
        start = visible[0]["time"]
        entry = {"id": item["id"], "start": start,
                 "label": " · ".join(dict.fromkeys(name(r["source"]) for r in visible))[:180]}
        if len(choices) < 8:
            choices.append(entry)
        if (not incident_id and selected is None) or item["id"] == incident_id:
            started = dt_util.parse_datetime(start)
            steps = []
            for row in visible:
                source = row["source"]
                step = {"time": row["time"], "offset": (dt_util.parse_datetime(row["time"]) - started).total_seconds(),
                        "source": source, "name": name(source), "kind": row["kind"], "status": row["status"], "text": row["text"]}
                if opts.get(CONF_COVERAGE):
                    step["gaps"] = [{"entity": camera, "name": name(camera), "state": state}
                                    for camera, state in row.get("camera_gaps", {}).items()
                                    if camera in cameras and can_read(camera)]
                event_id = row.get("event_id")
                event = rt.security_events.items.get(event_id) if safe_id(event_id) and rt.security_events.on(CONF_EVENT_EVIDENCE) else None
                if event and source == f"camera.{event['camera']}" and source in cameras:
                    root = f"/api/frigate/notifications/{event_id}"
                    if event.get("has_snapshot"):
                        step["snapshot"] = f"{root}/thumbnail.jpg"
                    if event.get("has_clip"):
                        step["clip"] = f"{root}/clip.mp4"
                    if summary := event.get("analysis", {}).get("summary"):
                        step["analysis"] = summary
                steps.append(step)
            selected = {**entry, "steps": steps}
        if len(choices) == 8 and selected is not None:
            break

    image = opts.get(CONF_REPLAY_IMAGE) or ""
    points = {}
    raw_points = opts.get(CONF_REPLAY_POINTS)
    if isinstance(raw_points, dict):
        for eid, xy in raw_points.items():
            if (isinstance(eid, str) and isinstance(xy, (list, tuple)) and len(xy) == 2
                    and all(type(n) in (int, float) and 0 <= n <= 100 for n in xy)
                    and can_read(eid)):
                points[eid] = list(xy)
    return {"enabled": True, "incidents": choices, "selected": selected,
            "image": image if isinstance(image, str) and LOCAL_IMAGE.fullmatch(image) else None,
            "points": points}


async def show_on_tablet(hass, rt, context, query):
    if rt.speech.guest_mode:
        return "Security replay is unavailable in guest mode."
    user = await hass.auth.async_get_user(context.user_id) if context.user_id else None
    if not user or not user.is_active or not user.is_admin or user.system_generated:
        return "Opening security replay on the tablet needs an identified Home Assistant administrator."
    selected = replay_data(hass, rt, user, query=query).get("selected")
    if not selected:
        return "I couldn't find a matching retained security incident."
    device_id = rt.options.get(CONF_REPLAY_TABLET)
    if not device_id or not hass.services.has_service("fully_kiosk", "load_url"):
        return "I found an incident. Open Security, then Incident replay. A Fully Kiosk tablet can be selected in Sentient settings."
    try:
        url = get_url(hass, prefer_external=False)
        await hass.services.async_call("fully_kiosk", "load_url", {
            "device_id": device_id, "url": f"{url}/sentient/security?{urlencode({'incident': selected['id']})}",
        }, blocking=True, context=context)
    except (HomeAssistantError, NoURLAvailableError):
        return "I found the incident, but couldn't open the tablet. You can open it under Security, then Incident replay."
    return "I sent the incident replay to the tablet."
