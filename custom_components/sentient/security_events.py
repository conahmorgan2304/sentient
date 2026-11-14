"""Optional Frigate event evidence, corrections and short-lived conversation references."""
from __future__ import annotations

import asyncio
from copy import deepcopy
from datetime import timedelta
import json
import logging
from pathlib import Path
import re
import uuid

from homeassistant.helpers.aiohttp_client import async_get_clientsession
from homeassistant.core import callback
from homeassistant.helpers.storage import Store
from homeassistant.helpers.event import async_track_time_interval
from homeassistant.util import dt as dt_util

from .clipwatch import frigate_prefix
from .faces import frigate_url
from .media import read_media
from .numeric import finite_float

CONF_EVENT_EVIDENCE = "security_event_evidence"
CONF_EVENT_CONTEXT = "security_conversation_context"
CONF_EVENT_FEEDBACK = "security_event_feedback"
CONF_EVENT_ZONES = "security_event_zones"  # "zones": only what entered a zone on a camera that has zones; "all"
CONF_HIDDEN_ZONES = "security_event_hidden_zones"  # "camera/zone": movement only there isn't listed
ZONE_MODES = ("zones", "all")
VISIT_GAP = 60  # seconds between events on one camera that still count as the same visit
HIDE_ZONE_AFTER = 3  # "not a person" corrections in one zone before suggesting it's hidden
NOT_PERSON = ("false_positive", "cat")
FEEDBACK = ("useful", "not_useful", "false_positive", "cat", "clear")
SAFE_ID = re.compile(r"[A-Za-z0-9_-][A-Za-z0-9_.-]{0,159}\Z")
_LOGGER = logging.getLogger(__name__)


def safe_id(value):
    return isinstance(value, str) and bool(SAFE_ID.fullmatch(value)) and ".." not in value


def before(value, since):
    when = dt_util.parse_datetime(str(value or ""))
    return when is not None and dt_util.as_utc(when) < since


def normal_event(data):
    if not isinstance(data, dict) or not safe_id(data.get("id")) or not safe_id(data.get("camera")):
        return None
    start = finite_float(data.get("start_time"))
    end = finite_float(data.get("end_time")) if data.get("end_time") else None
    if start is None or start <= 0 or (data.get("end_time") and end is None) or (end is not None and end < start):
        return None
    try:
        when = dt_util.utc_from_timestamp(start)
        if end is not None:
            dt_util.utc_from_timestamp(end)
    except (OverflowError, ValueError, OSError):
        return None
    if when < dt_util.utcnow() - timedelta(days=7) or when > dt_util.utcnow() + timedelta(seconds=30):
        return None
    zones = data.get("entered_zones") or data.get("zones") or []
    return {"id": data["id"], "camera": data["camera"], "start": start, "end": end,
            "label": str(data.get("label") or "unknown")[:60],
            "zones": [z[:100] for z in zones[:12] if isinstance(z, str)] if isinstance(zones, list) else [],
            "has_snapshot": data.get("has_snapshot") is True, "has_clip": data.get("has_clip") is True}


PARKABLE = {"car", "bicycle", "motorcycle", "boat", "truck", "bus", "van"}


class SecurityEvents:
    def __init__(self, hass, entry_id, options, runtime):
        self.hass, self.options, self.runtime = hass, options, runtime
        self._store = Store(hass, 1, f"sentient.{entry_id}.security_events")
        self.items = {}
        self._focus = {}
        self._generation = 0
        self._stopped = False
        self._unsub = None
        self._subscribing = None
        self._retention_unsub = None
        self._zoned = set()  # cameras Frigate says have zones

    def on(self, key):
        return (not self._stopped and bool(self.options.get(key))
                and not getattr(getattr(self.runtime(), "speech", None), "guest_mode", False))

    @property
    def tracking(self):
        return any(self.on(k) for k in (CONF_EVENT_EVIDENCE, CONF_EVENT_CONTEXT, CONF_EVENT_FEEDBACK))

    def stamp(self):
        rt = self.runtime()
        speech = getattr(rt, "speech", None)
        return (self._generation, id(self.options), getattr(speech, "_privacy_generation", 0),
                getattr(speech, "_guest_generation", 0), getattr(speech, "guest_mode", False))

    def invalidate(self):
        self._generation += 1
        self._focus.clear()

    async def async_start(self):
        self._stopped = False
        stored = await self._store.async_load() or {}
        for row in stored.get("items", []) if isinstance(stored, dict) else []:
            if not isinstance(row, dict):
                continue
            event = normal_event({"id": row.get("id"), "camera": row.get("camera"), "start_time": row.get("start"),
                                  "end_time": row.get("end"), "label": row.get("label"), "zones": row.get("zones"),
                                  "has_snapshot": row.get("has_snapshot"), "has_clip": row.get("has_clip")})
            if event:
                if row.get("vehicle_moved") is True:
                    event["vehicle_moved"] = True
                if row.get("parked_confirmed") is True:
                    event.update(parked=True, parked_confirmed=True)
                alerts = row.get("alerts")
                if isinstance(alerts, list):
                    event["alerts"] = [deepcopy(a) for a in alerts[-10:] if isinstance(a, dict)
                                       and dt_util.parse_datetime(str(a.get("time", ""))) is not None
                                       and all(isinstance(a.get(k), str) for k in ("sensor", "reason", "text", "link"))]
                for key, field in (("analysis", "summary"), ("feedback", "value")):
                    value = row.get(key)
                    if (isinstance(value, dict) and isinstance(value.get(field), str)
                            and dt_util.parse_datetime(str(value.get("at", ""))) is not None
                            and (key != "feedback" or value[field] in FEEDBACK)):
                        event[key] = deepcopy(value)
                self.items[event["id"]] = event
        if self._prune():
            self._save()
        self.configure(self.options)
        if self.tracking:
            self.hass.async_create_background_task(self._async_zoned(), "sentient frigate zones", eager_start=False)

    async def _async_zoned(self):
        """Which cameras have zones in Frigate's own settings; unknown leaves every event listed."""
        url = frigate_url(self.hass)
        if not url or self._stopped:
            return
        try:
            async with asyncio.timeout(5):
                response = await async_get_clientsession(self.hass).get(f"{url}/api/config")
                config = await response.json() if response.status == 200 else None
        except Exception:  # noqa: BLE001 - without it, a camera counts as zoned once an event enters a zone
            return
        cameras = config.get("cameras") if isinstance(config, dict) else None
        if isinstance(cameras, dict):
            self._zoned = {name for name, cam in cameras.items()
                           if isinstance(cam, dict) and isinstance(cam.get("zones"), dict) and cam["zones"]}

    def hidden_zones(self, camera):
        return {z.split("/", 1)[1] for z in self.options.get(CONF_HIDDEN_ZONES) or []
                if isinstance(z, str) and "/" in z and z.split("/", 1)[0] == camera}

    def zoned(self, camera):
        return camera in self._zoned or any(r["camera"] == camera and r.get("zones") for r in self.items.values())

    def in_scope(self, row):
        """Whether an event belongs on the list: in a zone that isn't hidden, unless the camera has no zones."""
        hidden = self.hidden_zones(row["camera"])
        zones = [z for z in row.get("zones") or [] if z not in hidden]
        if row.get("zones") and not zones:
            return False  # only ever in zones the household hid
        if self.options.get(CONF_EVENT_ZONES, "zones") == "all":
            return True
        return bool(zones) or not self.zoned(row["camera"])

    def configure(self, options):
        self.options = options
        self.invalidate()
        if self._subscribing is not None:
            self._subscribing.cancel()
            self._subscribing = None
        if not self._stopped and (self.items or self.tracking) and self._retention_unsub is None:
            self._retention_unsub = async_track_time_interval(self.hass, self._expire, timedelta(hours=6))
        elif self._stopped and self._retention_unsub:
            self._retention_unsub()
            self._retention_unsub = None
        if not self.tracking:
            if self._unsub:
                self._unsub()
                self._unsub = None
        elif self._unsub is None and self._subscribing is None and self.hass.config_entries.async_entries("mqtt"):
            self._subscribing = self.hass.async_create_background_task(self._subscribe(self.stamp()), "sentient security event evidence", eager_start=False)

    async def _subscribe(self, stamp):
        try:
            from homeassistant.components import mqtt
            if not await mqtt.async_wait_for_mqtt_client(self.hass) or not self.tracking or stamp != self.stamp():
                return
            unsub = await mqtt.async_subscribe(self.hass, f"{frigate_prefix(self.hass)}/events", self._message)
            if not self.tracking or stamp != self.stamp():
                unsub()
                return
            self._unsub = unsub
        except Exception as err:  # noqa: BLE001 - local REST matching remains available
            _LOGGER.debug("Security event subscription unavailable: %s", err)
        finally:
            if self._subscribing is asyncio.current_task():
                self._subscribing = None

    def async_stop(self):
        self._stopped = True
        self.configure(self.options)

    def cameras(self):
        from .incidents import configured_cameras

        return {camera.removeprefix("camera.") for camera in configured_cameras(self.hass, self.options)}

    def _prune(self):
        before = len(self.items)
        cutoff = (dt_util.utcnow() - timedelta(days=7)).timestamp()
        rows = sorted((r for r in self.items.values() if r["start"] >= cutoff), key=lambda r: r["start"], reverse=True)[:200]
        self.items = {r["id"]: r for r in rows}
        return len(self.items) != before

    @callback
    def _expire(self, _now):
        if self._prune():
            self._save()
        if self.tracking:
            self.hass.async_create_background_task(self._async_zoned(), "sentient frigate zones", eager_start=False)

    def _save(self):
        self._prune()
        self._store.async_delay_save(lambda: {"items": list(self.items.values())}, 5)

    @callback
    def _message(self, message):
        if not self.tracking:
            return
        try:
            payload = json.loads(message.payload)
        except (TypeError, ValueError):
            return
        if isinstance(payload, dict) and payload.get("type") in ("new", "update", "end"):
            self.ingest(payload.get("after"))

    def ingest(self, data):
        event = normal_event(data)
        if event and (existing := self.items.get(event["id"])) and (existing["camera"] != event["camera"] or existing["start"] != event["start"]):
            return None
        if not event or not self.tracking or event["camera"] not in self.cameras():
            return event
        previous = self.items.get(event["id"])
        if previous:
            if previous["camera"] != event["camera"] or previous["start"] != event["start"]:
                return None  # an ID may never be rebound to another event
            if previous["end"] is not None and event["end"] is None:
                return deepcopy(previous)  # a late update must not reopen a finished event
            for key in ("alerts", "analysis", "feedback"):
                if key in previous:
                    event[key] = previous[key]
            for key in ("has_snapshot", "has_clip"):
                if key not in data:
                    event[key] = previous[key]
        if event["label"] in PARKABLE:
            changes = data.get("position_changes")
            moving = type(changes) in (int, float) and changes > 0
            motionless = data.get("motionless_count")
            still = data.get("stationary") is True or (
                changes == 0 and type(motionless) in (int, float) and motionless > 0
            )
            # Keep evidence of travel for this track even if it later parks.
            if moving or (previous or {}).get("vehicle_moved"):
                event["vehicle_moved"] = True
            elif still or ((previous or {}).get("parked") and data.get("stationary") is not False):
                event.update(parked=True, parked_confirmed=True)

        self.items[event["id"]] = event
        self._save()
        rt = self.runtime()
        # A parked vehicle can appear as a new object before Frigate has enough samples.
        ready = event["label"] not in PARKABLE or event.get("vehicle_moved") or event["end"] is not None
        if ready and not event.get("parked") and self.in_scope(event) and self.on(CONF_EVENT_EVIDENCE) and getattr(rt, "incidents", None) is not None:
            rt.incidents.add(f"camera.{event['camera']}", f"Frigate reported {event['label']}", "event",
                             when=dt_util.utc_from_timestamp(event["start"]), event_id=event["id"])
        return deepcopy(event)

    async def async_event(self, camera, event_id):
        """Fetch this exact event, never the latest event in place of it."""
        camera = camera.removeprefix("camera.")
        if self._stopped or not safe_id(event_id) or not safe_id(camera):
            return None
        row = self.items.get(event_id)
        if row and row["camera"] == camera and row["end"] is not None:
            return deepcopy(row)
        stamp = self.stamp()
        url = frigate_url(self.hass)
        if not url:
            return deepcopy(row) if row and row["camera"] == camera else None
        try:
            async with asyncio.timeout(4):
                response = await async_get_clientsession(self.hass).get(f"{url}/api/events/{event_id}")
                data = await response.json() if response.status == 200 else None
        except Exception:  # noqa: BLE001 - absence of media cannot prove absence of a person
            return None
        if self._stopped or stamp != self.stamp():
            return None
        event = normal_event(data)
        if not event or event["id"] != event_id or event["camera"] != camera:
            return None
        return self.ingest(data)

    async def async_match(self, camera, when):
        if not self.on(CONF_EVENT_EVIDENCE):
            return None
        camera = camera.removeprefix("camera.")
        stamp = self.stamp()
        moment = when.timestamp()

        def candidates():
            return [r for r in self.items.values() if r["camera"] == camera and r["label"] == "person"
                    and r["start"] <= moment + 2 and (r["end"] is None or r["end"] >= moment - 2)
                    and r["start"] >= moment - 300]

        found = candidates()
        if not found and (url := frigate_url(self.hass)):
            try:
                async with asyncio.timeout(3):
                    response = await async_get_clientsession(self.hass).get(f"{url}/api/events", params={
                        "cameras": camera, "after": str(int(moment - 300)), "before": str(int(moment + 3)), "limit": "20"})
                    data = await response.json() if response.status == 200 else []
                if stamp != self.stamp() or not self.on(CONF_EVENT_EVIDENCE):
                    return None
                for event in data if isinstance(data, list) else []:
                    if isinstance(event, dict) and event.get("camera") == camera:
                        self.ingest(event)
            except Exception:  # noqa: BLE001 - an MQTT candidate may still arrive
                pass
            found = candidates()
        if not found and self._unsub is not None and stamp == self.stamp():
            # Occupancy may arrive before its tracked object. Keep the original camera and time window.
            await asyncio.sleep(1.5)
            found = candidates()
        return deepcopy(found[0]) if len(found) == 1 and stamp == self.stamp() and self.on(CONF_EVENT_EVIDENCE) else None

    def alert(self, event_id, sensor, reason, text, incident_id):
        if not self.on(CONF_EVENT_EVIDENCE) or (row := self.items.get(event_id)) is None:
            return
        alert = {"time": dt_util.utcnow().isoformat(), "sensor": sensor, "reason": reason,
                 "text": text[:600], "incident_id": incident_id, "link": "Unique camera/time candidate; sensor does not supply an event ID."}
        alerts = row.setdefault("alerts", [])
        if not any(a["sensor"] == sensor and a["reason"] == reason for a in alerts):
            alerts.append(alert)
            row["alerts"] = alerts[-10:]
            self._save()

    async def async_snapshot(self, row):
        """A bounded snapshot from this event ID, for AI enrichment only."""
        if not self.on(CONF_EVENT_EVIDENCE) or not safe_id(row.get("id")):
            return None
        stamp = self.stamp()
        url, local = frigate_url(self.hass), (self.hass.config.media_dirs or {}).get("local")
        if not url or not local:
            return None
        try:
            async with asyncio.timeout(4):
                async with async_get_clientsession(self.hass).get(f"{url}/api/events/{row['id']}/snapshot.jpg") as response:
                    body = await read_media(response, 8 * 1024 * 1024) if response.status == 200 else None
            if not body or not body.startswith(b"\xff\xd8") or stamp != self.stamp() or not self.on(CONF_EVENT_EVIDENCE):
                return None
            name = f"security_event_{uuid.uuid4().hex}.jpg"
            path = Path(local) / "sentient" / name

            def write():
                path.parent.mkdir(parents=True, exist_ok=True)
                path.write_bytes(body)

            await self.hass.async_add_executor_job(write)
            if stamp != self.stamp() or not self.on(CONF_EVENT_EVIDENCE):
                await self.hass.async_add_executor_job(lambda: path.unlink(missing_ok=True))
                return None
            return f"media-source://media_source/local/sentient/{name}", path
        except Exception:  # noqa: BLE001
            return None

    def analysis(self, event_id, camera, answer):
        row = self.items.get(event_id)
        if (not self.tracking or not row or row["camera"] != camera.removeprefix("camera.")
                or not isinstance(answer, dict) or not isinstance(answer.get("summary"), str)):
            return
        row["analysis"] = {"summary": answer["summary"][:1200], "status": "inferred",
                           "at": dt_util.utcnow().isoformat()}
        self._save()

    def feedback(self, event_id, value, also=()):
        """A correction on one event, or on every event of a visit (``also``)."""
        if not self.on(CONF_EVENT_FEEDBACK):
            return {"error": "Security event feedback is disabled"}
        self._prune()
        if value not in FEEDBACK or (row := self.items.get(event_id)) is None or row["camera"] not in self.cameras():
            return {"error": "Choose a retained event and a supported correction"}
        ids = [event_id] + [i for i in dict.fromkeys(also or ()) if i != event_id
                            and (other := self.items.get(i)) is not None and other["camera"] == row["camera"]]
        for i in ids:
            if value == "clear":
                self.items[i].pop("feedback", None)
            else:
                self.items[i]["feedback"] = {"value": value, "at": dt_util.utcnow().isoformat(),
                                             "source": "household correction; not independent verification"}
        self._save()
        reviewed = self._review_incidents(ids, value)
        return {"event_id": event_id, "event_ids": ids, "feedback": row.get("feedback"),
                "incidents_reviewed": reviewed, "changed_detector_settings": False}

    def from_incident_review(self, incidents, incident_id, outcome):
        """An incident reviewed as nothing (or an animal) marks its camera events "not a person", so the zone
        suggestions learn from the incident cards too. Taking the review back takes back only those marks."""
        if not self.on(CONF_EVENT_FEEDBACK):
            return 0
        item = next((i for i in getattr(incidents, "items", []) if i.get("id") == incident_id), None)
        ids = [e["event_id"] for e in (item or {}).get("evidence") or [] if e.get("event_id") in self.items]
        marked = 0
        for event_id in dict.fromkeys(ids):
            row = self.items[event_id]
            given = row.get("feedback") or {}
            if outcome == "false_detection" and not given:
                row["feedback"] = {"value": "false_positive", "at": dt_util.utcnow().isoformat(), "via": "incident",
                                   "source": "household correction; not independent verification"}
                marked += 1
            elif outcome != "false_detection" and given.get("via") == "incident":
                row.pop("feedback", None)
                marked += 1
        if marked:
            self._save()
        return marked

    def _review_incidents(self, ids, value):
        """"Not a person" is a reviewed false detection for an incident made only of these events.

        An incident with anything else in it (a door, another camera's sensor) isn't reviewed from here, and a
        review someone gave on the Mind tab is never replaced. Changing the answer takes back only reviews made here."""
        incidents = getattr(self.runtime(), "incidents", None)
        if incidents is None or not getattr(incidents, "enabled", False):
            return 0
        wanted = set(ids)
        done = 0
        for item in list(getattr(incidents, "items", None) or []):
            evidence = item.get("evidence") or []
            if not evidence or not all(e.get("event_id") in wanted for e in evidence):
                continue
            review = item.get("household_review")
            if review and review.get("via") != "event_feedback":
                continue
            if value in NOT_PERSON:
                if not (review and review.get("label") == "false_detection"):
                    incidents.review(item["id"], "false_detection", via="event_feedback")
                done += 1
            elif review:
                incidents.review(item["id"], "clear")
        return done

    def correction(self, event_id):
        row = self.items.get(event_id)
        if not self.on(CONF_EVENT_FEEDBACK) or not row or row["camera"] not in self.cameras():
            return None
        return deepcopy(row.get("feedback"))

    def recent(self):
        if not self.tracking:
            return []
        self._prune()
        rows = [row for row in self.items.values() if row["camera"] in self.cameras() and self.in_scope(row)]
        # a parked bike re-found all day mustn't push real sightings off the list
        rows = [r for r in rows if not r.get("parked")] + [r for r in rows if r.get("parked")]
        return [self.describe(row) for row in rows[:12]]

    def visits(self, limit=12):
        """Events on one camera within a minute of each other, as one visit: what was seen, where, and when."""
        if not self.tracking:
            return []
        self._prune()
        cameras = self.cameras()
        rows = sorted((r for r in self.items.values() if r["camera"] in cameras and not r.get("parked")
                       and self.in_scope(r)), key=lambda r: (r["camera"], r["start"]))
        groups = []
        for row in rows:
            last = groups[-1] if groups else None
            if last and last[0]["camera"] == row["camera"] and row["start"] <= max(
                    (r["end"] or r["start"]) for r in last) + VISIT_GAP:
                last.append(row)
            else:
                groups.append([row])
        groups.sort(key=lambda g: g[0]["start"], reverse=True)
        return [self._visit(g) for g in groups[:limit]]

    def _visit(self, rows):
        hidden = self.hidden_zones(rows[0]["camera"])
        best = next((r for r in rows if r["label"] == "person" and r.get("has_snapshot")), None) or next(
            (r for r in rows if r.get("has_snapshot")), rows[0])
        shown = self.describe(best)
        ends = [r["end"] for r in rows]
        given = [r["feedback"] for r in rows if r.get("feedback")] if self.on(CONF_EVENT_FEEDBACK) else []
        return {
            "id": rows[0]["id"], "ids": [r["id"] for r in rows], "camera": rows[0]["camera"],
            "start": rows[0]["start"], "end": None if None in ends else max(ends),
            "labels": list(dict.fromkeys(r["label"] for r in rows)),
            "zones": list(dict.fromkeys(z for r in rows for z in r.get("zones") or [] if z not in hidden)),
            "snapshot": shown["snapshot"], "clip": shown["clip"],
            "analysis": next((r["analysis"] for r in rows if r.get("analysis")), None),
            "alerts": [a for r in rows for a in r.get("alerts") or []][-10:],
            "feedback": max(given, key=lambda f: f.get("at", "")) if given else None,
            "limits": "Events close together on one camera are shown as one visit; that doesn't prove it was one person.",
        }

    def feedback_summary(self):
        if not self.on(CONF_EVENT_FEEDBACK):
            return []
        self._prune()
        cameras = {}
        for row in self.items.values():
            feedback = row.get("feedback", {}).get("value")
            if feedback not in FEEDBACK or feedback == "clear" or row["camera"] not in self.cameras():
                continue
            counts = cameras.setdefault(row["camera"], {"camera": row["camera"], "reviewed": 0,
                "useful": 0, "not_useful": 0, "false_positive": 0, "cat": 0, "person_corrections": 0})
            counts[feedback] += 1
            counts["reviewed"] += 1
            if row["label"] == "person" and feedback in ("cat", "false_positive"):
                counts["person_corrections"] += 1
        zones = {}
        for row in self.items.values():
            if (row.get("feedback") or {}).get("value") in NOT_PERSON and row["label"] == "person" and row["camera"] in self.cameras():
                hidden = self.hidden_zones(row["camera"])
                for zone in row.get("zones") or []:
                    if zone not in hidden:
                        zones[(row["camera"], zone)] = zones.get((row["camera"], zone), 0) + 1
        for counts in cameras.values():
            counts["suggestion"] = ("Review person-detection zones and confidence for this camera. No detector settings have been changed."
                                    if counts["person_corrections"] >= 3 else None)
            counts["limits"] = "Only reviewed events are counted; this is not a measured false-positive rate."
            counts["zones"] = [{"zone": zone, "not_a_person": n} for (camera, zone), n in sorted(zones.items())
                               if camera == counts["camera"] and n >= HIDE_ZONE_AFTER]
        return list(cameras.values())

    def describe(self, row):
        out = deepcopy(row)
        if not self.on(CONF_EVENT_FEEDBACK):
            out.pop("feedback", None)
        out["snapshot"] = f"/api/frigate/notifications/{row['id']}/thumbnail.jpg" if self.on(CONF_EVENT_EVIDENCE) and row.get("has_snapshot") else None
        out["clip"] = f"/api/frigate/notifications/{row['id']}/clip.mp4" if self.on(CONF_EVENT_EVIDENCE) and row.get("has_clip") else None
        out["limits"] = "Frigate detection and AI inference may be wrong. Nearby camera events do not establish one person's identity or route."
        return out

    def focus(self, owner, event_id=None):
        if not owner or not self.on(CONF_EVENT_CONTEXT):
            return None
        now = dt_util.utcnow()
        self._focus = {k: v for k, v in self._focus.items() if now - v[1] < timedelta(minutes=5) and v[2] == self.stamp()}
        if event_id is not None:
            if len(self._focus) >= 100:
                self._focus.pop(next(iter(self._focus)))
            self._focus[owner] = (event_id, now, self.stamp())
        return self._focus.get(owner, (None,))[0]

    def forget(self, since, until: float | None = None):
        """Forget incidents in an interval; recovery must preserve later events."""
        self.invalidate()

        def outside(value):
            when = dt_util.parse_datetime(str(value or ""))
            return (when is not None and
                    (dt_util.as_utc(when) < since or
                     (until is not None and dt_util.as_utc(when).timestamp() > until)))

        def keep(row):
            start = dt_util.utc_from_timestamp(row["start"])
            return (outside(start) and
                    all(outside(a.get("time")) for a in row.get("alerts", [])) and
                    (not row.get("feedback") or outside(row["feedback"].get("at"))) and
                    (not row.get("analysis") or outside(row["analysis"].get("at"))))

        previous_count = len(self.items)
        self.items = {key: row for key, row in self.items.items() if keep(row)}
        self._save()
        return previous_count - len(self.items)
