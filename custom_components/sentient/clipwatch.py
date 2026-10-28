"""Clip watching: what happened on the cameras, written down as it happens.

With this on, when Frigate finishes a review on one of the chosen cameras (an alert, a detection in a zone,
or any detection — a setting), Sentient fetches that stretch of recording, has the AI watch it once, and
keeps a sentence: "Gate 14:10 — a man walked a dog past". Those sentences go on the timeline and the
Sentient page, the voice answers "what happened at the gate this afternoon?" from them without another AI
call, and — if chosen — the phones get the ones worth a look. Never spoken.

Or, if Frigate's own AI review summaries are switched on in Frigate, Sentient can use those instead and make
no AI call of its own. Optional "whose": the same call is given one reference photo per household member from
Frigate's face library and says who it is only when it's clearly one of them.

Privacy: the clip file is deleted once it's watched; summaries are words only (no stranger is ever named) and
are dropped after a few days. Cheap: Frigate's own event messages over MQTT (no polling), a per-camera
cooldown, short clips skipped, long ones cut to 30 seconds, and its own daily allowance on the AI meter.
"""

from __future__ import annotations

import asyncio
from datetime import datetime, timedelta
import json
import logging
from pathlib import Path
import re
from typing import Any, Callable

from homeassistant.const import EVENT_STATE_CHANGED
from homeassistant.core import Event, HomeAssistant, callback
from homeassistant.helpers.aiohttp_client import async_get_clientsession
from homeassistant.helpers.event import async_track_time_change
from homeassistant.helpers.storage import Store
from homeassistant.util import dt as dt_util

from .ai import async_generate
from .const import (
    CLIP_NOTIFY_MODES,
    CLIP_SCOPES,
    CLIP_SOURCES,
    CLIP_WHENS,
    CONF_AI_TASK_ENTITY,
    CONF_CLIP_CAMERAS,
    CONF_CLIP_DAYS,
    CONF_CLIP_KEEP,
    CONF_CLIP_LOOKS,
    CONF_CLIP_NOTIFY,
    CONF_CLIP_SCOPE,
    CONF_CLIP_SOURCE,
    CONF_CLIP_WATCH,
    CONF_CLIP_WHEN,
    CONF_CLIP_WHOSE,
    CONF_FACE_PEOPLE,
    CONF_PERSONA,
    DEFAULT_PERSONA,
    DOMAIN,
)
from .faces import frigate_url
from .notifier import phone_apps

_LOGGER = logging.getLogger(__name__)

COOLDOWN = timedelta(minutes=5)  # per camera
MIN_SECONDS = 3
MAX_SECONDS = 30
MAX_BYTES = 25 * 1024 * 1024
HEVC = (b"hev1", b"hvc1")
KINDS = ["delivery", "visitor", "household", "passer-by", "animal", "vehicle", "suspicious", "nothing"]
SCOPES, WHENS, NOTIFY, SOURCES = CLIP_SCOPES, CLIP_WHENS, CLIP_NOTIFY_MODES, CLIP_SOURCES


def frigate_prefix(hass: HomeAssistant) -> str:
    for entry in hass.config_entries.async_entries("frigate"):
        for src in (entry.options, entry.data):
            if prefix := src.get("topic_prefix") or src.get("mqtt_topic_prefix"):
                return str(prefix).strip("/")
    return "frigate"


def pretty(camera: str) -> str:
    return camera.replace("_", " ").strip().capitalize()


class ClipWatch:
    def __init__(self, hass: HomeAssistant, entry_id: str, options: dict[str, Any], rt_getter: Callable[[], Any]) -> None:
        self.hass = hass
        self.options = options
        self._rt = rt_getter
        self._store: Store[dict[str, Any]] = Store(hass, 1, f"{DOMAIN}.{entry_id}.clipwatch")
        self.items: list[dict[str, Any]] = []  # newest first
        self._last: dict[str, datetime] = {}
        self._unsubs: list = []
        self.via: str | None = None
        self.skipped: dict[str, int] = {}
        self._stopped = False
        self.hevc: set[str] = set()  # cameras that record H.265

    # ----- settings -----------------------------------------------------------------------
    @property
    def enabled(self) -> bool:
        return bool(self.options.get(CONF_CLIP_WATCH, False))

    def cameras(self) -> list[str]:
        """Frigate's names for the chosen cameras."""
        return [c.split(".", 1)[-1] for c in self.options.get(CONF_CLIP_CAMERAS) or [] if c]

    def _opt(self, key: str, choices: list[str]) -> str:
        value = self.options.get(key) or choices[0]
        return value if value in choices else choices[0]

    # ----- lifecycle ----------------------------------------------------------------------
    async def async_start(self) -> None:
        self.items = (await self._store.async_load() or {}).get("items", [])
        self._prune()
        self._unsubs.append(self.hass.bus.async_listen(EVENT_STATE_CHANGED, self._occupancy))
        self._unsubs.append(async_track_time_change(self.hass, self._nightly, hour=4, minute=5, second=0))
        if self.hass.config_entries.async_entries("mqtt"):
            self.hass.async_create_background_task(self._subscribe(), "sentient clip watch: Frigate reviews")

    async def _subscribe(self) -> None:
        """Listen for Frigate's review messages once MQTT is up (in the background, so start-up isn't held)."""
        try:
            from homeassistant.components import mqtt

            if not await mqtt.async_wait_for_mqtt_client(self.hass) or self._stopped:
                return
            self._unsubs.append(await mqtt.async_subscribe(self.hass, f"{frigate_prefix(self.hass)}/reviews", self._message))
            self.via = "mqtt"
        except Exception as err:  # noqa: BLE001 - the occupancy sensors still work
            _LOGGER.debug("Sentient couldn't listen for Frigate reviews: %s", err)

    @callback
    def async_stop(self) -> None:
        self._stopped = True
        while self._unsubs:
            self._unsubs.pop()()

    @callback
    def _nightly(self, _now: datetime) -> None:
        if self._prune():
            self._save()

    def _prune(self) -> bool:
        days = int(float(self.options.get(CONF_CLIP_DAYS) or 7))
        cutoff = (dt_util.utcnow() - timedelta(days=days)).isoformat()
        before = len(self.items)
        self.items = [i for i in self.items if i.get("time", "") >= cutoff][:300]
        return len(self.items) != before

    def _save(self) -> None:
        self._store.async_delay_save(lambda: {"items": self.items}, 5)

    # ----- what starts a watch ------------------------------------------------------------
    def wanted(self, review: dict[str, Any]) -> str | None:
        """None if this review should be watched, else why not."""
        if not self.enabled:
            return "off"
        if review.get("camera") not in self.cameras():
            return "camera"
        data = review.get("data") or {}
        scope = self._opt(CONF_CLIP_SCOPE, SCOPES)
        if scope == "alerts" and review.get("severity") != "alert":
            return "not an alert"
        if scope == "zones" and not data.get("zones"):
            return "not in a zone"
        when = self._opt(CONF_CLIP_WHEN, WHENS)
        if when != "always" and (rt := self._rt()) is not None:
            quiet = rt.speech.quiet_hours_now()
            away = rt.away.everyone_away()
            if not {"quiet": quiet, "away": away, "quiet_or_away": quiet or away}[when]:
                return "not the chosen time"
        return None

    def _skip(self, why: str) -> None:
        self.skipped[why] = self.skipped.get(why, 0) + 1

    @callback
    def _message(self, msg: Any) -> None:
        try:
            payload = json.loads(msg.payload)
        except (TypeError, ValueError):
            return
        kind, review = payload.get("type"), payload.get("after") or {}
        source = self._opt(CONF_CLIP_SOURCE, SOURCES)
        if (kind == "end" and source == "sentient") or (kind == "genai" and source == "frigate"):
            if why := self.wanted(review):
                if why != "off":
                    self._skip(why)
                return
            self.hass.async_create_task(self.async_watch(review, frigate_summary=kind == "genai"), eager_start=False)

    @callback
    def _occupancy(self, event: Event) -> None:
        """Without MQTT: a person leaving a chosen camera's view starts a watch of its latest event."""
        if self.via == "mqtt" or not self.enabled:
            return
        entity = event.data.get("entity_id", "")
        if not entity.endswith("_person_occupancy"):
            return
        old, new = event.data.get("old_state"), event.data.get("new_state")
        if old is None or new is None or old.state != "on" or new.state != "off":
            return
        camera = entity.removeprefix("binary_sensor.").removesuffix("_person_occupancy")
        if camera in self.cameras():
            self.hass.async_create_task(self._from_latest_event(camera), eager_start=False)

    async def _from_latest_event(self, camera: str) -> None:
        url = frigate_url(self.hass)
        if not url:
            return
        try:
            async with asyncio.timeout(15):
                resp = await async_get_clientsession(self.hass).get(
                    f"{url}/api/events", params={"cameras": camera, "limit": "1", "has_clip": "1"})
                events = await resp.json() if resp.status == 200 else []
        except Exception:  # noqa: BLE001
            return
        if not events:
            return
        ev = events[0]
        review = {"id": ev["id"], "camera": camera, "start_time": ev.get("start_time"), "end_time": ev.get("end_time"),
                  "severity": "alert" if ev.get("zones") else "detection",
                  "data": {"detections": [ev["id"]], "zones": ev.get("zones") or [], "objects": [ev.get("label")]}}
        if why := self.wanted(review):
            self._skip(why)
            return
        await self.async_watch(review)

    # ----- watching -----------------------------------------------------------------------
    async def async_watch(self, review: dict[str, Any], frigate_summary: bool = False, force: bool = False) -> dict[str, Any]:
        camera = str(review.get("camera") or "")
        now = dt_util.utcnow()
        if not force and (last := self._last.get(camera)) and now - last < COOLDOWN:
            self._skip("cooldown")
            return {"skipped": "cooldown"}
        start = float(review.get("start_time") or 0)
        end = float(review.get("end_time") or 0) or start + 10
        if end - start < MIN_SECONDS and not force:
            self._skip("too short")
            return {"skipped": "too short"}
        end = min(end, start + MAX_SECONDS)
        data = review.get("data") or {}
        event = (data.get("detections") or [None])[0]
        rt = self._rt()
        if frigate_summary:
            meta = data.get("metadata") or {}
            summary = str(meta.get("shortSummary") or meta.get("title") or meta.get("scene") or "").strip()
            if not summary:
                return {"skipped": "no Frigate summary"}
            threat = int(meta.get("potential_threat_level") or 0)
            answer = {"summary": summary, "kind": "suspicious" if threat else "visitor", "worth": threat >= 1}
        else:
            if rt is not None and not rt.watches.spend("clip watch", force=force):
                self._skip("budget")
                return {"skipped": "today's clip allowance is used up"}
            self._last[camera] = now
            try:
                answer = await self._ask_ai(camera, start, end, event)
            except Exception as err:  # noqa: BLE001
                _LOGGER.debug("Sentient couldn't watch a clip from %s: %s", camera, err)
                return {"error": str(err)}
        self._last[camera] = now
        if not answer or not str(answer.get("summary") or "").strip():
            return {"error": "no answer"}
        return self._record(camera, start, event, review.get("id"), answer)

    async def _download(self, path: str, name: str, refuse: tuple[bytes, ...] = ()) -> str | None:
        """Fetch something from Frigate into local media; its media-source id, or None (also None when its
        first bytes contain any of `refuse` — e.g. H.265 video, which the AI can't read)."""
        url, local = frigate_url(self.hass), (self.hass.config.media_dirs or {}).get("local")
        if not url or not local:
            return None
        async with asyncio.timeout(60):
            resp = await async_get_clientsession(self.hass).get(f"{url}{path}")
            if resp.status != 200:
                return None
            body = await resp.content.read(MAX_BYTES + 1)
        if not body or len(body) > MAX_BYTES or any(r in body[:400_000] for r in refuse):
            return None

        def write() -> None:
            folder = Path(local) / "sentient"
            folder.mkdir(parents=True, exist_ok=True)
            (folder / name).write_bytes(body)

        await self.hass.async_add_executor_job(write)
        return f"media-source://media_source/local/sentient/{name}"

    async def _remove(self, name: str) -> None:
        local = (self.hass.config.media_dirs or {}).get("local")
        if local:
            await self.hass.async_add_executor_job(lambda: (Path(local) / "sentient" / name).unlink(missing_ok=True))

    def looks(self) -> dict[str, str]:
        """'Alex: motorbike helmet and riding jacket' lines -> {name: look}."""
        out = {}
        for line in str(self.options.get(CONF_CLIP_LOOKS) or "").splitlines():
            name, _, look = line.partition(":")
            if name.strip() and look.strip():
                out[name.strip()] = look.strip()
        return out

    async def _references(self) -> list[tuple[str, str]]:
        """One face photo per household member from Frigate's face library: [(name, media id)]."""
        url = frigate_url(self.hass)
        if not url:
            return []
        async with asyncio.timeout(15):
            resp = await async_get_clientsession(self.hass).get(f"{url}/api/faces")
            faces = await resp.json() if resp.status == 200 else {}
        chosen = {p.strip().replace(" ", "_").lower() for p in self.options.get(CONF_FACE_PEOPLE) or [] if p.strip()}
        out = []
        for name, files in sorted((faces or {}).items()):
            if name == "train" or not files or (chosen and name.lower() not in chosen):
                continue
            safe = re.sub(r"[^\w-]", "", name)
            media = await self._download(f"/clips/faces/{name}/{sorted(files)[-1]}", f"face_{safe}.webp")
            if media:
                out.append((name.replace("_", " "), media))
            if len(out) >= 6:
                break
        return out

    async def _video(self, camera: str, start: float, end: float, name: str) -> str | None:
        """The recording as H.264 the AI can read. Cameras that record H.265 get Frigate's own preview of the
        same moments instead (small, already H.264 — no converting here, so no load on Home Assistant)."""
        span = f"/api/{camera}/start/{int(start)}/end/{int(end) + 1}"
        if camera not in self.hevc:
            if media := await self._download(f"{span}/clip.mp4", name, refuse=HEVC):
                return media
            self.hevc.add(camera)
        return await self._download(f"{span}/preview.mp4", name)

    async def _ask_ai(self, camera: str, start: float, end: float, event: str | None = None) -> dict[str, Any] | None:
        name = f"clipwatch_{camera}.mp4"
        media = await self._video(camera, start, end, name)
        if media is None:
            raise RuntimeError("the clip isn't available")
        attachments = [{"media_content_id": media, "media_content_type": "video/mp4"}]
        if camera in self.hevc and event:  # the preview is small: add Frigate's best full-size still of it
            still = await self._download(f"/api/events/{event}/snapshot-clean.webp", f"clipwatch_{camera}.webp")
            if still:
                attachments.append({"media_content_id": still, "media_content_type": "image/webp"})
        structure: dict[str, Any] = {
            "summary": {"description": "One short sentence: what the person, animal or vehicle did", "required": True,
                        "selector": {"text": {}}},
            "kind": {"description": "What it was", "required": True,
                     "selector": {"select": {"options": KINDS}}},
            "worth": {"description": "Worth the household knowing about (not just someone passing)", "required": True,
                      "selector": {"boolean": {}}},
        }
        instructions = (
            f"This is a short home security clip from the {pretty(camera).lower()} camera. In one short sentence, say "
            "what actually happened — e.g. 'A delivery driver left a parcel by the door and went.' or 'A man walked "
            "a dog past the gate.' Describe clothing and actions only. Never guess a stranger's name; no camera names, "
            "IDs or times. 'suspicious' is only for someone trying doors, looking into windows or cars, or lingering "
            "for no clear reason.")
        plain = (instructions, dict(structure))  # the video on its own, if the extras are refused
        if len(attachments) > 1:
            instructions += " The clip is a small preview; a full-size still of the clearest moment follows it."
        refs: list[tuple[str, str]] = []
        looks = self.looks() if self.options.get(CONF_CLIP_WHOSE) else {}
        if self.options.get(CONF_CLIP_WHOSE):
            try:
                refs = await self._references()
            except Exception as err:  # noqa: BLE001
                _LOGGER.debug("Sentient couldn't fetch the reference faces: %s", err)
        names = list(dict.fromkeys([n for n, _ in refs] + list(looks)))
        if names:
            attachments += [{"media_content_id": m, "media_content_type": "image/webp"} for _, m in refs]
            if refs:
                instructions += (" Then come reference face photos of the household, in this order: "
                                 + ", ".join(n for n, _ in refs) + ".")
            if looks:
                instructions += " How household members often look when their face can't be seen: " + "; ".join(
                    f"{n} — {d}" for n, d in looks.items()) + "."
            rt = self._rt()
            seen = rt.faces.recent(2) if rt is not None else []
            if seen:
                instructions += " Recently recognised elsewhere: " + "; ".join(
                    f"{s['name']} at the {s['camera'].lower()} {s['time']}" for s in seen[:4]) + "."
            instructions += (" Set 'whose' to one of " + ", ".join(names) + " only if the person is clearly them "
                             "(face, or that look together with how they arrive or leave); otherwise leave it empty. "
                             "Never guess.")
            structure["whose"] = {"description": "Household member's name if clearly them, else empty",
                                  "required": False, "selector": {"text": {}}}
        data: dict[str, Any] = {"task_name": "Sentient: clip watch", "instructions": instructions,
                                "structure": structure, "attachments": attachments}
        if ai := self.options.get(CONF_AI_TASK_ENTITY):
            data["entity_id"] = ai
        try:
            try:
                response = await async_generate(self.hass, data)
            except Exception:  # noqa: BLE001 - an extra picture the AI won't take: try the clip alone once
                if len(attachments) == 1:
                    raise
                names = []
                data.update(instructions=plain[0], structure=plain[1], attachments=attachments[:1])
                response = await async_generate(self.hass, data)
        finally:
            if not self.options.get(CONF_CLIP_KEEP):
                await self._remove(name)
                await self._remove(f"clipwatch_{camera}.webp")
        answer = (response or {}).get("data")
        if not isinstance(answer, dict):
            return {"summary": str(answer or ""), "kind": "visitor", "worth": False}
        whose = str(answer.get("whose") or "").strip()
        answer["whose"] = next((n for n in names if n.lower() == whose.lower()), None)
        return answer

    # ----- results ------------------------------------------------------------------------
    def _record(self, camera: str, start: float, event: str | None, review_id: str | None,
                answer: dict[str, Any]) -> dict[str, Any]:
        when = dt_util.utc_from_timestamp(start) if start else dt_util.utcnow()
        kind = answer.get("kind") if answer.get("kind") in KINDS else "visitor"
        summary = str(answer.get("summary")).strip().rstrip(".") + "."
        item = {"time": when.isoformat(), "camera": camera, "summary": summary, "kind": kind,
                "worth": bool(answer.get("worth")), "whose": answer.get("whose"), "event": event, "review": review_id}
        self.items.insert(0, item)
        self._prune()
        self._save()
        local = dt_util.as_local(when).strftime("%H:%M")
        who = f" (probably {item['whose']})" if item.get("whose") else ""
        text = f"{pretty(camera)} {local} — {summary[0].lower() + summary[1:] if summary else ''}"
        if (rt := self._rt()) is not None:
            rt.timeline.add("clip", text + who, "clip watched", camera=camera, event=event)
        self._notify(item, text + who)
        return {"recorded": item}

    def _notify(self, item: dict[str, Any], text: str) -> None:
        mode = self._opt(CONF_CLIP_NOTIFY, NOTIFY)
        if mode == "off" or (mode == "worth" and not (item["worth"] or item["kind"] == "suspicious")) or (
                mode == "suspicious" and item["kind"] != "suspicious"):
            return
        apps = phone_apps(self.hass, self.options)
        if not apps:
            return
        persona = self.options.get(CONF_PERSONA) or DEFAULT_PERSONA
        data: dict[str, Any] = {"tag": f"sentient-clip-{item['camera']}"}
        if ev := item.get("event"):
            data["image"] = f"/api/frigate/notifications/{ev}/thumbnail.jpg"
            data["actions"] = [{"action": "URI", "title": "Play clip", "uri": f"/api/frigate/notifications/{ev}/clip.mp4"}]
        msg = {"title": f"{persona} · {pretty(item['camera'])}", "message": text, "data": data}

        async def send() -> None:
            for app in apps:
                try:
                    await self.hass.services.async_call("notify", app, msg, blocking=True)
                except Exception as err:  # noqa: BLE001
                    _LOGGER.warning("Sentient couldn't send a clip notification to %s: %s", app, err)

        self.hass.async_create_task(send(), eager_start=False)

    def recent(self, camera: str | None = None, hours: float = 24) -> list[dict[str, Any]]:
        cutoff = (dt_util.utcnow() - timedelta(hours=hours)).isoformat()
        out = []
        for i in self.items:
            if i["time"] < cutoff:
                continue
            if camera is None or i["camera"] == camera:
                out.append({**i, "local": dt_util.as_local(dt_util.parse_datetime(i["time"])).strftime("%a %H:%M")})
        return out

    async def async_latest(self, camera: str) -> dict[str, Any]:
        """Watch the latest review from this camera now (check_now)."""
        url = frigate_url(self.hass)
        if not url:
            return {"error": "Frigate isn't set up in Home Assistant"}
        async with asyncio.timeout(15):
            resp = await async_get_clientsession(self.hass).get(f"{url}/api/review", params={"cameras": camera, "limit": "1"})
            reviews = await resp.json() if resp.status == 200 else []
        if not reviews:
            return {"error": "no recent review from that camera"}
        return await self.async_watch(reviews[0], force=True)
