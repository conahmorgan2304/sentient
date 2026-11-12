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
    "1.5.6": [
        "Fixed: saving one setting through Sentient's settings commands no longer resets the other settings on that page to their defaults (the Settings page itself was never affected).",
        "House health's check for devices listed twice reads Home Assistant's device list the current way (the old way logged a warning and would stop working in 2027.9).",
    ],
    "1.5.5": [
        "Live camera: Home Assistant's own player that loads but never shows a picture now falls back to the still picture after 12 seconds, instead of staying black.",
        "Security incidents: a door and a camera only count as backing each other up when they're different devices, and evidence with no clear status is treated as unknown rather than settled.",
        "Long paragraphs in text manuals are split into sections instead of losing their end; voice recognition no longer suggests a match level from broken or ambiguous scores.",
        "For testing, off by default: \"Compare security incidents\" (Settings → Safety & security) judges each incident a second, stricter way alongside the usual stages and counts how often they agree, as totals on the Mind sensor. It never changes an alert, a stage or what is said.",
    ],
    "1.5.4": [
        "Manuals say which page: each PDF is read page by page once, on this machine, and a question sends the AI only the few pages that match instead of the whole document — far cheaper — so the answer can say \"it's on page 14\". A manual is also found by what it says when its name doesn't match, and with the AI down the closest passage is read out with its page. Scanned PDFs are still read whole (the Setup doctor lists them). Switch: Settings → Manuals & documents → Read PDFs page by page.",
    ],
    "1.5.3": [
        "A device flickering between states (an Echo going playing, idle, paused in a second) counts as one unusual event, not three, on the Mind tab and in what the voice is told.",
        "Fixed: \"Stop flagging\" on a device that shares its name with an empty copy (a plug in Tuya Local and again from Tuya's cloud) said there was nothing to ignore; it now picks the real one.",
        "House health spots a device listed twice under one name where one copy is empty, with a button to disable the empty copy so it stays out of the way (Settings → House health → Spot devices listed twice).",
        "The Mind tab's learning card lists the devices that switched by themselves most this week.",
        "After a week of watch mode, if skipping remarks about what's usual would have saved 20 or more AI calls, Sentient suggests switching it on (once, in the timeline).",
        "The Mind sensor no longer works out the self-check during its update; it shows the last one and refreshes straight after, so start-up is quicker.",
    ],
    "1.5.2": [
        "Restarts no longer count as devices switching by themselves: everything coming back from unavailable is left out of that count and of the patterns Sentient forms overnight, so a restart at the same time on a few days can't become a belief.",
    ],
    "1.5.1": [
        "The Mind tab's learning card counts properly: a script finishing is no longer taken as someone putting Sentient's change straight back, \"switched by themselves\" counts controlled devices only (not every sensor reading), answers straight from the house count as done without the AI, and watch mode shows how many AI calls skipping the usual would save.",
        "Fixed: when the AI is down, \"when's my next alarm?\" and \"any reminders?\" are answered from the house again (the reminders list was read the wrong way, so no answer came).",
    ],
    "1.5.0": [
        "The house ledger: every change to every device is now recorded with what caused it — a person, a voice request, an automation, Sentient, or nothing in Home Assistant (the device itself or its app). Ask \"when did the heating go off, and why?\".",
        "Sentient now learns what's usual at each hour and notices what isn't. The Mind tab's HOW I'M LEARNING card shows how much of each day it expected, and that number should rise week on week.",
        "An important device that changes by itself is mentioned once, and repeats are linked: \"the third time this week, always around 11am\".",
        "Memories, conversations and the timeline are kept for a year in Sentient's own database, and recall searches all of it.",
        "Names are said the way they're pronounced: \"Siobhan is pronounced Shivawn\" (a memory, or Settings → Voice → Pronunciations) changes what the speakers say, while the chat and timeline keep the real spelling.",
        "New on Settings: Memory & learning, Weather warnings (your own country's service) and Extra skills (read-only email, calendar clashes, a security check and automations from plain English).",
    ],
    "0.88.2": [
        "Sentient is in the Home Assistant Marketplace (Settings → Marketplace, formerly HACS); updates show under Settings → Updates.",
    ],
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
