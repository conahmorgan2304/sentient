"""Energy: what's used the most today, and anything using far more than usual.

Reads the devices already set up on Home Assistant's Energy dashboard (no list to keep
here) and compares today, up to the last full hour, with the same part of the day over
the past week. Plain statistics, no AI.
"""

from __future__ import annotations

from datetime import datetime, timedelta
import logging
from typing import Any

from homeassistant.core import HomeAssistant
from homeassistant.util import dt as dt_util

_LOGGER = logging.getLogger(__name__)
DAYS = 7
UNUSUAL = 2.0  # at least this many times the usual
MIN_KWH = 0.3  # ignore tiny amounts


async def _devices(hass: HomeAssistant) -> dict[str, str]:
    """statistic id -> name, from the Energy dashboard's device list."""
    try:
        from homeassistant.components.energy.data import async_get_manager

        manager = await async_get_manager(hass)
        prefs = manager.data or {}
    except Exception as err:  # noqa: BLE001 - energy not set up
        _LOGGER.debug("No energy prefs: %s", err)
        return {}
    out = {}
    for dev in prefs.get("device_consumption", []) or []:
        stat = dev.get("stat_consumption")
        if stat:
            st = hass.states.get(stat)
            out[stat] = dev.get("name") or (st.name if st else stat)
    return out


def enabled(hass: HomeAssistant) -> bool:
    from .const import CONF_ENERGY, DOMAIN

    entries = hass.config_entries.async_loaded_entries(DOMAIN)
    return not entries or bool(entries[0].runtime_data.options.get(CONF_ENERGY, True))


def _price(hass: HomeAssistant) -> float | None:
    from .const import CONF_ENERGY_PRICE, DOMAIN

    entries = hass.config_entries.async_loaded_entries(DOMAIN)
    value = entries[0].runtime_data.options.get(CONF_ENERGY_PRICE) if entries else None
    return float(value) if value else None


async def async_report(hass: HomeAssistant) -> dict[str, Any]:
    if not enabled(hass):
        return {"enabled": False, "devices": [], "unusual": [], "note": "Energy insight is turned off in Sentient's settings"}
    devices = await _devices(hass)
    if not devices:
        return {"devices": [], "note": "No devices on the Energy dashboard"}
    from homeassistant.components.recorder import get_instance
    from homeassistant.components.recorder.statistics import statistics_during_period

    now = dt_util.now()
    upto = now.replace(minute=0, second=0, microsecond=0)  # last full hour
    midnight = upto.replace(hour=0)
    start = dt_util.as_utc(midnight - timedelta(days=DAYS))
    rows = await get_instance(hass).async_add_executor_job(
        statistics_during_period, hass, start, dt_util.as_utc(upto), set(devices), "hour", None, {"change"}
    )
    hours_in = (upto - midnight).total_seconds() / 3600
    out = []
    for stat, name in devices.items():
        today = 0.0
        past = [0.0] * DAYS
        for row in rows.get(stat, []):
            change = row.get("change") or 0.0
            when = dt_util.as_local(dt_util.utc_from_timestamp(row["start"]) if isinstance(row["start"], (int, float)) else row["start"])
            day_start = when.replace(hour=0, minute=0, second=0, microsecond=0)
            if day_start == midnight:
                today += change
            else:
                ago = (midnight - day_start).days
                if 1 <= ago <= DAYS and (when - day_start).total_seconds() / 3600 < hours_in:
                    past[ago - 1] += change
        usual = sorted(past)[DAYS // 2]  # median: one odd day doesn't skew it
        item = {"name": name, "kwh": round(today, 2), "usual": round(usual, 2)}
        if today >= MIN_KWH and today >= UNUSUAL * max(usual, 0.05):
            item["unusual"] = True
            item["times_usual"] = round(today / usual, 1) if usual > 0.05 else None
        out.append(item)
    out.sort(key=lambda i: i["kwh"], reverse=True)
    total = round(sum(i["kwh"] for i in out), 2)
    total_usual = round(sum(i["usual"] for i in out), 2)
    price = _price(hass)
    return {
        "enabled": True,
        "cost_today": round(total * price / 100, 2) if price else None,
        "up_to": upto.strftime("%H:%M"),
        "total_kwh": total,
        "usual_kwh": total_usual,
        "devices": out,
        "unusual": [i for i in out if i.get("unusual")],
    }


def describe(report: dict[str, Any]) -> list[str]:
    lines = []
    for i in report.get("unusual", [])[:3]:
        if i.get("times_usual"):
            lines.append(f"{i['name']} has used {i['kwh']} kWh today, about {i['times_usual']}× its usual")
        else:
            lines.append(f"{i['name']} has used {i['kwh']} kWh today, when it's normally next to nothing")
    return lines
