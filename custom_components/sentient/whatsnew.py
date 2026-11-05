"""What's new after an update: a few plain lines per release, shown on the Sentient page until seen.

Only releases newer than the one last marked as seen are shown, newest first. A fresh install starts as
"seen" (nothing to catch up on). Turning it off in Settings hides the card; the notes stay in the code.
"""

from __future__ import annotations

from typing import Any

from homeassistant.core import HomeAssistant
from homeassistant.helpers.storage import Store
from homeassistant.loader import async_get_integration

from .const import CONF_WHATS_NEW, DOMAIN

NOTES: dict[str, list[str]] = {
    "0.88.1": [
        "Alarms and automated routines from before 0.88 are checked when they go off, like ones made by voice. If one of yours made on the Alarms page is skipped, open it there and save it once.",
    ],
    "0.88.0": [
        "Stories read with a reading voice no longer pause between parts — the next part is made while this one is read.",
        "Phone notifications now show what they're about, and tapping one opens that moment on the timeline. The timeline has a SENT TO PHONES filter to look back over them.",
        "Alarms and automated routines made by voice that switch things are read back before they're saved, can only use what voice assistants are allowed to control, and are checked again when they go off.",
        "This card: what's new after each update.",
        "The set-up wizard asks about people and phones, how much the house does by itself, private rooms and the world outside — run it again from Settings to see.",
        "Settings → Brain & system → How it's used: what you use, what you never have, and a simpler page for new houses.",
        "A readable copy of everything it knows and does (Backup → Readable copy), as well as the restore file.",
        "Start-up is lighter: optional parts that are switched off don't start, and diagnostics show how long start-up took.",
    ],
    "0.87.0": [
        "Automations can pass things through Sentient with sentient.notice — same quiet hours, same judgement.",
        "Outside information (energy price, weather warnings, parcels) in the briefing; a parcel out for delivery means the door is watched all day.",
        "Maintenance page: filters and descaling due by how much things have actually run.",
        "With a time-of-use tariff, a nudge to use the dishwasher's delay start when electricity's cheapest.",
        "With Music Assistant: \"play something calm in the kitchen\".",
        "Questions can be answered from your phone when nobody's near a speaker (phone buttons → Questions).",
    ],
    "0.86.0": [
        "Text it reads from the web, documents or cameras can't make it switch anything.",
        "Brain & system → What leaves the house, with \"forget the last 48 hours\".",
        "Holiday mode: quiet, away lights, heating down, back to normal when you're home.",
        "Choose whether the house just does things, says so first or asks — per kind of device.",
        "Rooms page: private rooms, a gentler voice, own volume and quiet time.",
    ],
    "0.85.0": [
        "Pin memories; the rest are chosen by what you asked.",
        "Ask \"why did you say that?\" — it gives the reason it noted at the time.",
        "\"No, the other one\" puts it right in one go.",
        "Habits that follow sunset or getting up; habits done together offered as one.",
        "The Mind page can suggest a small change to its settings — only changed if you say yes.",
    ],
    "0.84.0": [
        "Setup doctor (Brain & system) checks the AI, speakers, voices and cameras and says what to fix.",
        "Plain yes, no and \"it already does that\" answers to its own questions, without the AI.",
        "It notices when parts of itself keep failing.",
    ],
}


def _key(version: str) -> tuple[int, ...]:
    try:
        return tuple(int(x) for x in version.split(".")[:3])
    except ValueError:
        return (0,)


class WhatsNew:
    def __init__(self, hass: HomeAssistant, entry_id: str, options: dict[str, Any]) -> None:
        self.hass = hass
        self.options = options
        self._store: Store[dict[str, Any]] = Store(hass, 1, f"{DOMAIN}.{entry_id}.whats_new")
        self.seen: str | None = None
        self.version = "0"

    async def async_start(self) -> None:
        self.version = str((await async_get_integration(self.hass, DOMAIN)).version or "0")
        data = await self._store.async_load()
        if data is None:  # a new install: nothing to catch up on
            self.seen = self.version
            self._save()
        else:
            self.seen = data.get("seen")

    def async_stop(self) -> None:
        return

    def _save(self) -> None:
        self._store.async_delay_save(lambda: {"seen": self.seen}, 1)

    def listing(self) -> dict[str, Any]:
        if not self.options.get(CONF_WHATS_NEW, True):
            return {"version": self.version, "releases": []}
        since = _key(self.seen or "0")
        releases = [{"version": v, "items": items} for v, items in sorted(NOTES.items(), key=lambda kv: _key(kv[0]), reverse=True)
                    if since < _key(v) <= _key(self.version)]
        return {"version": self.version, "releases": releases[:4]}

    def mark_seen(self) -> None:
        self.seen = self.version
        self._save()
