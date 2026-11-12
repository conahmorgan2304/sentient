"""Official weather and flood warnings for the household's own country.

The country comes from Settings (defaulting to Home Assistant's own country), and the place from Home
Assistant's location. Sources, all free and public:

* GB — Met Office weather warnings and, in England, Environment Agency flood warnings near home;
* most of Europe — MeteoAlarm, the national weather services' shared feed;
* US — the National Weather Service's active alerts for home's location.

Anywhere else has no feed yet, and the settings page says so. Checked every 30 minutes, only while switched
on. A new amber or red (severe) warning is said once; others go on the timeline. No AI. Switch: "Weather and
flood warnings" (Settings → Weather warnings); turning it off stops the checks and forgets what was found.
"""
from __future__ import annotations

from datetime import timedelta
import logging
from typing import Any

from homeassistant.core import HomeAssistant, callback
from homeassistant.helpers.aiohttp_client import async_get_clientsession
from homeassistant.helpers.event import async_track_time_interval

try:  # pragma: no cover - which parser depends on what's installed
    from defusedxml import ElementTree as ET  # type: ignore[import-not-found]
except ImportError:  # pragma: no cover
    from xml.etree import ElementTree as ET  # noqa: N817

_LOGGER = logging.getLogger(__name__)

CONF_WARNINGS = "weather_warnings"
CONF_WARNINGS_COUNTRY = "warnings_country"
CONF_WARNINGS_REGION = "warnings_region"
CONF_WARNINGS_SPEAK = "warnings_speak"
EVERY = timedelta(minutes=30)
METOFFICE = "https://www.metoffice.gov.uk/public/data/PWSCache/WarningsRSS/Region/UK"
FLOODS = "https://environment.data.gov.uk/flood-monitoring/id/floods?lat={lat}&long={lon}&dist=15"
NWS = "https://api.weather.gov/alerts/active?point={lat},{lon}"
METEOALARM = "https://feeds.meteoalarm.org/feeds/meteoalarm-legacy-atom-{name}"
METEOALARM_COUNTRIES = {
    "AT": "austria", "BE": "belgium", "BA": "bosnia-herzegovina", "BG": "bulgaria", "HR": "croatia", "CY": "cyprus",
    "CZ": "czechia", "DK": "denmark", "EE": "estonia", "FI": "finland", "FR": "france", "DE": "germany",
    "GR": "greece", "HU": "hungary", "IS": "iceland", "IE": "ireland", "IL": "israel", "IT": "italy", "LV": "latvia",
    "LT": "lithuania", "LU": "luxembourg", "MT": "malta", "MD": "moldova", "ME": "montenegro", "NL": "netherlands",
    "MK": "republic-of-north-macedonia", "NO": "norway", "PL": "poland", "PT": "portugal", "RO": "romania",
    "RS": "serbia", "SK": "slovakia", "SI": "slovenia", "ES": "spain", "SE": "sweden", "CH": "switzerland",
    "UA": "ukraine",
}
SEVERE = ("amber", "red", "severe", "extreme", "severe flood", "flood warning")


def source_for(country: str | None) -> str | None:
    country = (country or "").upper()
    if country in ("GB", "UK"):
        return "Met Office and Environment Agency"
    if country in METEOALARM_COUNTRIES:
        return "MeteoAlarm"
    if country == "US":
        return "National Weather Service"
    return None


def countries() -> list[dict[str, str]]:
    """The choices for the settings page: every country with a feed."""
    names = {"GB": "United Kingdom", "US": "United States", **{k: v.replace("-", " ").title() for k, v in METEOALARM_COUNTRIES.items()}}
    return [{"value": k, "label": v} for k, v in sorted(names.items(), key=lambda kv: kv[1])]


def _text(el: Any, *names: str) -> str:
    for name in names:
        found = el.find(name)
        if found is not None and found.text:
            return found.text.strip()
    return ""


def parse_metoffice(xml: str) -> list[dict[str, Any]]:
    root = ET.fromstring(xml)
    out = []
    for item in root.iter("item"):
        title = _text(item, "title")
        level = next((c for c in ("red", "amber", "yellow") if c in title.lower()), "")
        out.append({"id": _text(item, "guid", "link") or title, "title": title, "level": level,
                    "detail": _text(item, "description")[:400], "source": "Met Office"})
    return out


def parse_floods(data: dict[str, Any]) -> list[dict[str, Any]]:
    out = []
    for item in data.get("items") or []:
        level = int(item.get("severityLevel") or 4)
        if level > 3:
            continue
        out.append({"id": str(item.get("@id") or item.get("floodAreaID")),
                    "title": f"{item.get('severity') or 'Flood alert'}: {item.get('description') or item.get('eaAreaName') or ''}".strip(),
                    "level": {1: "severe flood", 2: "flood warning", 3: "flood alert"}.get(level, "flood alert"),
                    "detail": str(item.get("message") or "")[:400], "source": "Environment Agency"})
    return out


def parse_meteoalarm(xml: str) -> list[dict[str, Any]]:
    root = ET.fromstring(xml)
    out = []
    for entry in root.findall("{http://www.w3.org/2005/Atom}entry"):
        severity = (entry.findtext("{urn:oasis:names:tc:emergency:cap:1.2}severity") or "").lower()
        if severity not in ("moderate", "severe", "extreme"):
            continue
        area = entry.findtext("{urn:oasis:names:tc:emergency:cap:1.2}areaDesc") or ""
        event = entry.findtext("{urn:oasis:names:tc:emergency:cap:1.2}event") or entry.findtext("{http://www.w3.org/2005/Atom}title") or ""
        out.append({"id": entry.findtext("{http://www.w3.org/2005/Atom}id") or f"{area}{event}",
                    "title": f"{event} — {area}".strip(" —"), "level": severity, "detail": "", "source": "MeteoAlarm",
                    "area": area})
    return out


def parse_nws(data: dict[str, Any]) -> list[dict[str, Any]]:
    out = []
    for f in data.get("features") or []:
        p = f.get("properties") or {}
        out.append({"id": p.get("id") or f.get("id"), "title": p.get("headline") or p.get("event") or "",
                    "level": str(p.get("severity") or "").lower(), "detail": str(p.get("description") or "")[:400],
                    "source": "National Weather Service"})
    return out


def is_severe(warning: dict[str, Any]) -> bool:
    return str(warning.get("level", "")).lower() in SEVERE


class Warnings:
    def __init__(self, hass: HomeAssistant, options: dict[str, Any], rt_getter: Any) -> None:
        self.hass = hass
        self.options = options
        self._rt = rt_getter
        self.current: list[dict[str, Any]] = []
        self.seen: set[str] = set()
        self.error: str | None = None
        self.checked: str | None = None
        self._unsub = None

    @property
    def enabled(self) -> bool:
        return bool(self.options.get(CONF_WARNINGS))

    @property
    def country(self) -> str:
        return str(self.options.get(CONF_WARNINGS_COUNTRY) or self.hass.config.country or "").upper()

    def configure(self, options: dict[str, Any]) -> None:
        self.options = options
        if self.enabled and self._unsub is None:
            self._unsub = async_track_time_interval(self.hass, self._tick, EVERY)
            self.hass.async_create_background_task(self.async_check(), "sentient warnings")
        elif not self.enabled:
            self.stop()
            self.current, self.seen, self.error = [], set(), None

    def stop(self) -> None:
        if self._unsub:
            self._unsub()
            self._unsub = None

    @callback
    def _tick(self, _now: Any) -> None:
        self.hass.async_create_background_task(self.async_check(), "sentient warnings")

    async def _fetch(self, url: str, as_json: bool) -> Any:
        session = async_get_clientsession(self.hass)
        headers = {"User-Agent": "Sentient for Home Assistant (github.com/conahmorgan2304/sentient)",
                   "Accept": "application/geo+json, application/json" if as_json else "application/xml, text/xml"}
        import aiohttp

        async with session.get(url, headers=headers, timeout=aiohttp.ClientTimeout(total=20)) as resp:
            resp.raise_for_status()
            return await (resp.json(content_type=None) if as_json else resp.text())

    async def async_check(self) -> list[dict[str, Any]]:
        if not self.enabled:
            return []
        country, lat, lon = self.country, self.hass.config.latitude, self.hass.config.longitude
        found: list[dict[str, Any]] = []
        try:
            if country in ("GB", "UK"):
                found += parse_metoffice(await self._fetch(METOFFICE, False))
                try:
                    found += parse_floods(await self._fetch(FLOODS.format(lat=lat, lon=lon), True))
                except Exception:  # noqa: BLE001 - outside England there are no Environment Agency warnings
                    pass
            elif country in METEOALARM_COUNTRIES:
                found += parse_meteoalarm(await self._fetch(METEOALARM.format(name=METEOALARM_COUNTRIES[country]), False))
            elif country == "US":
                found += parse_nws(await self._fetch(NWS.format(lat=round(lat, 4), lon=round(lon, 4)), True))
            else:
                self.error = f"No official warnings feed for {country or 'this country'} yet."
                return []
        except Exception as err:  # noqa: BLE001
            self.error = f"Couldn't read the warnings: {err}"
            _LOGGER.debug("Warnings check failed", exc_info=True)
            return self.current
        region = str(self.options.get(CONF_WARNINGS_REGION) or "").strip().casefold()
        if region:
            found = [w for w in found if w["source"] in ("Environment Agency", "National Weather Service")
                     or region in (w["title"] + " " + w.get("area", "")).casefold()]
        self.error = None
        self.current = found
        from homeassistant.util import dt as dt_util

        self.checked = dt_util.utcnow().isoformat()
        await self._announce_new(found)
        return found

    async def _announce_new(self, found: list[dict[str, Any]]) -> None:
        rt = self._rt()
        new = [w for w in found if w["id"] not in self.seen]
        self.seen = {w["id"] for w in found} | (self.seen if len(self.seen) < 500 else set())
        if rt is None:
            return
        for w in new:
            rt.timeline.add("critical" if is_severe(w) else "noticed", f"{w['source']}: {w['title']}", w.get("detail") or None)
            if is_severe(w) and self.options.get(CONF_WARNINGS_SPEAK, True):
                from .reasons import because

                with because(f"an official {w['level']} warning was issued"):
                    await rt.speech.async_say(f"There's an official {w['level']} warning: {w['title']}.", "normal", None,
                                              source="warnings")

    def listing(self) -> dict[str, Any]:
        return {"on": self.enabled, "country": self.country, "source": source_for(self.country),
                "warnings": self.current, "checked": self.checked, "error": self.error}
