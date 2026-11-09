"""Settings that point at things that aren't there.

Sentient's settings name lots of entities: the doorbell, the cameras' person sensors, the speakers,
the phone, the AI. If one is deleted or has been dead for a while, the feature that relies on it
quietly stops. This finds them so House health can say so, in words that name the setting.
"""

from __future__ import annotations

from datetime import timedelta
import re
from typing import Any

from homeassistant.const import STATE_UNAVAILABLE
from homeassistant.core import HomeAssistant
from homeassistant.util import dt as dt_util

from .const import (
    CONF_AI_TASK_ENTITY,
    CONF_BINS_CALENDAR,
    CONF_BUSY_ENTITIES,
    CONF_CAMSEC_ENTRIES,
    CONF_CAMSEC_INSIDE,
    CONF_CAMSEC_SENSORS,
    CONF_CHAT_AGENT,
    CONF_CLIP_CAMERAS,
    CONF_DEFAULT_SPEAKER,
    CONF_DOOR_CAMERA,
    CONF_DOOR_TRIGGERS,
    CONF_DOORSTEP_SENSORS,
    CONF_DRIVE_CAMERA,
    CONF_DRIVE_TRIGGERS,
    CONF_FACE_SENSOR,
    CONF_FALLBACK_SPEAKERS,
    CONF_HEALTH_IGNORE,
    CONF_INSIDE_SENSORS,
    CONF_NIGHT_WATCH_SPEAKERS,
    CONF_NOTIFY_TARGETS,
    CONF_PHONE_APPS,
    CONF_SPEAKERS,
    CONF_TTS_ENTITY,
    CONF_VOICE_AGENT,
    CONF_ZONE_DOOR_SENSORS,
)

ENTITY_ID = re.compile(r"^[a-z_]+\.[a-z0-9_]+$")
DEAD_FOR = timedelta(hours=1)  # unavailable this long (a blip or a restart isn't news)

# what each setting is called, for the sentence; the ones a feature stops working without
LABELS: dict[str, str] = {
    CONF_DOOR_CAMERA: "door camera", CONF_DOOR_TRIGGERS: "doorbell", CONF_DOORSTEP_SENSORS: "doorstep sensors",
    CONF_ZONE_DOOR_SENSORS: "front door sensor", CONF_DRIVE_CAMERA: "drive camera", CONF_DRIVE_TRIGGERS: "drive sensors",
    CONF_FACE_SENSOR: "face sensor", CONF_CLIP_CAMERAS: "clip cameras", CONF_CAMSEC_SENSORS: "camera security sensors",
    CONF_CAMSEC_INSIDE: "camera security inside sensors", CONF_CAMSEC_ENTRIES: "camera security entries",
    CONF_SPEAKERS: "speakers", CONF_FALLBACK_SPEAKERS: "backup speakers", CONF_DEFAULT_SPEAKER: "default speaker",
    CONF_NIGHT_WATCH_SPEAKERS: "night-watch speakers", CONF_NOTIFY_TARGETS: "phone notifications",
    CONF_PHONE_APPS: "phone app", CONF_AI_TASK_ENTITY: "AI task", CONF_VOICE_AGENT: "voice agent",
    CONF_CHAT_AGENT: "chat agent", CONF_TTS_ENTITY: "voice (TTS)", CONF_BINS_CALENDAR: "bins calendar",
    CONF_INSIDE_SENSORS: "inside sensors", CONF_BUSY_ENTITIES: "busy signal",
}


def _ids(value: Any) -> list[str]:
    items = [value] if isinstance(value, str) else list(value) if isinstance(value, (list, tuple, set)) else []
    return [i for i in items if isinstance(i, str) and ENTITY_ID.match(i)]


def broken(hass: HomeAssistant, options: dict[str, Any]) -> list[dict[str, Any]]:
    """Chosen entities that no longer exist, or that have been unavailable for a while."""
    ignore = set(options.get(CONF_HEALTH_IGNORE) or [])
    now = dt_util.utcnow()
    out: list[dict[str, Any]] = []
    seen: set[tuple[str, str]] = set()
    for key, label in LABELS.items():
        for eid in _ids(options.get(key)):
            if eid in ignore or (key, eid) in seen:
                continue
            seen.add((key, eid))
            domain, object_id = eid.split(".", 1)
            st = hass.states.get(eid)
            if st is None:
                if domain == "notify" and hass.services.has_service("notify", object_id):
                    continue  # an old-style notify action, not an entity
                out.append({"kind": "wiring", "name": eid, "entity_id": eid, "setting": label, "gone": True})
            elif st.state == STATE_UNAVAILABLE and now - st.last_changed > DEAD_FOR:
                out.append({"kind": "wiring", "name": st.name or eid, "entity_id": eid, "setting": label,
                            "gone": False, "since": st.last_changed.isoformat()})
    return out
