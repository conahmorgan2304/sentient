"""Separate retained incident evidence from camera availability at a known time."""
from __future__ import annotations

from homeassistant.util import dt as dt_util

from .incidents import CONF_COVERAGE, configured_cameras
from .security_voice import allowed


async def readable_evidence(hass, context, evidence):
    visible = []
    for row in evidence:
        source = row.get("source")
        origin = row.get("read_source")
        if source and await allowed(hass, context, source) and (
            not origin or origin == source or await allowed(hass, context, origin)
        ):
            visible.append(row)
    return sorted(visible, key=lambda row: row["time"])


async def describe_coverage(hass, options, context, evidence):
    if not options.get(CONF_COVERAGE):
        return {}
    cameras = []
    observed = {r["source"] for r in evidence if r["kind"] in ("event", "detection", "clip", "visit")}
    for eid in sorted(configured_cameras(hass, options)):
        if not await allowed(hass, context, eid):
            continue
        camera = {"camera": eid, "linked_evidence": eid in observed}
        # Snapshots are attached to the readable observation that supplied the time.
        if snapshot := next((r for r in reversed(evidence) if eid in r.get("camera_gaps", {})), None):
            camera["during_incident"] = {"state": snapshot["camera_gaps"][eid], "observed_at": snapshot["time"]}
        cameras.append(camera)
    # Capture present availability together after permission checks, separately from historical observations.
    as_of = dt_util.utcnow().isoformat()
    for camera in cameras:
        state = hass.states.get(camera["camera"])
        camera["current_ha_report"] = (state.state if state and not state.attributes.get("restored") else "unknown")
    return {"route_confirmed": False, "cameras": cameras, "current_reports_as_of": as_of,
            "limits": "Report gaps and label current Home Assistant availability separately. "
                      "A camera with no linked evidence did not confirm the route. Missing evidence does not show that nothing happened. "
                      "A camera state does not verify recording or detector health. Nearby detections do not establish one person's identity."}
