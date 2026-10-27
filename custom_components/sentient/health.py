"""House health: what's offline, what's running flat, what wants updating.

A local check every few minutes — no AI. Devices are reported as a whole
("Hallway tablet is offline") rather than entity by entity, and only once
every one of their entities has been unavailable for a while, so a brief
blip doesn't count.
"""

from __future__ import annotations

from datetime import datetime, timedelta
from typing import Any, Callable

from homeassistant.const import STATE_UNAVAILABLE
from homeassistant.core import HomeAssistant, callback
from homeassistant.helpers import device_registry as dr
from homeassistant.helpers import entity_registry as er
from homeassistant.helpers.event import async_track_time_interval
from homeassistant.util import dt as dt_util

from .const import (
    CONF_BATTERY_THRESHOLD,
    CONF_HEALTH_IGNORE,
    DEFAULT_BATTERY_THRESHOLD,
    EVENT_HEALTH_CHANGED,
)

OFFLINE_AFTER = timedelta(minutes=10)
CHECK_EVERY = timedelta(minutes=5)
LOOK_BACK = timedelta(days=7)  # an entity that hasn't had a real reading in this long isn't evidence
RECHECK = timedelta(hours=6)
SPEECH_FAILED_FOR = timedelta(hours=6)  # a speaker that failed shows until it works again, or this long
STUCK_ON = timedelta(hours=6)  # a motion sensor "on" this long is stuck
STUCK_QUIET = timedelta(days=3)  # ...or one that's seen nothing this long while others have


class HouseHealth:
    """Keeps a short list of problems worth knowing about."""

    def __init__(self, hass: HomeAssistant, options: dict[str, Any], rt_getter: Callable[[], Any] | None = None) -> None:
        self.hass = hass
        self.options = options
        self._rt = rt_getter
        self.issues: list[dict[str, Any]] = []
        self.updates: list[str] = []
        self._unsub = None
        self._never: dict[str, datetime] = {}  # entity -> until when it counts as "never has a reading"
        self._checked: dict[str, datetime] = {}  # entity -> when its history was last looked at
        self._went_off: dict[str, datetime] = {}  # entity -> when it last lost a real reading (from history)
        self._checking = False

    @callback
    def async_start(self) -> None:
        self._unsub = async_track_time_interval(self.hass, self._tick, CHECK_EVERY)
        self.hass.loop.call_soon(self.async_check)

    @callback
    def async_stop(self) -> None:
        if self._unsub:
            self._unsub()
            self._unsub = None

    @callback
    def _tick(self, _now: datetime) -> None:
        self.async_check()

    @callback
    def async_check(self) -> list[dict[str, Any]]:
        ignore = set(self.options.get(CONF_HEALTH_IGNORE) or [])
        threshold = float(self.options.get(CONF_BATTERY_THRESHOLD, DEFAULT_BATTERY_THRESHOLD))
        now = dt_util.utcnow()
        ent_reg = er.async_get(self.hass)
        dev_reg = dr.async_get(self.hass)

        # devices that report a lost connection, or whose every main entity has been unavailable a while
        by_device: dict[str, list] = {}
        links: dict[str, list] = {}
        for entry in ent_reg.entities.values():
            if not entry.device_id or entry.disabled_by:
                continue
            if entry.entity_category is None and entry.domain != "update":
                by_device.setdefault(entry.device_id, []).append(entry.entity_id)
            if entry.domain == "binary_sensor":
                links.setdefault(entry.device_id, []).append(entry.entity_id)
        offline = []
        unverified: list[str] = []
        for device_id, entity_ids in by_device.items():
            if device_id in ignore or any(e in ignore for e in entity_ids):
                continue
            # entities that are always unavailable (e.g. a "last seen" sensor that never got a value) aren't evidence
            entity_ids = [e for e in entity_ids if not (e in self._never and self._never[e] > now)]
            states = [self.hass.states.get(e) for e in entity_ids]
            states = [s for s in states if s is not None]
            if not states:
                continue
            link = [s for s in self._connectivity(links.get(device_id, [])) if s.state == "off"]
            if link:  # the device says it has lost its connection
                since = max(s.last_changed for s in link)
            elif any(s.state != STATE_UNAVAILABLE for s in states):
                continue
            else:
                since = max(s.last_changed for s in states)
                # after a restart everything "changed" at start-up: history knows when it really went
                if known := [self._went_off[e] for e in entity_ids if e in self._went_off]:
                    since = min(since, max(known))
            if not link:
                stale = [e for e in entity_ids if now - self._checked.get(e, datetime.min.replace(tzinfo=now.tzinfo)) > RECHECK]
                if stale:  # look at their history first; reported on a later check if they really went offline
                    unverified.extend(stale)
                    continue
            if now - since < OFFLINE_AFTER:
                continue
            device = dev_reg.async_get(device_id)
            if device is None or device.disabled_by:
                continue
            name = device.name_by_user or device.name or entity_ids[0]
            offline.append({"kind": "offline", "name": name, "since": since.isoformat(), "device_id": device_id})

        if unverified and not self._checking:
            self._checking = True
            self.hass.async_create_task(self._async_verify(unverified), eager_start=False)

        # batteries — one line per device (the lowest reading)
        low_by_device: dict[str, dict[str, Any]] = {}
        for st in self.hass.states.async_all(("sensor", "binary_sensor")):
            if st.entity_id in ignore or st.attributes.get("device_class") != "battery":
                continue
            entry = ent_reg.async_get(st.entity_id)
            if entry is not None and entry.platform == "mobile_app":
                continue  # phones and tablets charge every day: not a house problem
            if st.domain == "sensor":
                try:
                    level = float(st.state)
                except ValueError:
                    continue
                if level >= threshold:
                    continue
                item = {"kind": "battery", "name": st.name, "level": round(level), "entity_id": st.entity_id}
            elif st.state == "on":
                item = {"kind": "battery", "name": st.name, "level": None, "entity_id": st.entity_id}
            else:
                continue
            entry = ent_reg.async_get(st.entity_id)
            if entry and entry.device_id and entry.device_id in ignore:
                continue
            key = (entry.device_id if entry and entry.device_id else st.entity_id)
            have = low_by_device.get(key)
            if have is None or (item["level"] is not None and (have["level"] is None or item["level"] < have["level"])):
                low_by_device[key] = item
        # a battery sensor that's briefly unavailable (e.g. just after a restart) keeps its last reading
        for old in self.issues:
            if old["kind"] != "battery" or any(i["name"] == old["name"] for i in low_by_device.values()):
                continue
            st = self.hass.states.get(old["entity_id"])
            if st is None or st.state in ("unavailable", "unknown"):
                low_by_device[old["entity_id"]] = old
        low = list(low_by_device.values())

        updates = sorted(
            s.name for s in self.hass.states.async_all("update") if s.state == "on" and s.entity_id not in ignore
        )
        issues = (self._self_check(now, ignore) + sorted(offline, key=lambda i: i["since"])
                  + sorted(low, key=lambda i: (i["level"] is None, i["level"] or 0)))
        if issues != self.issues or updates != self.updates:
            self.issues = issues
            self.updates = updates
            self.hass.bus.async_fire(
                EVENT_HEALTH_CHANGED, {
                    "count": len(issues),
                    "problems": {f"{i['kind']}:{i['name']}": line for i, line in zip(issues, self.describe())},
                    "updates": updates,
                }
            )
        return issues

    async def _async_verify(self, entity_ids: list[str]) -> None:
        """Which of these unavailable entities had a real reading in the last week?"""
        now = dt_util.utcnow()
        try:
            if "recorder" in self.hass.config.components:
                from homeassistant.components.recorder import get_instance, history

                rows = await get_instance(self.hass).async_add_executor_job(
                    lambda: history.get_significant_states(
                        self.hass, now - LOOK_BACK, None, entity_ids, significant_changes_only=True, minimal_response=True
                    )
                )
                for e in entity_ids:
                    real, was_real = False, False
                    for st in rows.get(e, []):
                        state = st.state if hasattr(st, "state") else st.get("state")
                        is_real = state not in ("unavailable", "unknown", None)
                        if was_real and not is_real:
                            when = st.last_changed if hasattr(st, "last_changed") else st.get("last_changed")
                            if isinstance(when, (int, float)):
                                when = dt_util.utc_from_timestamp(when)
                            elif isinstance(when, str):
                                when = dt_util.parse_datetime(when)
                            if when is not None:
                                self._went_off[e] = when
                        real, was_real = real or is_real, is_real
                    if real:
                        self._never.pop(e, None)
                    else:
                        self._never[e] = now + RECHECK
            for e in entity_ids:
                self._checked[e] = now
        except Exception:  # noqa: BLE001 - fall back to counting them
            for e in entity_ids:
                self._checked[e] = now
        finally:
            self._checking = False
        self.async_check()

    def _connectivity(self, entity_ids: list[str]) -> list:
        """Connectivity sensors among a device's entities (any category)."""
        return [
            st for e in entity_ids
            if e.startswith("binary_sensor.") and (st := self.hass.states.get(e)) is not None
            and st.attributes.get("device_class") == "connectivity"
        ]

    def _self_check(self, now: datetime, ignore: set[str]) -> list[dict[str, Any]]:
        """Sentient's own parts: the AI, the speakers it talks through, and the sensors it relies on."""
        rt = self._rt() if self._rt else None
        if rt is None:
            return []
        out: list[dict[str, Any]] = []
        gate = getattr(rt, "ai", None)
        if gate is not None:
            if gate.failing:
                out.append({"kind": "ai", "name": "The AI", "detail": gate.last_error, "since": gate.last_error_at})
            if gate.capped:
                out.append({"kind": "limit", "name": "AI calls", "level": gate.used, "since": gate.capped_at})
        for label, (when, err) in list(getattr(rt.speech, "failures", {}).items()):
            if now - when < SPEECH_FAILED_FOR:
                out.append({"kind": "speaker", "name": label, "detail": err, "since": when.isoformat()})
        if (dog := getattr(rt, "watchdog", None)) is not None:
            out += [i for i in dog.issues() if i.get("entity_id") not in ignore]
        from .presence import inside_sensors

        sensors = [st for e in inside_sensors(self.hass, self.options)
                   if e not in ignore and (st := self.hass.states.get(e)) is not None and st.state not in ("unavailable", "unknown")]
        lively = any(now - st.last_changed < timedelta(hours=24) for st in sensors)
        for st in sensors:
            quiet = now - st.last_changed
            if st.state == "on" and quiet > STUCK_ON:
                out.append({"kind": "stuck", "name": st.name, "entity_id": st.entity_id, "since": st.last_changed.isoformat(), "on": True})
            elif st.state == "off" and lively and quiet > STUCK_QUIET:
                out.append({"kind": "stuck", "name": st.name, "entity_id": st.entity_id, "since": st.last_changed.isoformat()})
        return out

    def describe(self) -> list[str]:
        out = []
        now = dt_util.utcnow()
        for i in self.issues:
            if i["kind"] in ("wear", "fight", "frozen"):
                out.append(i["text"])
            elif i["kind"] == "ai":
                out.append("The AI hasn't answered the last few times — plain words for now")
            elif i["kind"] == "limit":
                out.append("Today's AI calls are used up — plain words until midnight")
            elif i["kind"] == "speaker":
                out.append(f"Couldn't speak on {i['name']}")
            elif i["kind"] == "stuck":
                since = dt_util.parse_datetime(i.get("since") or "") or now
                if i.get("on"):
                    out.append(f"{i['name']} has said there's movement for {round((now - since).total_seconds() / 3600)} hours")
                else:
                    out.append(f"{i['name']} hasn't seen anything for {max(1, (now - since).days)} days")
            elif i["kind"] == "offline":
                out.append(f"{i['name']} is offline")
            elif i["level"] is not None:
                out.append(f"{i['name']} is at {i['level']}%")
            else:
                out.append(f"{i['name']} is low")
        return out
