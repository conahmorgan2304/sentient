"""How it's used: which optional features are earning their keep, from what Sentient already sees.

A feature counts as used when one of its voice tools is called or it says something (by the source it
speaks under). Kept per day for 35 days — names and counts only. The "How it's used" page lists what's on
and used this month, what's on but never used (worth switching off to keep things simple), and what's off.
"""

from __future__ import annotations

from datetime import timedelta
from typing import Any

from homeassistant.core import Event, HomeAssistant, callback
from homeassistant.helpers.storage import Store
from homeassistant.util import dt as dt_util

from .const import DOMAIN, EVENT_SPOKE

KEEP_DAYS = 35
# feature id -> (name, the setting that switches it, settings page, tools, speech sources (prefixes))
FEATURES: dict[str, tuple[str, str | None, str, tuple[str, ...], tuple[str, ...]]] = {
    "briefings": ("Briefings", "briefings", "briefing", ("sentient_briefing",), ("briefing",)),
    "welcome": ("Welcome home", "welcome_home", "noticing", (), ("welcome",)),
    "visitors": ("Visitors at the door", "announce_visitors", "door", ("sentient_visitors",), ("visitors", "parcel")),
    "offers": ("Offers", None, "offers", ("sentient_accept_offer",), ("offer",)),
    "alarms": ("Alarms and announcements", None, "alarms", ("sentient_alarm",), ("alarm", "announce")),
    "routines": ("Learned routines", "suggest_routines", "noticing", ("sentient_routines", "sentient_adopt_routine"), ("learning",)),
    "noticing": ("Remarks about what changed", None, "noticing", (), ("notice",)),
    "curiosity": ("Curious questions", "curiosity", "mind", (), ("curious",)),
    "foresight": ("A look at tomorrow", "foresight", "mind", (), ("thought",)),
    "watches": ("Watches and reminders", None, "mind", ("sentient_watch", "sentient_remind"), ("watch", "reminder")),
    "cameras": ("Looking through cameras", None, "spots", ("sentient_look", "sentient_spot", "sentient_what_happened"), ()),
    "books": ("Books and stories", "books", "reading", ("sentient_book",), ()),
    "games": ("Story games", "games", "games", ("sentient_game", "sentient_game_set", "sentient_game_log"), ()),
    "recipes": ("Recipes", "recipes", "reading", ("sentient_recipe",), ()),
    "manuals": ("Manuals", "manuals", "reading", ("sentient_manual",), ()),
    "goals": ("Goals", "goals", "mind", ("sentient_goal",), ()),
    "music": ("Music by asking", "music", "voice", ("sentient_music",), ()),
    "search": ("Web search", "web_search", "search", ("sentient_search",), ()),
    "party": ("Light shows", None, "party", ("sentient_party",), ()),
    "heating": ("Heating", "preheat", "heating", ("sentient_heating",), ()),
    "holiday": ("Holiday mode", "holiday_mode", "away", ("sentient_holiday",), ()),
    "maintenance": ("Maintenance", None, "maintenance", ("sentient_maintenance",), ("maintenance",)),
    "appliances": ("Appliances finishing", "appliance_announce", "appliances", (), ("appliance",)),
    "bins": ("Bins check", "bins_check", "bins", (), ("bins",)),
    "rain": ("Rain warnings", "rain_warnings", "rain", (), ("rain",)),
    "animals": ("Animals", "hutch_check", "animals", (), ("animals",)),
    "autooff": ("Auto-off", None, "autooff", (), ("autooff",)),
    "cheap_power": ("Cheap-electricity nudges", None, "energy", (), ("cheap electricity",)),
    "rules": ("Rules", "rules", "mind", ("sentient_make_rule",), ("rule",)),
    "energy": ("Energy", "energy_insight", "energy", ("sentient_energy",), ()),
}


class Usage:
    def __init__(self, hass: HomeAssistant, entry_id: str, options: dict[str, Any], rt_getter: Any) -> None:
        self.hass = hass
        self.options = options
        self._rt = rt_getter
        self._store: Store[dict[str, Any]] = Store(hass, 1, f"{DOMAIN}.{entry_id}.usage")
        self.days: dict[str, dict[str, int]] = {}  # day -> feature -> count
        self.last: dict[str, str] = {}  # feature -> last day used
        self._by_tool = {t: f for f, spec in FEATURES.items() for t in spec[3]}
        self._unsub = None

    async def async_start(self) -> None:
        data = await self._store.async_load() or {}
        self.days, self.last = dict(data.get("days") or {}), dict(data.get("last") or {})
        self._unsub = self.hass.bus.async_listen(EVENT_SPOKE, self._spoke)

    @callback
    def async_stop(self) -> None:
        if self._unsub:
            self._unsub()
            self._unsub = None

    def _save(self) -> None:
        self._store.async_delay_save(lambda: {"days": self.days, "last": self.last}, 30)

    @callback
    def hit(self, feature: str) -> None:
        today = dt_util.now().date().isoformat()
        day = self.days.setdefault(today, {})
        day[feature] = day.get(feature, 0) + 1
        self.last[feature] = today
        if len(self.days) > KEEP_DAYS:
            for old in sorted(self.days)[:-KEEP_DAYS]:
                self.days.pop(old, None)
        self._save()

    @callback
    def tool(self, name: str) -> None:
        if (feature := self._by_tool.get(name)) is not None:
            self.hit(feature)

    @callback
    def _spoke(self, event: Event) -> None:
        source = str(event.data.get("source") or "")
        for feature, spec in FEATURES.items():
            if spec[4] and source.startswith(spec[4]):
                self.hit(feature)
                return

    def _on(self, feature: str) -> bool | None:
        key = FEATURES[feature][1]
        rt = self._rt()
        if feature in ("offers", "alarms", "autooff", "maintenance", "rules") and rt is not None:
            store = rt.lists.get({"alarms": "alarms", "rules": "rules"}.get(feature, feature))
            if store is not None and not store.enabled():
                return False
        if key is None:
            return True if feature not in ("cheap_power",) else bool(self.options.get("price_entity"))
        return bool(self.options.get(key))

    def report(self) -> dict[str, Any]:
        month = (dt_util.now() - timedelta(days=30)).date().isoformat()
        rows = []
        for feature, (name, key, page, _tools, _sources) in FEATURES.items():
            used = sum(n.get(feature, 0) for d, n in self.days.items() if d >= month)
            on = self._on(feature)
            rows.append({"id": feature, "name": name, "on": on, "page": page, "used_this_month": used,
                         "last_used": self.last.get(feature), "setting": key})
        return {
            "used": [r for r in rows if r["on"] and r["used_this_month"]],
            "never": [r for r in rows if r["on"] and not r["used_this_month"]],
            "off": [r for r in rows if not r["on"]],
            "since": min(self.days) if self.days else None,
        }
