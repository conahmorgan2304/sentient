"""Faces: putting names to people the cameras already recognise.

Sentient doesn't recognise faces itself. Frigate does (its "<name> last camera" sensors), and so do some
doorbells (a "last recognised face" sensor). When one of those names someone, Sentient notes it:
"Sam seen at the gate, 17:32" goes on the timeline, and the voice can answer "has Sam come home?"
or "when did Nan last come round?". Names are only ever said when someone asks — never announced, never
guessed (an unknown face stays "someone"). On/off, and which people, in the settings. Off by default.

Teaching (its own switch, also off): recognition only gets good with practice. When a person leaves one of the
chosen cameras and Frigate kept a face it couldn't name, the phones get a private "Who's this?" with the face
and a button per person (or type a name). Tapping one files that face under the name in Frigate, so it
recognises them next time. Nothing is spoken, it never goes to the tablets, and it asks at most every
ten minutes and a handful of times a day. No AI calls — it's Frigate's own face library.

Strangers: "Not someone we know" (or typing "no" under "Someone else…") deletes that face from Frigate straight away, and (a setting) every night
any face nobody named that's older than a set number of hours is deleted from Frigate too — so only the
household's faces are kept. Nothing about a stranger is ever stored by Sentient.
"""

from __future__ import annotations

import asyncio
from datetime import datetime, timedelta
import logging
import re
import uuid
from typing import Any, Callable

from homeassistant.const import EVENT_STATE_CHANGED
from homeassistant.core import Event, HomeAssistant, callback
from homeassistant.helpers.aiohttp_client import async_get_clientsession
from homeassistant.helpers.event import async_track_time_change
from homeassistant.helpers.storage import Store
from homeassistant.util import dt as dt_util

from .const import (
    CONF_DOOR_CAMERA,
    CONF_FACE_CAMERAS,
    CONF_FACE_FORGET_HOURS,
    CONF_FACE_PEOPLE,
    CONF_FACE_SENSOR,
    CONF_FACE_TEACH,
    CONF_FACE_TEACH_CAMERAS,
    CONF_FACES,
    CONF_PERSONA,
    DEFAULT_PERSONA,
    DOMAIN,
)
from .notifier import phone_apps

_LOGGER = logging.getLogger(__name__)

FRIGATE = re.compile(r"^sensor\.frigate_(.+)_last_camera$")
NOT_A_NAME = {"", "unknown", "unavailable", "none", "no one", "nobody", "null"}
REPEAT = timedelta(minutes=20)  # the same person on the same camera again within this is the same visit
ACTION = "SENTIENT_FACE"  # prefix of the "who's this?" buttons
ASK_GAP = timedelta(minutes=10)
ASK_DAILY = 6
SETTLE = 8  # seconds after someone leaves before looking — Frigate files the face as the event ends
SKIP_WORDS = {"", "no", "skip", "nobody", "no one", "unknown", "don't know", "dont know", "not sure", "stranger"}


def frigate_url(hass: HomeAssistant) -> str | None:
    for entry in hass.config_entries.async_entries("frigate"):
        if url := entry.data.get("url"):
            return str(url).rstrip("/")
    return None


def face_file_time(name: str) -> float | None:
    """When Frigate saved an unnamed face: '<frame time>-<id>-<saved at>-<guess>-<score>.webp'."""
    parts = name.split("-")
    for i in (2, 0):
        try:
            return float(parts[i])
        except (ValueError, IndexError):
            continue
    return None


def _norm(text: str) -> str:
    return re.sub(r"[^a-z0-9]", "", text.lower())


def parse_face_file(name: str, event_id: str) -> tuple[str, float]:
    """Frigate names unrecognised faces '<event id>-<time>-<best guess>-<score>.webp'."""
    try:
        rest = name[len(event_id) + 1:].rsplit(".", 1)[0]
        guess, score = rest.split("-", 1)[1].rsplit("-", 1)
        return guess, float(score)
    except (ValueError, IndexError):
        return "", 0.0


class Faces:
    def __init__(self, hass: HomeAssistant, entry_id: str, options: dict[str, Any], rt_getter: Callable[[], Any]) -> None:
        self.hass = hass
        self.options = options
        self._rt = rt_getter
        self._store: Store[dict[str, Any]] = Store(hass, 1, f"{DOMAIN}.{entry_id}.faces")
        self.seen: dict[str, dict[str, str]] = {}  # name -> {camera, time}
        self._unsubs: list = []
        self._asks: dict[str, dict[str, Any]] = {}  # button token -> {file, names, camera}
        self._asked: list[str] = []  # face files already asked about
        self._last_ask: datetime | None = None
        self._today: tuple[str, int] = ("", 0)
        self.taught = 0

    async def async_start(self) -> None:
        self.seen = (await self._store.async_load() or {}).get("seen", {})
        self._unsubs = [
            self.hass.bus.async_listen(EVENT_STATE_CHANGED, self._changed),
            self.hass.bus.async_listen("mobile_app_notification_action", self._answer),
            async_track_time_change(self.hass, self._nightly, hour=3, minute=50, second=0),
        ]

    @callback
    def async_stop(self) -> None:
        while self._unsubs:
            self._unsubs.pop()()

    @property
    def enabled(self) -> bool:
        return bool(self.options.get(CONF_FACES, False))

    def allowed(self, name: str) -> bool:
        people = [p.strip().lower() for p in self.options.get(CONF_FACE_PEOPLE) or [] if p.strip()]
        return not people or name.lower() in people

    def camera_allowed(self, camera: str) -> bool:
        """Only names from the chosen cameras count (none chosen: all)."""
        chosen = self.options.get(CONF_FACE_CAMERAS) or []
        if not chosen:
            return True
        names = set()
        for c in chosen:
            names.add(_norm(c.split(".", 1)[-1]))
            if (st := self.hass.states.get(c)) is not None:
                names.add(_norm(st.name))
        return _norm(camera) in names

    @property
    def teaching(self) -> bool:
        return bool(self.options.get(CONF_FACE_TEACH, False))

    def teach_cameras(self) -> list[str]:
        """Frigate's names for the cameras to learn from (default: the door camera)."""
        cams = self.options.get(CONF_FACE_TEACH_CAMERAS) or [self.options.get(CONF_DOOR_CAMERA) or ""]
        return [c.split(".", 1)[-1] for c in cams if c]

    @callback
    def _changed(self, event: Event) -> None:
        if self.teaching and event.data.get("entity_id", "").endswith("_person_occupancy"):
            self._visit_ended(event)
        if not self.enabled:
            return
        new = event.data.get("new_state")
        if new is None or str(new.state).strip().lower() in NOT_A_NAME:
            return
        old = event.data.get("old_state")
        if old is not None and old.state == new.state and new.entity_id != self.options.get(CONF_FACE_SENSOR):
            return
        if m := FRIGATE.match(new.entity_id):
            name = (new.name or m.group(1)).removeprefix("Frigate ").removesuffix(" Last Camera").removesuffix(" last camera")
            camera = str(new.state)
        elif new.entity_id == self.options.get(CONF_FACE_SENSOR):
            name = str(new.state)
            door = self.hass.states.get(self.options.get(CONF_DOOR_CAMERA) or "")
            camera = door.name if door else "the door"
        else:
            return
        name = " ".join(w.capitalize() for w in re.split(r"[_\s]+", name.strip()) if w)
        if not name or not self.allowed(name):
            return
        if not self.camera_allowed(camera):
            return
        self.saw(name, camera.replace("_", " "))

    @callback
    def saw(self, name: str, camera: str) -> None:
        now = dt_util.utcnow()
        last = self.seen.get(name)
        if last and last.get("camera") == camera and now - (dt_util.parse_datetime(last["time"]) or now) < REPEAT:
            self.seen[name]["time"] = now.isoformat()
            return
        self.seen[name] = {"camera": camera, "time": now.isoformat()}
        self._store.async_delay_save(lambda: {"seen": self.seen}, 5)
        if (rt := self._rt()) is not None:
            rt.timeline.add("noticed", f"{name} seen at the {camera.lower()}", "face recognition")

    def recent(self, hours: float = 24) -> list[dict[str, str]]:
        cutoff = dt_util.utcnow() - timedelta(hours=hours)
        out = []
        for name, s in self.seen.items():
            when = dt_util.parse_datetime(s.get("time") or "")
            if when and when >= cutoff and self.allowed(name):
                out.append({"name": name, "camera": s["camera"], "time": dt_util.as_local(when).strftime("%H:%M")})
        return sorted(out, key=lambda s: s["time"], reverse=True)

    def prompt_line(self) -> str | None:
        if not self.enabled or not (seen := self.recent()):
            return None
        return ("Recognised by the cameras in the last day (face recognition) — only mention this when someone asks "
                "who's been about or whether someone's home; never announce it: "
                + "; ".join(f"{s['name']} at the {s['camera'].lower()} {s['time']}" for s in seen[:8]) + ".")

    # ----- teaching: "who's this?" on the phones ---------------------------------------------------
    @callback
    def _visit_ended(self, event: Event) -> None:
        old, new = event.data.get("old_state"), event.data.get("new_state")
        if old is None or new is None or old.state != "on" or new.state != "off":
            return
        camera = new.entity_id.removeprefix("binary_sensor.").removesuffix("_person_occupancy")
        if camera in self.teach_cameras():
            self.hass.async_create_task(self._after_visit(camera), eager_start=False)

    async def _after_visit(self, camera: str) -> None:
        await asyncio.sleep(SETTLE)
        try:
            await self.async_ask(camera)
        except Exception as err:  # noqa: BLE001 - never let this break anything
            _LOGGER.debug("Sentient couldn't ask about a face: %s", err)

    async def _get(self, path: str, params: dict[str, str] | None = None) -> Any:
        url = frigate_url(self.hass)
        if not url:
            return None
        async with asyncio.timeout(15):
            resp = await async_get_clientsession(self.hass).get(f"{url}/api/{path}", params=params)
            return await resp.json() if resp.status == 200 else None

    async def _post(self, path: str, body: dict[str, Any] | None = None) -> bool:
        url = frigate_url(self.hass)
        if not url:
            return False
        async with asyncio.timeout(15):
            resp = await async_get_clientsession(self.hass).post(f"{url}/api/{path}", json=body or {})
            return resp.status == 200

    def _names(self, known: list[str], guess: str) -> list[str]:
        """Up to two people to offer as buttons: Frigate's best guess first, then the chosen people."""
        chosen = [p.strip() for p in self.options.get(CONF_FACE_PEOPLE) or [] if p.strip()]
        order = [guess] + [k for c in chosen for k in known if k.lower() == c.replace(" ", "_").lower()] + known
        out: list[str] = []
        for n in order:
            if n and n in known and n not in out and self.allowed(n.replace("_", " ")):
                out.append(n)
        return out[:2]

    async def async_ask(self, camera: str, force: bool = False) -> dict[str, Any]:
        """If Frigate kept a face from this camera's latest visit that it couldn't name, ask the phones who it was."""
        if not self.teaching and not force:
            return {"skipped": "face teaching is off in Sentient's settings"}
        now = dt_util.utcnow()
        day = dt_util.now().date().isoformat()
        if self._today[0] != day:
            self._today = (day, 0)
        if not force and ((self._last_ask and now - self._last_ask < ASK_GAP) or self._today[1] >= ASK_DAILY):
            return {"skipped": "asked recently"}
        if not frigate_url(self.hass):
            return {"error": "Frigate isn't set up in Home Assistant"}
        apps = phone_apps(self.hass, self.options)
        if not apps:
            return {"error": "no phone to ask — choose the phones under Phone notifications"}
        faces = await self._get("faces")
        if not isinstance(faces, dict) or "train" not in faces:
            return {"error": "Frigate's face recognition is off, or has no faces yet"}
        known = sorted(k for k in faces if k != "train")
        events = await self._get("events", {"cameras": camera, "labels": "person", "limit": "20",
                                            "after": str(int(now.timestamp()) - 1800)}) or []
        best: tuple[float, str, str, str] | None = None  # score, file, event, guess
        for ev in events:
            for f in faces.get("train") or []:
                if f.startswith(f"{ev['id']}-") and f not in self._asked:
                    guess, score = parse_face_file(f, ev["id"])
                    if best is None or score > best[0]:
                        best = (score, f, ev["id"], guess)
        if best is None:
            return {"nothing": "no unnamed face from that visit"}
        _, file, event_id, guess = best
        names = self._names(known, guess)
        token = uuid.uuid4().hex[:10]
        self._asks[token] = {"file": file, "names": names, "camera": camera}
        while len(self._asks) > 20:
            self._asks.pop(next(iter(self._asks)))
        self._asked = (self._asked + [file])[-200:]
        self._last_ask = now
        self._today = (day, self._today[1] + 1)
        actions = [{"action": f"{ACTION}_{token}_{i}", "title": n.replace("_", " ")} for i, n in enumerate(names)]
        # phones show three buttons at most, so "Someone else…" (type a name, or "no") is always one of them
        actions.append({"action": f"{ACTION}_{token}_other", "title": "Someone else…", "behavior": "textInput",
                        "textInputButtonTitle": "Save", "textInputPlaceholder": "Their name — or 'no' if you don't know them"})
        if len(names) < 2:
            actions.append({"action": f"{ACTION}_{token}_forget", "title": "Not someone we know"})
        persona = self.options.get(CONF_PERSONA) or DEFAULT_PERSONA
        data = {"title": f"{persona} · Who's this?",
                "message": f"The {camera.replace('_', ' ')} camera saw someone it couldn't put a name to. "
                           "Tap who it was and it'll know them next time. Someone new, or a stranger? Tap "
                           "'Someone else…' and type their name, or 'no'.",
                "data": {"image": f"/api/frigate/notifications/{event_id}/thumbnail.jpg",
                         "tag": f"sentient-face-{token}", "actions": actions}}
        for app in apps:
            try:
                await self.hass.services.async_call("notify", app, data, blocking=True)
            except Exception as err:  # noqa: BLE001
                _LOGGER.warning("Sentient couldn't ask %s about a face: %s", app, err)
        return {"asked": file, "names": names}

    @callback
    def _answer(self, event: Event) -> None:
        action = str(event.data.get("action") or "")
        if not action.startswith(f"{ACTION}_"):
            return
        token, _, choice = action.removeprefix(f"{ACTION}_").partition("_")
        ask = self._asks.pop(token, None)
        if ask is None:
            return
        if choice == "forget":
            self.hass.async_create_task(self.async_forget([ask["file"]]), eager_start=False)
            return
        if choice == "other":
            name = str(event.data.get("reply_text") or "").strip()
        else:
            try:
                name = ask["names"][int(choice)]
            except (ValueError, IndexError):
                return
        if name.lower() in SKIP_WORDS:
            self.hass.async_create_task(self.async_forget([ask["file"]]), eager_start=False)
            return
        self.hass.async_create_task(self.async_teach(ask["file"], name), eager_start=False)

    async def async_teach(self, file: str, name: str) -> bool:
        """File this face under the name in Frigate's face library (making the person if they're new)."""
        clean = "_".join(w.capitalize() if w.islower() else w for w in re.split(r"[_\s]+", name.strip()) if w)
        clean = re.sub(r"[^\w-]", "", clean)[:40]
        if not clean:
            return False
        try:
            faces = await self._get("faces") or {}
            folder = next((k for k in faces if k != "train" and k.lower() == clean.lower()), None)
            if folder is None:
                folder = clean
                await self._post(f"faces/{folder}/create")
            ok = await self._post(f"faces/train/{folder}/classify", {"training_file": file})
        except Exception as err:  # noqa: BLE001
            _LOGGER.warning("Sentient couldn't teach Frigate a face: %s", err)
            return False
        if ok:
            self.taught += 1
            if (rt := self._rt()) is not None:
                rt.timeline.add("noticed", f"Learnt another photo of {folder.replace('_', ' ')}'s face", "face recognition")
        return ok

    # ----- forgetting strangers -------------------------------------------------------------
    async def async_forget(self, files: list[str]) -> bool:
        """Delete unnamed faces from Frigate's face library."""
        if not files:
            return True
        try:
            return await self._post("faces/train/delete", {"ids": files})
        except Exception as err:  # noqa: BLE001
            _LOGGER.warning("Sentient couldn't delete a face from Frigate: %s", err)
            return False

    @callback
    def _nightly(self, _now: datetime) -> None:
        if float(self.options.get(CONF_FACE_FORGET_HOURS) or 0) > 0:
            self.hass.async_create_task(self.async_forget_old(), eager_start=False)

    async def async_forget_old(self, force: bool = False) -> dict[str, Any]:
        """Delete every unnamed face older than the setting (nobody named it, so it's someone we don't know)."""
        hours = float(self.options.get(CONF_FACE_FORGET_HOURS) or 0)
        if hours <= 0 and not force:
            return {"skipped": "forgetting unnamed faces is off"}
        try:
            faces = await self._get("faces")
        except Exception as err:  # noqa: BLE001
            return {"error": f"couldn't reach Frigate: {err}"}
        if not isinstance(faces, dict):
            return {"error": "Frigate's face recognition is off"}
        cutoff = dt_util.utcnow().timestamp() - max(hours, 0) * 3600
        old = [f for f in faces.get("train") or [] if (t := face_file_time(f)) is not None and t < cutoff]
        for i in range(0, len(old), 100):
            await self.async_forget(old[i:i + 100])
        self._asked = [f for f in self._asked if f not in old]
        return {"forgotten": len(old), "kept": len(faces.get("train") or []) - len(old)}
