"""Event clips: watching what happened, not just a single picture.

Frigate keeps a short clip of each event. With this on, Sentient fetches the clip for an event (straight
from Frigate, on the local network), saves it in Home Assistant's local media folder and hands it to the
same AI task as everything else — Home Assistant's Google AI already accepts video. The answer is a
sentence or two about what actually happened: "A man in a hi-vis jacket left a parcel by the door and went."

Used after a camera-security alert (the clip is watched once the event has finished, and the timeline
gets the fuller story), and for "what happened at the door?" / "what happened in the garden?" by voice.
One AI call per clip, counted on the meter. Off by default.
"""

from __future__ import annotations

import asyncio
import logging
import uuid
from pathlib import Path
from typing import Any, Callable

from homeassistant.core import HomeAssistant
from homeassistant.helpers.aiohttp_client import async_get_clientsession
from homeassistant.util import dt as dt_util

from .ai import async_generate
from .numeric import finite_float
from .media import read_media
from .const import CONF_AI_TASK_ENTITY, CONF_CLIP_KEEP, CONF_EVENT_CLIPS, CONF_SMART_AI

_LOGGER = logging.getLogger(__name__)

MAX_BYTES = 25 * 1024 * 1024  # a 10–30 second clip is a few MB
SETTLE = 45  # seconds after an alert before the clip is asked for — Frigate finishes it when the event ends


class Clips:
    def __init__(self, hass: HomeAssistant, options: dict[str, Any], rt_getter: Callable[[], Any]) -> None:
        self.hass = hass
        self.options = options
        self._rt = rt_getter
        self.last: dict[str, Any] = {}
        self._stopped = False
        self._generation = 0
        self._busy: set[str] = set()

    async def async_start(self) -> None:
        self._stopped = False

    def async_stop(self) -> None:
        self._stopped = True
        self.invalidate()

    def invalidate(self) -> None:
        self._generation += 1

    def _stamp(self) -> tuple:
        rt = self._rt()
        speech = getattr(rt, "speech", None)
        return (self._generation, id(self.options), getattr(speech, "_privacy_generation", 0),
                getattr(speech, "_guest_generation", 0), getattr(speech, "guest_mode", False),
                getattr(getattr(rt, "ai", None), "_generation", 0))

    @property
    def enabled(self) -> bool:
        return bool(self.options.get(CONF_EVENT_CLIPS, False))

    def frigate_url(self) -> str | None:
        for entry in self.hass.config_entries.async_entries("frigate"):
            if url := entry.data.get("url"):
                return str(url).rstrip("/")
        return None

    @staticmethod
    def camera_name(camera: str) -> str:
        """camera.front_door -> front_door (Frigate's own name for it)."""
        return camera.split(".", 1)[-1]

    async def async_latest_event(self, camera: str, since: float | None = None) -> dict[str, Any] | None:
        url = self.frigate_url()
        if not url:
            return None
        params = {"cameras": self.camera_name(camera), "limit": "1", "has_clip": "1", "include_thumbnails": "0"}
        if since:
            params["after"] = str(int(since))
        session = async_get_clientsession(self.hass)
        async with asyncio.timeout(15):
            resp = await session.get(f"{url}/api/events", params=params)
            if resp.status != 200:
                return None
            events = await resp.json()
        return events[0] if events else None

    async def async_save_clip(self, event_id: str, camera: str, span: tuple[float, float] | None = None,
                              name: str | None = None) -> str | None:
        """Download the clip into local media; the media-source id for the AI task, or None."""
        url, local = self.frigate_url(), (self.hass.config.media_dirs or {}).get("local")
        if not url or not local:
            return None
        session = async_get_clientsession(self.hass)
        async with asyncio.timeout(60):
            async with session.get(f"{url}/api/events/{event_id}/clip.mp4") as resp:
                if resp.status != 200:
                    return None
                data = await read_media(resp, MAX_BYTES)
            if data and (b"hev1" in data[:400_000] or b"hvc1" in data[:400_000]) and span:
                # H.265, which the AI can't read: Frigate's own small H.264 preview of the same moments instead
                async with session.get(f"{url}/api/{self.camera_name(camera)}/start/{int(span[0])}/end/{int(span[1]) + 1}/preview.mp4") as resp:
                    data = await read_media(resp, MAX_BYTES) if resp.status == 200 else None
        if not data or len(data) > MAX_BYTES:
            return None
        name = name or f"clip_{self.camera_name(camera)}_{uuid.uuid4().hex}.mp4"

        def write() -> None:
            folder = Path(local) / "sentient"
            folder.mkdir(parents=True, exist_ok=True)
            (folder / name).write_bytes(data)

        await self.hass.async_add_executor_job(write)
        return f"media-source://media_source/local/sentient/{name}"

    async def async_what_happened(self, camera: str, question: str | None = None, since: float | None = None,
                                  force: bool = False) -> dict[str, Any]:
        """Watch the latest event, unless a valid cached clip summary answers already."""
        stamp = self._stamp()
        rt = self._rt()

        def current() -> bool:
            return not self._stopped and stamp == self._stamp() and (force or self.enabled)

        if self._stopped:
            return {"skipped": "event clip review stopped"}
        if rt is not None and since is None and (cw := getattr(rt, "clipwatch", None)) is not None and cw.enabled \
                and self.camera_name(camera) in cw.cameras() and (seen := cw.recent(self.camera_name(camera), 12)):
            return {"already_watched": [{"when": i["local"], "what": i["summary"], **({"probably": i["whose"]} if i.get("whose") else {})}
                                        for i in seen[:8]],
                    "note": "Clips from this camera in the last 12 hours, newest first. Say only what's asked."}
        if not current():
            return {"skipped": "event clips are off in Sentient's settings"}
        key = self.camera_name(camera)
        if key in self._busy:
            return {"skipped": "clip review already running for this camera"}
        self._busy.add(key)
        try:
            return await self._analyse(camera, question, since, force, current)
        finally:
            self._busy.discard(key)

    async def _analyse(self, camera: str, question: str | None, since: float | None, force: bool,
                       current: Callable[[], bool]) -> dict[str, Any]:
        rt = self._rt()
        try:
            event = await self.async_latest_event(camera, since)
        except Exception as err:  # noqa: BLE001
            return {"error": f"couldn't reach Frigate: {err}"}
        if not current():
            return {"skipped": "event clip context changed"}
        if not isinstance(event, dict) or not event.get("id"):
            return {"error": "no recent clip from that camera"}
        start = finite_float(event.get("start_time"))
        end = finite_float(event.get("end_time")) if event.get("end_time") else (start + 20 if start is not None else None)
        if start is None or end is None or start <= 0 or end < start:
            return {"error": "invalid clip times"}
        try:
            when = dt_util.as_local(dt_util.utc_from_timestamp(start))
            dt_util.utc_from_timestamp(end)
        except (ValueError, OverflowError, OSError):
            return {"error": "invalid clip times"}
        if rt is not None and not rt.watches.spend("clips", force=force):
            return {"skipped": "daily AI limit reached"}
        name = f"clip_{self.camera_name(camera)}_{uuid.uuid4().hex}.mp4"
        keep = bool(self.options.get(CONF_CLIP_KEEP))
        try:
            media = await self.async_save_clip(event["id"], camera, (start, end), name=name)
            if not current():
                return {"skipped": "event clip context changed"}
            if media is None:
                return {"error": "the clip isn't available"}
            data: dict[str, Any] = {
                "task_name": "Sentient: what happened",
                "instructions": (question or "Watch this short home security clip.") + (
                    " In one or two short spoken sentences, say what the person, animal or vehicle actually did — "
                    "e.g. 'A delivery driver left a parcel by the door and went.' Describe only what's visible; no "
                    "guesses at names, no camera names, IDs or times."),
                "attachments": [{"media_content_id": media, "media_content_type": "video/mp4"}],
            }
            if ai := self.options.get(CONF_SMART_AI) or self.options.get(CONF_AI_TASK_ENTITY):
                data["entity_id"] = ai
            response = await async_generate(self.hass, data)
        except Exception as err:  # noqa: BLE001
            return {"error": str(err)}
        finally:
            if (not (keep and self.options.get(CONF_CLIP_KEEP)) or not current()) and (local := (self.hass.config.media_dirs or {}).get("local")):
                await self.hass.async_add_executor_job(lambda: (Path(local) / "sentient" / name).unlink(missing_ok=True))
        if not current():
            return {"skipped": "event clip context changed"}
        answer = response.get("data") if isinstance(response, dict) else None
        if not isinstance(answer, str) or not answer.strip():
            return {"error": "no clip description returned"}
        answer = answer.strip()
        self.last = {"camera": camera, "event": event["id"], "answer": answer, "time": when.isoformat()}
        if rt is not None and getattr(rt, "incidents", None) is not None:
            rt.incidents.add(camera if camera.startswith("camera.") else f"camera.{camera}", answer,
                             "clip", evidence="inferred", when=when, event_id=event["id"])
        return {"what_happened": answer, "when": when.strftime("%H:%M"), "label": event.get("label")}

    async def async_after_alert(self, camera: str, text: str, stamp: tuple | None = None) -> None:
        """Once the event's over, watch the clip and add the fuller story to the timeline."""
        stamp = self._stamp() if stamp is None else stamp
        if self._stopped or not self.enabled or stamp != self._stamp():
            return
        started = dt_util.utcnow().timestamp() - 120
        await asyncio.sleep(SETTLE)
        if self._stopped or not self.enabled or stamp != self._stamp():
            return
        out = await self.async_what_happened(camera, since=started)
        if (not self._stopped and self.enabled and stamp == self._stamp()
                and (rt := self._rt()) is not None and out.get("what_happened")):
            rt.timeline.add("security", f"Clip: {out['what_happened']}", f"after: {text[:80]}")
