"""Ongoing situations: what's going on in the house, each with a start, its evidence, how sure, and what ends it.

The brain's "situations" used to be a few lines worked out afresh on every turn ("Conah just got home."), with no
idea when something began or whether it was over, so the voice could say the same thing twice or contradict
itself ("the heating's fine" after an hour of it not getting anywhere). This keeps a short book of them:

* **arriving** — someone's person entity went home; over 20 minutes later;
* **heating struggling** — a thermostat Sentient looks after has been calling for heat for an hour and is still
  a degree or more short, rising slowly; over when it's within half a degree, the target drops, or it's off;
* **busy garden** — the camera-security "inside the boundary" sensors (garden, shed) went on three or more times
  in half an hour; over after half an hour with nothing new;
* **someone on the cameras** — a security incident still collecting evidence (the incidents' own start, cameras
  and end are used, not copied); over when the incident is;
* **a device that keeps failing** — offline now, and dropped offline three or more times this week (the watchdog's
  record); over when it's back;
* **the house asleep** — from sleep detection, with when it started; over when the house wakes.

Each is told to the voice once as ongoing (with its start and evidence) and, for an hour after it ends, as over —
so a question about it gets "that's sorted now" rather than old news. Nothing here speaks or acts; nothing is
stored (it's rebuilt from the house after a restart). No AI. Part of Settings › Memory & learning › Situations.
"""

from __future__ import annotations

from datetime import datetime, timedelta
from typing import Any, Callable

from homeassistant.core import HomeAssistant
from homeassistant.util import dt as dt_util

ARRIVED_FOR = timedelta(minutes=20)
STRUGGLE_AFTER = timedelta(hours=1)
STRUGGLE_SHORT = 1.0  # degrees below target
STRUGGLE_RISE = 1.0  # risen less than this since it started calling for heat
GARDEN_WINDOW = timedelta(minutes=30)
GARDEN_TIMES = 3
KEEP_ENDED = timedelta(hours=1)
FAILS = 3


def _num(value: Any) -> float | None:
    from .numeric import finite_float

    return finite_float(value)


def _hm(when: datetime) -> str:
    return dt_util.as_local(when).strftime("%H:%M")


class SituationBook:
    def __init__(self, hass: HomeAssistant, options_getter: Callable[[], dict[str, Any]], rt_getter: Callable[[], Any]) -> None:
        self.hass = hass
        self._options = options_getter
        self._rt = rt_getter
        self.items: dict[str, dict[str, Any]] = {}  # key -> {kind, title, started, evidence, sure, ends_when, ended}
        self._calling: dict[str, tuple[datetime, float | None]] = {}  # heater -> (calling for heat since, temp then)

    # ----- what's true now ----------------------------------------------------------------------------------------
    def _arrivals(self, now: datetime) -> dict[str, dict[str, Any]]:
        out = {}
        for st in self.hass.states.async_all("person"):
            if st.state == "home" and now - st.last_changed < ARRIVED_FOR:
                out[f"arrived:{st.entity_id}"] = {
                    "kind": "arriving", "title": f"{st.name} arriving home", "started": st.last_changed,
                    "evidence": [f"their person entity went home at {_hm(st.last_changed)}"],
                    "sure": "from Home Assistant's people (a phone, usually)",
                    "ends_when": "20 minutes after they got in"}
        return out

    def _heating(self, now: datetime) -> dict[str, dict[str, Any]]:
        from .const import CONF_HEATING_ENTITIES

        out = {}
        heaters = [h for h in self._options().get(CONF_HEATING_ENTITIES) or [] if h.startswith("climate.")]
        for heater in heaters:
            st = self.hass.states.get(heater)
            target = _num(st.attributes.get("temperature")) if st is not None else None
            current = _num(st.attributes.get("current_temperature")) if st is not None else None
            calling = (st is not None and st.state not in ("off", "unavailable", "unknown") and target is not None
                       and current is not None and target - current >= STRUGGLE_SHORT
                       and st.attributes.get("hvac_action") in ("heating", None))
            if not calling:
                self._calling.pop(heater, None)
                continue
            since, then = self._calling.setdefault(heater, (now, current))
            rose = current - then if then is not None else 0.0
            if now - since >= STRUGGLE_AFTER and rose < STRUGGLE_RISE:
                minutes = round((now - since).total_seconds() / 60)
                out[f"heating:{heater}"] = {
                    "kind": "heating struggling", "title": f"{st.name} struggling to reach {target:g}°",
                    "started": since,
                    "evidence": [f"calling for heat for {minutes} minutes", f"now {current:g}°, target {target:g}°",
                                 f"up {rose:.1f}° since {_hm(since)}"],
                    "sure": "from the thermostat's own readings (a window open or a cold night can do this)",
                    "ends_when": f"it's within half a degree of {target:g}°, the target drops, or it's off"}
        return out

    def _garden(self, now: datetime) -> dict[str, dict[str, Any]]:
        from .const import CONF_CAMSEC_INSIDE

        rt = self._rt()
        ledger = getattr(getattr(rt, "brain", None), "ledger", None) if rt is not None else None
        sensors = set(self._options().get(CONF_CAMSEC_INSIDE) or [])
        if ledger is None or not sensors:
            return {}
        rows = [r for r in ledger.recent_rows((now - GARDEN_WINDOW).timestamp()) if r["entity"] in sensors and r["new"] == "on"]
        previous = self.items.get("garden")
        active = previous is not None and previous.get("ended") is None
        if not rows or (not active and len(rows) < GARDEN_TIMES):
            return {}
        first = datetime.fromtimestamp(min(r["ts"] for r in rows), tz=dt_util.UTC)
        names = sorted({r.get("name") or r["entity"] for r in rows})
        return {"garden": {
            "kind": "busy garden", "title": "The garden's busy", "started": first,
            "evidence": [f"{len(rows)} detections in half an hour ({', '.join(names[:3])})"],
            "sure": "camera detections — a person, an animal or a shadow can all set them off",
            "ends_when": "half an hour with nothing new"}}

    def _incidents(self, now: datetime) -> dict[str, dict[str, Any]]:
        from .incidents import GAP

        rt = self._rt()
        incidents = getattr(rt, "incidents", None) if rt is not None else None
        if incidents is None or not incidents.enabled:
            return {}
        out = {}
        for item in incidents.items:
            start, end = dt_util.parse_datetime(item.get("start") or ""), dt_util.parse_datetime(item.get("end") or "")
            if start is None or end is None or now - end > GAP:
                continue
            sources = list(dict.fromkeys(item.get("sources") or []))
            out[f"incident:{item.get('id')}"] = {
                "kind": "someone on the cameras", "title": "Someone on the cameras", "started": start,
                "evidence": [f"seen by {', '.join(sources[:4])}", f"last at {_hm(end)}"],
                "sure": "the route between cameras isn't confirmed",
                "ends_when": "a few minutes with no new sighting (sentient_incidents has the detail)"}
        return out

    def _failing(self, now: datetime) -> dict[str, dict[str, Any]]:
        rt = self._rt()
        watchdog = getattr(rt, "watchdog", None) if rt is not None else None
        health = getattr(rt, "health", None) if rt is not None else None
        if watchdog is None or health is None:
            return {}
        offline = {i["name"] for i in getattr(health, "all_issues", []) if i.get("kind") == "offline"}
        out = {}
        for name, times in (watchdog.drops or {}).items():
            if name in offline and len(times) >= FAILS:
                last = dt_util.parse_datetime(times[-1]) or now
                out[f"failing:{name}"] = {
                    "kind": "device keeps failing", "title": f"{name} keeps dropping offline", "started": last,
                    "evidence": [f"offline now; dropped {len(times)} times this week"],
                    "sure": "from Home Assistant's own availability",
                    "ends_when": "it's back online"}
        return out

    def _asleep(self, now: datetime) -> dict[str, dict[str, Any]]:
        rt = self._rt()
        sleep = getattr(rt, "sleep", None) if rt is not None else None
        if sleep is None or not getattr(sleep, "asleep", False):
            return {}
        since = dt_util.parse_datetime(getattr(sleep, "since", None) or "") or now
        return {"asleep": {
            "kind": "the house asleep", "title": "The house is asleep", "started": since,
            "evidence": [f"nobody about since {_hm(since)}"], "sure": "from movement — it can't see who's awake in bed",
            "ends_when": "someone gets up"}}

    # ----- the book ---------------------------------------------------------------------------------------------
    def update(self, now: datetime | None = None) -> None:
        now = now or dt_util.utcnow()
        found: dict[str, dict[str, Any]] = {}
        uncertain = set()
        detectors = (("arrived:", self._arrivals), ("heating:", self._heating), ("garden", self._garden),
                     ("incident:", self._incidents), ("failing:", self._failing), ("asleep", self._asleep))
        for prefix, detector in detectors:
            try:
                found.update(detector(now))
            except Exception:  # noqa: BLE001 - an unavailable detector does not prove a situation ended
                uncertain.add(prefix)
        for key, item in found.items():
            known = self.items.get(key)
            if known is None or known.get("ended"):
                self.items[key] = {**item, "ended": None}
            else:
                known.update(evidence=item["evidence"], title=item["title"])  # still going: its start stays
        for key, item in list(self.items.items()):
            if key not in found and not item.get("ended") and not any(key.startswith(p) for p in uncertain):
                item["ended"] = (min(now, item["started"] + ARRIVED_FOR) if key.startswith("arrived:") else now)
            if item.get("ended") and now - item["ended"] > KEEP_ENDED:
                self.items.pop(key)

    def lines(self, now: datetime | None = None) -> list[str]:
        self.update(now)
        ongoing, over = [], []
        for item in sorted(self.items.values(), key=lambda i: i["started"], reverse=True):
            if item.get("ended"):
                over.append(f"Over since {_hm(item['ended'])}: {item['title']} (began {_hm(item['started'])}).")
            else:
                ongoing.append(f"Going on since {_hm(item['started'])}: {item['title']} — {'; '.join(item['evidence'])}. "
                               f"How sure: {item['sure']}. Ends when {item['ends_when']}.")
        return ongoing[:4] + over[:2]

    def listing(self) -> list[dict[str, Any]]:
        self.update()
        return [{"kind": i["kind"], "title": i["title"], "started": i["started"].isoformat(),
                 "evidence": i["evidence"], "sure": i["sure"], "ends_when": i["ends_when"],
                 "ended": i["ended"].isoformat() if i.get("ended") else None}
                for i in sorted(self.items.values(), key=lambda i: i["started"], reverse=True)]
