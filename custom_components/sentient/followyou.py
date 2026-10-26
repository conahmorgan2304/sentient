"""Messages that follow people, and closing loops.

**Follow you.** Something said where nobody was about (no one in any speaker's room), or held
back for quiet hours or a hush, is kept for a while. The next time someone turns up in a room
with a speaker, it's passed on there: "Earlier: the parcel came." Each message is passed on
once, and dropped after a set time.

**Closing loops.** Sentient checks back on things instead of forgetting them:
* a follow-up an agent set ("I'll check the living room's warmed up in an hour") — at the time
  it looks at the device and says how it went;
* a watch that ran out without what it was waiting for ("no sign of the post today").
Follow-ups go through the same queue when nobody's about, so they're never lost.
"""

from __future__ import annotations

from datetime import datetime, timedelta
import logging
from typing import Any, Callable
import uuid

from homeassistant.const import EVENT_STATE_CHANGED, STATE_ON
from homeassistant.core import Event, EventStateChangedData, HomeAssistant, callback
from homeassistant.helpers.event import async_call_later, async_track_point_in_utc_time
from homeassistant.helpers.storage import Store
from homeassistant.util import dt as dt_util

from .const import (
    CONF_FOLLOW_MINUTES,
    CONF_FOLLOW_UPS,
    CONF_FOLLOW_YOU,
    CONF_QUIET_END,
    DEFAULT_FOLLOW_MINUTES,
    DEFAULT_QUIET_END,
    DOMAIN,
    EVENT_WATCH_EXPIRED,
    PRIORITY_LOW,
)
from .presence import area_of

_LOGGER = logging.getLogger(__name__)

SETTLE_SECONDS = 8  # let them get into the room first
MAX_QUEUE = 8
SAY_AT_ONCE = 3
# things held back that are worth passing on later; everyday remarks aren't
_KEEP_SOURCES = ("watch", "reminder", "followup", "visitor", "door", "security", "parcel", "thought")


def next_quiet_end(now: datetime, quiet_end: str) -> datetime:
    """The next end of quiet hours after now (local)."""
    try:
        h, m, *_ = (int(x) for x in quiet_end.split(":"))
    except ValueError:
        h, m = 8, 0
    end = now.replace(hour=h, minute=m, second=0, microsecond=0)
    return end if end > now else end + timedelta(days=1)


def compose(items: list[dict[str, Any]]) -> str:
    """'Earlier: …' for things nobody heard; overnight thoughts said as they are."""
    said = [q["text"].rstrip(".!? ") + "." for q in items if q.get("source") != "thought"]
    thoughts = [q["text"].rstrip(".!? ") + "." for q in items if q.get("source") == "thought"]
    out = []
    if said:
        out.append(("Earlier: " if len(said) == 1 else "While you were away: ") + " ".join(said))
    if thoughts:
        out.append(("A thought from overnight: " if not said else "And a thought from overnight: ") + " ".join(thoughts))
    return " ".join(out)


class FollowYou:
    """A short queue of messages nobody heard, passed on when someone turns up."""

    def __init__(self, hass: HomeAssistant, entry_id: str, options: dict[str, Any], rt_getter: Callable[[], Any]) -> None:
        self.hass = hass
        self.options = options
        self._rt = rt_getter
        self._store: Store[dict[str, Any]] = Store(hass, 1, f"{DOMAIN}.{entry_id}.followyou")
        self.queue: list[dict[str, Any]] = []
        self._unsub = None
        self._pending = None

    async def async_start(self) -> None:
        data = await self._store.async_load() or {}
        self.queue = data.get("queue", [])
        self._unsub = self.hass.bus.async_listen(EVENT_STATE_CHANGED, self._changed)

    @callback
    def async_stop(self) -> None:
        if self._unsub:
            self._unsub()
            self._unsub = None
        if self._pending:
            self._pending()
            self._pending = None

    @callback
    def _save(self) -> None:
        self._store.async_delay_save(lambda: {"queue": self.queue}, 5)

    @property
    def enabled(self) -> bool:
        return bool(self.options.get(CONF_FOLLOW_YOU, True))

    # ----- keeping -----------------------------------------------------------------
    @callback
    def unheard(self, text: str, priority: str, source: str | None, held: str | None) -> None:
        """From the speech router: said where nobody was, or held back for quiet/hush."""
        if not self.enabled or not text or priority == PRIORITY_LOW:
            return
        src = (source or "").lower()
        if src.startswith(("followyou", "critical", "briefing", "welcome", "curious")):
            return
        if held and not src.startswith(_KEEP_SOURCES):
            return  # a passing remark held back at night isn't worth repeating in the morning
        self.keep(text, after_quiet=bool(held), source=source)

    @callback
    def keep(self, text: str, after_quiet: bool = False, source: str | None = None,
             not_before: datetime | None = None, hours: float | None = None) -> None:
        now = dt_util.now()
        start = not_before or now
        if after_quiet:
            start = max(start, next_quiet_end(now, self.options.get(CONF_QUIET_END) or DEFAULT_QUIET_END))
        minutes = float(self.options.get(CONF_FOLLOW_MINUTES, DEFAULT_FOLLOW_MINUTES))
        expires = start + (timedelta(hours=hours) if hours else timedelta(minutes=minutes))
        self.queue = [q for q in self.queue if q["text"].lower() != text.lower()]
        self.queue.append({
            "id": uuid.uuid4().hex[:8], "text": text, "source": source,
            "from": dt_util.as_utc(start).isoformat(), "expires": dt_util.as_utc(expires).isoformat(),
            "kept": dt_util.utcnow().isoformat(),
        })
        self.queue = self.queue[-MAX_QUEUE:]
        self._save()

    def _live(self) -> list[dict[str, Any]]:
        now = dt_util.utcnow().isoformat()
        before = len(self.queue)
        self.queue = [q for q in self.queue if q["expires"] > now]
        if len(self.queue) != before:
            self._save()
        return [q for q in self.queue if q["from"] <= now]

    def listing(self) -> list[dict[str, Any]]:
        return [{"text": q["text"], "kept": q["kept"], "expires": q["expires"]} for q in self.queue]

    def take_thoughts(self) -> list[str]:
        """Overnight thoughts due now, taken out of the queue (the briefing says them instead)."""
        live = [q for q in self._live() if q.get("source") == "thought"]
        if live:
            ids = {q["id"] for q in live}
            self.queue = [q for q in self.queue if q["id"] not in ids]
            self._save()
        return [q["text"] for q in live]

    def pending_thoughts(self) -> list[str]:
        return [q["text"] for q in self._live() if q.get("source") == "thought"]

    # ----- passing on ----------------------------------------------------------------
    @callback
    def _changed(self, event: Event[EventStateChangedData]) -> None:
        if not self.queue or self._pending is not None:
            return
        new, old = event.data["new_state"], event.data["old_state"]
        if new is None or new.state != STATE_ON or (old is not None and old.state == STATE_ON):
            return
        rt = self._rt()
        if rt is None or not rt.tracker.is_presence(new):
            return
        area = area_of(self.hass, new.entity_id)
        speaker = next((s for s in rt.speech.speakers if area and area_of(self.hass, s) == area), None)
        if speaker is None:
            return

        @callback
        def _go(_now) -> None:
            self._pending = None
            self.hass.async_create_task(self.async_pass_on(speaker), eager_start=False)

        self._pending = async_call_later(self.hass, SETTLE_SECONDS, _go)

    async def async_pass_on(self, speaker: str) -> bool:
        rt = self._rt()
        if rt is None or rt.speech.quiet_now() or rt.speech.guest_mode:
            return False
        live = self._live()[:SAY_AT_ONCE]
        if not live:
            return False
        text = compose(live)
        try:
            result = await rt.speech.async_say(text, "normal", [speaker], source="followyou")
        except Exception as err:  # noqa: BLE001
            _LOGGER.debug("Could not pass on: %s", err)
            return False
        if result.spoken:
            done = {q["id"] for q in live}
            self.queue = [q for q in self.queue if q["id"] not in done]
            self._save()
        return result.spoken


class Loops:
    """Follow-ups: check back on something later and say how it went."""

    def __init__(self, hass: HomeAssistant, entry_id: str, options: dict[str, Any], rt_getter: Callable[[], Any]) -> None:
        self.hass = hass
        self.options = options
        self._rt = rt_getter
        self._store: Store[dict[str, Any]] = Store(hass, 1, f"{DOMAIN}.{entry_id}.loops")
        self.items: dict[str, dict[str, Any]] = {}
        self._timers: dict[str, Callable] = {}
        self._unsub = None

    async def async_start(self) -> None:
        data = await self._store.async_load() or {}
        self.items = {i["id"]: i for i in data.get("items", [])}
        for item in list(self.items.values()):
            self._arm(item)
        self._unsub = self.hass.bus.async_listen(EVENT_WATCH_EXPIRED, self._watch_expired)

    @callback
    def async_stop(self) -> None:
        if self._unsub:
            self._unsub()
            self._unsub = None
        for unsub in self._timers.values():
            unsub()
        self._timers.clear()

    @callback
    def _save(self) -> None:
        self._store.async_delay_save(lambda: {"items": list(self.items.values())}, 2)

    @property
    def enabled(self) -> bool:
        return bool(self.options.get(CONF_FOLLOW_UPS, True))

    def add(
        self, minutes: float, about: str, entity: str | None = None, state: str | None = None,
        above: float | None = None, below: float | None = None, if_done: str | None = None,
        if_not: str | None = None, speaker: str | None = None,
    ) -> dict[str, Any]:
        due = dt_util.utcnow() + timedelta(minutes=max(1.0, min(float(minutes), 24 * 60)))
        item = {
            "id": uuid.uuid4().hex[:8], "about": about.strip()[:160], "due": due.isoformat(),
            "entity": entity, "state": state, "above": above, "below": below,
            "if_done": (if_done or "").strip(), "if_not": (if_not or "").strip(), "speaker": speaker,
        }
        self.items[item["id"]] = item
        self._arm(item)
        self._save()
        return self.summary(item)

    def summary(self, item: dict[str, Any]) -> dict[str, Any]:
        due = dt_util.as_local(dt_util.parse_datetime(item["due"]))
        return {"id": item["id"], "about": item["about"], "at": f"{due:%H:%M}"}

    def listing(self) -> list[dict[str, Any]]:
        return [self.summary(i) for i in sorted(self.items.values(), key=lambda i: i["due"])]

    def cancel(self, id_or_words: str) -> dict[str, Any] | None:
        needle = id_or_words.lower()
        item = self.items.get(id_or_words) or next((i for i in self.items.values() if needle in i["about"].lower()), None)
        if item is None:
            return None
        self.items.pop(item["id"], None)
        if unsub := self._timers.pop(item["id"], None):
            unsub()
        self._save()
        return self.summary(item)

    def _arm(self, item: dict[str, Any]) -> None:
        when = dt_util.parse_datetime(item["due"]) or dt_util.utcnow()
        when = max(when, dt_util.utcnow() + timedelta(seconds=5))

        @callback
        def _due(_now) -> None:
            self._timers.pop(item["id"], None)
            self.hass.async_create_task(self.async_check(item["id"]), eager_start=False)

        self._timers[item["id"]] = async_track_point_in_utc_time(self.hass, _due, when)

    def outcome(self, item: dict[str, Any]) -> tuple[bool | None, str]:
        """(worked?, what to say). None when there's nothing to check, just a reminder to look."""
        entity = item.get("entity")
        st = self.hass.states.get(entity) if entity else None
        if st is None:
            return None, item.get("if_done") or item["about"]
        ok = True
        try:
            if item.get("state") is not None:
                ok = st.state.lower() == str(item["state"]).lower()
            if item.get("above") is not None:
                ok = ok and float(st.state) >= float(item["above"])
            if item.get("below") is not None:
                ok = ok and float(st.state) <= float(item["below"])
        except (TypeError, ValueError):
            ok = False
        unit = st.attributes.get("unit_of_measurement") or ""
        now = f"{st.name} is {st.state}{(' ' + unit) if unit else ''}"
        if ok:
            return True, item.get("if_done") or f"{item['about']}: done — {now}."
        return False, item.get("if_not") or f"{item['about']}: not yet — {now}."

    async def async_check(self, item_id: str) -> None:
        item = self.items.pop(item_id, None)
        self._save()
        rt = self._rt()
        if item is None or rt is None:
            return
        worked, text = self.outcome(item)
        rt.timeline.add("loop", text, "follow-up" + ("" if worked is None else (" · done" if worked else " · not done")))
        await self.async_tell(text, item.get("speaker"))

    async def async_tell(self, text: str, speaker: str | None = None) -> None:
        """Say it now if that's allowed, else keep it for when someone's about."""
        rt = self._rt()
        if rt is None:
            return
        targets = None
        if speaker and (found := rt.speech.find_speaker(speaker)):
            targets = [found]
        if rt.speech.quiet_now():
            rt.followyou.keep(text, after_quiet=True, source="followup")
            return
        try:
            result = await rt.speech.async_say(text, "normal", targets, source="followup")
        except Exception as err:  # noqa: BLE001 - a speaker gone missing: keep it for later
            _LOGGER.debug("Follow-up not said: %s", err)
            rt.followyou.keep(text, source="followup")
            return
        if not result.spoken and result.reason not in ("duplicate", "quiet_hours", "hushed"):
            rt.followyou.keep(text, source="followup")

    @callback
    def _watch_expired(self, event: Event) -> None:
        """A watch ran out with nothing seen: close the loop rather than go silent."""
        if not self.enabled:
            return
        what = (event.data.get("what") or "").strip().rstrip(".")
        if not what:
            return
        text = f"No sign of {what} — I've stopped keeping an eye out." if len(what) < 60 else f"Nothing came of watching for {what}."
        rt = self._rt()
        if rt is not None:
            rt.timeline.add("loop", text, "watch ended")
        self.hass.async_create_task(self.async_tell(text), eager_start=False)
