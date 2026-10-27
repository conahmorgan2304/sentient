"""Conversations: what was asked and said, across every speaker.

Home Assistant keeps each conversation's chat log only while it's going on. Sentient listens
to those logs for conversations that use the Sentient API (so its own background AI jobs never
count), and keeps a short record of each one — when, which room, what was asked and what was
said back. That lets any voice pick up where another left off ("what was I asking earlier?"),
and gives the overnight reflection something to think about. Kept for two weeks.
"""

from __future__ import annotations

from datetime import timedelta
import logging
import re
from typing import Any

from homeassistant.core import HomeAssistant, callback
from homeassistant.helpers import area_registry as ar, device_registry as dr
from homeassistant.helpers.event import async_track_time_interval
from homeassistant.helpers.storage import Store
from homeassistant.util import dt as dt_util

from .const import CONF_CONVERSATIONS, DOMAIN

_LOGGER = logging.getLogger(__name__)

KEEP_DAYS = 14
KEEP_MAX = 300
IDLE = timedelta(minutes=3)  # a conversation with nothing new for this long is finished
_WORD = re.compile(r"[a-z0-9']+")


def _short(text: str, limit: int = 160) -> str:
    text = " ".join((text or "").split())
    return text if len(text) <= limit else text[: limit - 1].rstrip() + "…"



# Sentient tools that change something (the rest only read)
SENTIENT_ACTIONS = {
    "sentient_remember", "sentient_forget", "sentient_correction", "sentient_announce", "sentient_watch",
    "sentient_remind", "sentient_cancel_watch", "sentient_adopt_routine", "sentient_forget_routine",
    "sentient_accept_offer", "sentient_alarm", "sentient_party", "sentient_quiet", "sentient_shush",
    "sentient_run_routine", "sentient_scene", "sentient_follow_up", "sentient_motion_lights", "sentient_spot",
}
ACTION_WORDS = {
    "HassTurnOn": "Turned on", "HassTurnOff": "Turned off", "HassLightSet": "Set", "HassToggle": "Toggled",
    "HassClimateSetTemperature": "Set the temperature of", "HassSetPosition": "Moved", "HassVacuumStart": "Started",
    "HassVacuumReturnToBase": "Sent home", "HassMediaPause": "Paused", "HassMediaUnpause": "Resumed",
    "HassSetVolume": "Set the volume of", "HassMediaNext": "Skipped on",
}

class Conversations:
    def __init__(self, hass: HomeAssistant, entry_id: str, options: dict[str, Any]) -> None:
        self.hass = hass
        self.options = options
        self._store: Store[dict[str, Any]] = Store(hass, 1, f"{DOMAIN}.{entry_id}.conversations")
        self.episodes: list[dict[str, Any]] = []  # newest first
        self._open: dict[str, dict[str, Any]] = {}  # conversation id -> episode being written
        self._ours: dict[str, dict[str, Any]] = {}  # conversation id -> room/device, for ones using Sentient
        self._unsubs: list = []
        self.timeline: Any = None  # set by the runtime: where voice actions are logged
        self.guard: Any = None  # set by the runtime: puts back what a voice switched by mistake

    async def async_start(self) -> None:
        data = await self._store.async_load() or {}
        self.episodes = data.get("episodes", [])
        try:
            from homeassistant.components.conversation.chat_log import async_subscribe_chat_logs
        except ImportError:  # older Home Assistant: nothing to listen to
            _LOGGER.debug("Chat log subscriptions not available")
            return
        self._unsubs.append(async_subscribe_chat_logs(self.hass, self._chat))
        self._unsubs.append(async_track_time_interval(self.hass, self._tidy, timedelta(minutes=1)))

    @callback
    def async_stop(self) -> None:
        for cid in list(self._open):
            self._finish(cid)
        while self._unsubs:
            self._unsubs.pop()()

    @callback
    def _save(self) -> None:
        self._store.async_delay_save(lambda: {"episodes": self.episodes}, 10)

    @property
    def enabled(self) -> bool:
        return bool(self.options.get(CONF_CONVERSATIONS, True))

    # ----- listening -------------------------------------------------------------------
    @callback
    def attach(self, conversation_id: str, device_id: str | None, platform: str | None = None) -> None:
        """Called when a conversation asks for the Sentient API: this one's worth keeping."""
        if conversation_id not in self._ours:
            self._ours[conversation_id] = {"room": self._room(device_id), "device_id": device_id, "via": platform}
            if (ep := self._open.get(conversation_id)) is not None:
                ep.update(room=self._ours[conversation_id]["room"], via=platform)

    def _room(self, device_id: str | None) -> str | None:
        if not device_id or (device := dr.async_get(self.hass).async_get(device_id)) is None:
            return None
        area = ar.async_get(self.hass).async_get_area(device.area_id) if device.area_id else None
        return area.name if area else device.name_by_user or device.name

    @callback
    def _chat(self, conversation_id: str, event_type: Any, data: dict[str, Any]) -> None:
        kind = str(event_type)
        if kind.endswith("deleted"):
            self._finish(conversation_id)
            return
        if not kind.endswith("content_added") or not self.enabled:
            return
        content = data.get("content") or {}
        if content.get("role") == "tool_result":
            self._tool_result(content, conversation_id)
            return
        role, text = content.get("role"), content.get("content")
        if role not in ("user", "assistant") or not text or not isinstance(text, str):
            return
        ep = self._open.get(conversation_id)
        if ep is None:
            ours = self._ours.get(conversation_id) or {}
            ep = self._open[conversation_id] = {
                "id": conversation_id[-10:], "start": dt_util.utcnow().isoformat(), "room": ours.get("room"),
                "via": ours.get("via"), "turns": [],
            }
        ep["turns"].append({"who": "them" if role == "user" else "me", "text": _short(text, 400)})
        ep["last"] = dt_util.utcnow().isoformat()
        del ep["turns"][:-12]

    @callback
    def _tool_result(self, content: dict[str, Any], conversation_id: str | None = None) -> None:
        """What a voice actually did: put each device action on the timeline, with what didn't work."""
        if self.timeline is None:
            return
        name = str(content.get("tool_name") or "").split("__")[-1]  # "assist__intent__HassTurnOff" -> "HassTurnOff"
        result = content.get("tool_result") or {}
        if name.startswith("sentient_"):
            if name not in SENTIENT_ACTIONS:
                return
            err = result.get("error") if isinstance(result, dict) else None
            args = content.get("tool_args") or {}
            what = next((str(v) for v in args.values() if isinstance(v, str)), "") if isinstance(args, dict) else ""
            self.timeline.add("voice", f"{name.removeprefix('sentient_').replace('_', ' ').capitalize()}"
                              + (f": {_short(what, 80)}" if what else ""), f"by voice{'; failed: ' + str(err) if err else ''}")
            return
        data = result.get("data") if isinstance(result, dict) else None
        if not isinstance(data, dict) or not ("success" in data or "failed" in data):
            return  # a question, not an action
        def names(items: Any) -> list[str]:
            return [str(t.get("name")) for t in items or [] if isinstance(t, dict) and t.get("name")]
        ok, bad = names(data.get("success")), names(data.get("failed"))
        if not ok and not bad:
            return
        verb = ACTION_WORDS.get(name) or re.sub(r"(?<!^)(?=[A-Z])", " ", name.removeprefix("Hass")).capitalize()
        detail = "by voice" + (f"; didn't work: {', '.join(bad)}" if bad else "")
        self.timeline.add("voice", f"{verb} {', '.join((ok or bad)[:4])}" + (f" and {len(ok) - 4} more" if len(ok) > 4 else ""), detail)
        if self.guard is not None and name in ("HassTurnOn", "HassTurnOff", "HassToggle"):
            ep = self._open.get(conversation_id or "") or {}
            said = next((t["text"] for t in reversed(ep.get("turns", [])) if t["who"] == "them"), "")
            if said:
                self.hass.async_create_task(self._async_guard(said, data.get("success") or []), eager_start=False)

    async def _async_guard(self, said: str, targets: list) -> None:
        restored = await self.guard.async_put_back(said, targets)
        if restored and self.timeline is not None:
            self.timeline.add("voice", f"Put back {', '.join(restored[:4])}" + (f" and {len(restored) - 4} more" if len(restored) > 4 else ""),
                              f"the voice switched more than was asked for (\"{_short(said, 60)}\")")

    @callback
    def _tidy(self, _now=None) -> None:
        cutoff = (dt_util.utcnow() - IDLE).isoformat()
        for cid, ep in list(self._open.items()):
            if ep.get("last", ep["start"]) < cutoff:
                self._finish(cid)
        oldest = (dt_util.utcnow() - timedelta(days=KEEP_DAYS)).isoformat()
        before = len(self.episodes)
        self.episodes = [e for e in self.episodes if e["start"] >= oldest][:KEEP_MAX]
        if len(self.episodes) != before:
            self._save()

    @callback
    def _finish(self, conversation_id: str) -> None:
        ep = self._open.pop(conversation_id, None)
        ours = self._ours.pop(conversation_id, None)
        if ep is None or ours is None:
            return  # not a conversation that used Sentient (a background AI job, say)
        if not any(t["who"] == "them" for t in ep["turns"]):
            return  # only Sentient talking (an announcement-style question nobody answered)
        ep.pop("last", None)
        self.episodes.insert(0, ep)
        del self.episodes[KEEP_MAX:]
        self._save()

    # ----- reading -----------------------------------------------------------------------
    def recent(self, hours: float = 24, limit: int = 50, exclude: str | None = None) -> list[dict[str, Any]]:
        """Finished and ongoing conversations, newest first."""
        cutoff = (dt_util.utcnow() - timedelta(hours=hours)).isoformat()
        open_eps = [
            {**ep, "ongoing": True} for cid, ep in self._open.items()
            if cid in self._ours and cid != exclude and any(t["who"] == "them" for t in ep["turns"])
        ]
        out = sorted(open_eps, key=lambda e: e["start"], reverse=True) + self.episodes
        return [e for e in out if e["start"] >= cutoff][:limit]

    def search(self, query: str | None, days: float = 7, limit: int = 8) -> list[dict[str, Any]]:
        eps = self.recent(hours=days * 24, limit=KEEP_MAX)
        words = {w for w in _WORD.findall((query or "").lower()) if len(w) > 2}
        if words:
            scored = []
            for ep in eps:
                hay = " ".join(t["text"] for t in ep["turns"]).lower()
                score = sum(1 for w in words if w in hay)
                if score:
                    scored.append((score, ep))
            scored.sort(key=lambda s: (s[0], s[1]["start"]), reverse=True)
            eps = [e for _, e in scored]
        return [self.describe(e) for e in eps[:limit]]

    def describe(self, ep: dict[str, Any]) -> dict[str, Any]:
        when = dt_util.as_local(dt_util.parse_datetime(ep["start"]))
        return {
            "when": f"{when:%a %H:%M}", "room": ep.get("room"),
            "conversation": [f"{'They' if t['who'] == 'them' else 'You'}: {_short(t['text'], 220)}" for t in ep["turns"]],
        }

    def prompt_lines(self, exclude: str | None = None, hours: float = 12, limit: int = 4) -> list[str]:
        out = []
        for ep in self.recent(hours=hours, limit=limit, exclude=exclude):
            when = dt_util.as_local(dt_util.parse_datetime(ep["start"]))
            asked = next((t["text"] for t in ep["turns"] if t["who"] == "them"), "")
            said = next((t["text"] for t in reversed(ep["turns"]) if t["who"] == "me"), "")
            room = f", {ep['room']}" if ep.get("room") else ""
            line = f"- {when:%H:%M}{room}: asked \"{_short(asked, 110)}\""
            if said:
                line += f" — you said \"{_short(said, 110)}\""
            out.append(line)
        return out

    def count_today(self) -> int:
        start = dt_util.start_of_local_day().isoformat()
        return sum(1 for e in self.recent(hours=24, limit=KEEP_MAX) if e["start"] >= start)
