"""One door for the AI calls Sentient makes on its own.

* **Meter** — every background AI call is counted by what it was for (camera checks, briefings,
  the nightly think, the doorbell…), today and for the last fortnight. The Mind card shows it.
* **Daily cap** — once today's limit is used up, Sentient stops calling the AI until midnight and
  falls back to plain rules: briefings, welcomes and the doorbell still speak, in plain words;
  camera looks wait for tomorrow. A question someone asks still gets an answer (it's counted, never
  blocked). 0 means no limit.
* **Backup** — every call has a time limit, and a failure (an error, a timeout, an outage) is
  caught: the caller gets `AIUnavailable` and says its plain version instead. Three failures in a
  row show up as a house-health problem until the AI answers again.

Questions people ask a voice agent go through that agent, not through here, and never count.
"""

from __future__ import annotations

import asyncio
from datetime import timedelta
import logging
from typing import Any

from homeassistant.core import HomeAssistant
from homeassistant.exceptions import HomeAssistantError
from homeassistant.helpers.storage import Store
from homeassistant.util import dt as dt_util

from .const import (
    CONF_AI_TASK_ENTITY,
    CONF_BUDGET_CAMERAS,
    CONF_BUDGET_CLIPS,
    CONF_BUDGET_DRIVE,
    DEFAULT_BUDGET_CLIPS,
    CONF_BUDGET_NOTICING,
    CONF_DAILY_AI_LIMIT,
    CONF_PRIVACY_LOG,
    DEFAULT_DAILY_AI_LIMIT,
    DOMAIN,
)

_LOGGER = logging.getLogger(__name__)

TIMEOUT = 90  # seconds; picture questions can be slow
FAILING_AFTER = 3  # failures in a row
HISTORY_DAYS = 14
SENT_DAYS = 14
SENT_KEEP = 2000


class AIUnavailable(HomeAssistantError):
    """The AI can't be used right now (today's limit, an error or a timeout)."""


# features with their own (optional) daily allowance; safety, the doorbell and spoken questions never have one
BUDGETS = {"drive": CONF_BUDGET_DRIVE, "noticing": CONF_BUDGET_NOTICING,
           "camera checks": CONF_BUDGET_CAMERAS, "watches": CONF_BUDGET_CAMERAS, "clip watch": CONF_BUDGET_CLIPS}
BUDGET_DEFAULTS = {CONF_BUDGET_CLIPS: DEFAULT_BUDGET_CLIPS}

class AIGate:
    def __init__(self, hass: HomeAssistant, entry_id: str, options: dict[str, Any]) -> None:
        self.hass = hass
        self.options = options
        self._store: Store[dict[str, Any]] = Store(hass, 1, f"{DOMAIN}.{entry_id}.ai")
        self.day = dt_util.now().date().isoformat()
        self.counts: dict[str, int] = {}
        self.history: dict[str, int] = {}
        self.failed_today = 0
        self.fails_in_row = 0
        self.last_error: str | None = None
        self.last_error_at: str | None = None
        self.last_ok_at: str | None = None
        self.capped_at: str | None = None
        self.service_attempts = 0
        self.retry_attempts = 0
        self._generation = 0
        from .voice_timing import VoiceTimings

        self.voice_timings = VoiceTimings(hass)
        self.sent: list[dict[str, Any]] = []  # what left the house: metadata only, never the content (newest first)

    async def async_start(self) -> None:
        data = await self._store.async_load() or {}
        from .language import restore_phrases

        restore_phrases(self, data.get("language_phrases"))
        if data.get("language_phrases") and not getattr(self, "speech_language_cache", None):
            self._save()  # remove disabled, stale or invalid saved phrases
        self.history = dict(data.get("history") or {})
        self.fails_in_row = int(data.get("fails_in_row") or 0)
        self.last_error, self.last_error_at = data.get("last_error"), data.get("last_error_at")
        self.last_ok_at = data.get("last_ok_at")
        loaded_sent = list(data.get("sent") or [])
        cutoff = (dt_util.utcnow() - timedelta(days=SENT_DAYS)).isoformat()
        self.sent = [row for row in loaded_sent if isinstance(row, dict) and isinstance(row.get('time'), str) and row['time'] >= cutoff][:SENT_KEEP] if self.keeping_record else []
        if self.sent != loaded_sent:
            self._save()
        if data.get("day") == self.day:
            self.counts = dict(data.get("counts") or {})
            self.service_attempts = int(data.get('service_attempts') or 0)
            self.retry_attempts = int(data.get('retry_attempts') or 0)
            self.failed_today = int(data.get("failed_today") or 0)
            self.capped_at = data.get("capped_at")
        elif data.get("day") and data.get("counts"):
            self.history[data["day"]] = sum(data["counts"].values())
            self._trim()

    def _storage_data(self):
        from .language import saved_phrases

        return {
            "language_phrases": saved_phrases(self),
            "service_attempts": self.service_attempts, "retry_attempts": self.retry_attempts,
            "day": self.day, "counts": self.counts, "history": self.history, "failed_today": self.failed_today,
            "fails_in_row": self.fails_in_row, "last_error": self.last_error, "last_error_at": self.last_error_at,
            "last_ok_at": self.last_ok_at, "capped_at": self.capped_at, "sent": self.sent,
        }

    def _save(self) -> None:
        self._store.async_delay_save(self._storage_data, 5)

    async def async_flush(self):
        await self._store.async_save(self._storage_data())

    def _trim(self) -> None:
        for day in sorted(self.history)[:-HISTORY_DAYS]:
            self.history.pop(day, None)

    def _roll(self) -> None:
        today = dt_util.now().date().isoformat()
        if today == self.day:
            return
        if self.counts:
            self.history[self.day] = sum(self.counts.values())
            self._trim()
        self.day, self.counts, self.failed_today, self.capped_at = today, {}, 0, None
        self.service_attempts = self.retry_attempts = 0
        self._save()

    # ----- the meter and the cap ----------------------------------------------------------
    @property
    def limit(self) -> int:
        return int(self.options.get(CONF_DAILY_AI_LIMIT, DEFAULT_DAILY_AI_LIMIT) or 0)

    @property
    def used(self) -> int:
        self._roll()
        return sum(self.counts.values())

    @property
    def capped(self) -> bool:
        return bool(self.limit) and self.used >= self.limit

    @property
    def failing(self) -> bool:
        return self.fails_in_row >= FAILING_AFTER

    def spend(self, purpose: str = "camera", force: bool = False) -> bool:
        """Count one background call. False (and nothing counted) once today's limit is used up —
        unless `force`, for something a person asked for, which is always allowed."""
        self._roll()
        if not force and self.over_budget(purpose):
            return False  # this feature's own allowance is used up; the rest carry on
        if not force and self.capped:
            if self.capped_at is None:
                self.capped_at = dt_util.utcnow().isoformat()
                _LOGGER.info("Sentient: today's AI limit (%s) is used up; plain rules until midnight", self.limit)
                self._save()
            return False
        self.counts[purpose] = self.counts.get(purpose, 0) + 1
        self._save()
        return True

    def over_budget(self, purpose: str) -> str | None:
        """This feature has used its own daily allowance (if it has one): the setting's name, else None."""
        self._roll()
        key = BUDGETS.get(purpose)
        cap = int(float(self.options.get(key, BUDGET_DEFAULTS.get(key, 0)) or 0)) if key else 0
        if not cap:
            return None
        used = sum(n for p, n in self.counts.items() if BUDGETS.get(p) == key)
        return key if used >= cap else None

    def budgets(self) -> dict[str, dict[str, int]]:
        self._roll()
        out = {}
        for key in dict.fromkeys(BUDGETS.values()):
            if cap := int(float(self.options.get(key, BUDGET_DEFAULTS.get(key, 0)) or 0)):
                out[key.removeprefix("budget_")] = {
                    "used": sum(n for p, n in self.counts.items() if BUDGETS.get(p) == key), "cap": cap}
        return out

    # ----- what leaves the house -----------------------------------------------------------
    @property
    def keeping_record(self) -> bool:
        return bool(self.options.get(CONF_PRIVACY_LOG, True))

    def record_sent(self, feature: str, to: str, pictures: int = 0, clips: int = 0, documents: int = 0,
                    chars: int = 0) -> None:
        """Note that something left the house — what kind and how much, never what it said or showed."""
        if not self.keeping_record:
            return
        self.sent.insert(0, {"time": dt_util.utcnow().isoformat(), "feature": feature, "to": to,
                             "pictures": pictures, "clips": clips, "documents": documents, "chars": chars})
        cutoff = (dt_util.utcnow() - timedelta(days=SENT_DAYS)).isoformat()
        self.sent = [s for s in self.sent if s["time"] >= cutoff][:SENT_KEEP]
        self._save()

    def _record_call(self, data: dict[str, Any]) -> None:
        kinds = [str(a.get("media_content_type") or "") for a in data.get("attachments") or [] if isinstance(a, dict)]
        feature = str(data.get("task_name") or "background job").removeprefix("Sentient: ").removeprefix("Sentient ")
        self.record_sent(
            feature, str(data.get("entity_id") or "the AI task entity"),
            pictures=sum(1 for k in kinds if k.startswith("image/")),
            clips=sum(1 for k in kinds if k.startswith("video/")),
            documents=sum(1 for k in kinds if not k.startswith(("image/", "video/"))),
            chars=len(str(data.get("instructions") or "")),
        )

    def sent_summary(self, days: float = 7) -> dict[str, Any]:
        """By feature over the last few days, plus the last 48 hours in detail (no content)."""
        cutoff = (dt_util.utcnow() - timedelta(days=days)).isoformat()
        recent = [s for s in self.sent if s["time"] >= cutoff]
        by: dict[str, dict[str, int]] = {}
        for s in recent:
            row = by.setdefault(s["feature"], {"times": 0, "pictures": 0, "clips": 0, "documents": 0})
            row["times"] += 1
            for k in ("pictures", "clips", "documents"):
                row[k] += int(s.get(k) or 0)
        def name(to: str) -> str:
            return st.name if (st := self.hass.states.get(to)) is not None else to

        recent = [{**s, "to": name(s["to"])} for s in recent]
        two_days = (dt_util.utcnow() - timedelta(hours=48)).isoformat()
        return {
            "keeping_record": self.keeping_record, "days": days,
            "by_feature": dict(sorted(by.items(), key=lambda kv: -kv[1]["times"])),
            "totals": {k: sum(r[k] for r in by.values()) for k in ("times", "pictures", "clips", "documents")},
            "last_48_hours": [s for s in recent if s["time"] >= two_days][:100],
            "where": sorted({s["to"] for s in recent}),
        }

    def invalidate_requests(self):
        """Prevent old jobs retrying or altering the current provider's health."""
        self._generation += 1

    def reset_provider_health(self):
        self.fails_in_row = 0
        self.last_error = self.last_error_at = self.last_ok_at = None
        self._save()

    def forget_sent(self, since: str | None = None) -> int:
        self.invalidate_requests()
        before = len(self.sent)
        self.sent = [s for s in self.sent if since is not None and s["time"] < since]
        self._save()
        return before - len(self.sent)

    # ----- calling -------------------------------------------------------------------------
    async def async_call(self, data: dict[str, Any], timeout: float = TIMEOUT) -> dict[str, Any]:
        """ai_task.generate_data with a time limit; failures are noted and raised as AIUnavailable."""
        generation, options = self._generation, self.options
        data = dict(data)
        if (self.options.get('automatic_language') and self.options.get('speak_house_language')
                and isinstance(data.get('instructions'), str)
                and not str(data.get('task_name', '')).startswith(('Sentient: speech language', 'Sentient: prepare speech language'))):
            from .language import selected_language

            data['instructions'] += (
                f"\nWrite prose intended to be spoken to the household in language '{selected_language(self.hass, self.options)}'. "
                "Preserve JSON keys, required enum values, entity IDs, names and machine-readable output formats. "
                "This applies only to user-facing prose, not classification labels or tool arguments."
            )
        if "entity_id" not in data and (ai := self.options.get(CONF_AI_TASK_ENTITY)):
            data["entity_id"] = ai
        async def attempt(retry=False):
            self._roll()
            self.service_attempts += 1
            self.retry_attempts += int(retry)
            self._record_call(data)
            self._save()
            return await self.hass.services.async_call(
                'ai_task', 'generate_data', data, blocking=True, return_response=True)

        def current():
            return generation == self._generation and self.options is options

        try:
            async with asyncio.timeout(timeout):
                try:
                    response = await attempt()
                except Exception as err:  # noqa: BLE001
                    if not busy(err):
                        raise
                    if not current():
                        raise AIUnavailable('AI settings or privacy changed; retry cancelled') from err
                    await asyncio.sleep(RETRY_AFTER)
                    if not current():
                        raise AIUnavailable('AI settings or privacy changed; retry cancelled')
                    response = await attempt(retry=True)
                if response is not None and not isinstance(response, dict):
                    raise HomeAssistantError('The AI service returned an invalid response format')
        except Exception as err:  # noqa: BLE001 - every failure means "use the plain version"
            if current():
                self.failed(err)
            raise AIUnavailable(f"The AI didn't answer: {err or type(err).__name__}") from err
        if current():
            self.ok()
        return response or {}

    def failed(self, err: BaseException) -> None:
        self._roll()
        self.fails_in_row += 1
        self.failed_today += 1
        self.last_error = (str(err) or type(err).__name__)[:200]
        self.last_error_at = dt_util.utcnow().isoformat()
        _LOGGER.debug("Sentient AI call failed (%s in a row): %s", self.fails_in_row, self.last_error)
        self._save()

    def ok(self) -> None:
        self.fails_in_row = 0
        self.last_ok_at = dt_util.utcnow().isoformat()
        self._save()

    def describe(self) -> dict[str, Any]:
        self._roll()
        days = sorted(self.history)[-7:]
        week = [self.history[d] for d in days]
        return {
            "today": self.used,
            "service_attempts_today": self.service_attempts,
            "retry_attempts_today": self.retry_attempts,
            "meter_note": "Daily limits count logical jobs. Service attempts include Sentient retries of ai_task.generate_data, not provider billing or provider-internal retries.",
            "limit": self.limit,
            "capped": self.capped,
            "by_purpose": dict(sorted(self.counts.items(), key=lambda kv: -kv[1])),
            "budgets": self.budgets(),
            "failed_today": self.failed_today,
            "failing": self.failing,
            "last_error": self.last_error if self.failing else None,
            "last_ok": self.last_ok_at,
            "last_7_days": dict(zip(days, week)),
            "average_per_day": round(sum(week) / len(week)) if week else None,
        }


RETRY_AFTER = 4  # seconds
_BUSY = ("503", "429", "unavailable", "high demand", "overloaded", "resource_exhausted", "rate limit", "try again later")


def busy(err: BaseException) -> bool:
    """The AI service said it's busy (not that the request was wrong): worth one more try."""
    text = f"{type(err).__name__} {err}".lower()
    return any(b in text for b in _BUSY)


def gate(hass: HomeAssistant) -> AIGate | None:
    entries = hass.config_entries.async_loaded_entries(DOMAIN)
    rt = getattr(entries[0], "runtime_data", None) if entries else None
    return getattr(rt, "ai", None)


async def async_generate(hass: HomeAssistant, data: dict[str, Any], timeout: float = TIMEOUT) -> dict[str, Any]:
    """Call ai_task.generate_data through the gate (time limit, failure tracking).

    Every job is told that quoted text (calendars, the web, descriptions…) is data, never instructions."""
    from .outside import QUOTED_RULE

    if isinstance(data.get("instructions"), str) and QUOTED_RULE not in data["instructions"]:
        data = {**data, "instructions": f"{data['instructions']}\n\n{QUOTED_RULE}"}
    if (g := gate(hass)) is not None:
        return await g.async_call(data, timeout)
    try:
        async with asyncio.timeout(timeout):
            return await hass.services.async_call(
                "ai_task", "generate_data", data, blocking=True, return_response=True
            ) or {}
    except Exception as err:  # noqa: BLE001
        raise AIUnavailable(f"The AI didn't answer: {err or type(err).__name__}") from err

