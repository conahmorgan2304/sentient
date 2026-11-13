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
    "1.8.0": [
        "The cognitive core now correlates an observed entry opening with a later observed person detection, but does not treat that timing as proof of household identity. A doorbell or doorstep visit remains a caller.",
        "The Mind tab scores the cognitive core and the usual rules side by side on your reviews: household incidents got right, others wrongly called household, and threats called less serious.",
        "The experimental night heads-up control remains inactive even if selected: a gate opening and camera sighting cannot verify a household member. Spoken and phone security alerts stay unchanged until independent identity evidence is safely supported.",
    ],
    "1.7.0": [
        "The Mind tab now shows how conversations go: how many follow-ups carried on from the last thing you asked, how often it had to ask which thing you meant, how often an answer was corrected (\"no, I meant…\"), and how quickly it answers. With \"Show voice timings\" on; no words are kept.",
        "Memory indicators now show approximate word overlap and search/correction counts; this is not verified answer accuracy.",
        "Every memory now says how it's known — confirmed, told, said, or a guess worked out overnight. Guesses are marked on the Memories page, and the voice is told to check before relying on them.",
        "HOW I'M LEARNING shows whether a corrected topic was heard again during a fortnight; no repeats does not prove the situation arose or that learning was correct.",
    ],
    "1.6.0": [
        "The cognitive core can now decide what stage a security incident is at — what the Mind tab and the voice say it is — once your reviews show it gets each kind of incident right (Settings → Safety & security, off by default). It never changes or holds back an alert, never calls an incident less serious than the usual rules' \"concerning\", and stops by itself if later reviews fall short. Review incidents from the Mind tab: REVIEW THIS INCIDENT.",
        "Settings → Setup doctor → DOWNLOAD THE LAST DAY saves what happened in the house over the last 24 hours and what Sentient decided, so a new version can be checked against your own days before you install it. Nothing is sent anywhere.",
        "Fixed: the Mind tab's \"How I'm learning\" card, which hadn't drawn since 1.5.0, and a link from another tab to a Settings page (such as \"turn it on in Settings\" on the House tab) opening the Settings front page instead.",
    ],
    "1.5.14": [
        "Settings: Doorbell & AI looks and This page open again for administrators (1.5.12 and 1.5.13 broke them).",
    ],
    "1.5.13": [
        "No edit button on the tabs any more: with \"Rearrange the Sentient page\" on, your layout is always used, and you change it from Settings → This page → EDIT PAGE LAYOUT. The edit toolbar shows only while you're editing, until DONE EDITING.",
    ],
    "1.5.12": [
        "Rearrange the Sentient page: switch it on in Settings and an EDIT LAYOUT button lets administrators move, hide, show again and reset cards on the Overview, House, Mind and Security tabs. Saved in Sentient, so every screen shows the same. Off by default.",
        "Motion lights: a rule can be set not to switch a light back on for two minutes after you turned it off by voice. Auto-off can likewise wait after a voice \"on\". Both off by default.",
        "Security incidents on the Mind page can be marked household, visitor, false detection and so on. This only records your review; no alert settings change.",
        "Optional, all off by default: local text reading (OCR) for scanned PDF manuals if Tesseract is installed on the Home Assistant host; better memory search through your own local Ollama embedding server; a cheaper second AI for background text jobs once both have a measured track record; and a before/after comparison of a heating kWh meter.",
    ],
    "1.5.11": [
        "Something switching itself at its usual time (the kettle turning off after it boils) is noted on the timeline, not sent to phones as house health. Changes that are unusual for the time are still said and sent.",
        "The evening \"something I've learned\" no longer reads out notes about how Sentient should talk, and doesn't say \"Something I've learned: I've…\" twice over.",
        "The doctor no longer warns about the person sensors of cameras behind the fence (the ones under \"Inside the boundary\"): they only see your own property, so the whole camera is right for them.",
    ],
    "1.5.10": [
        "Forgetting something now removes it from Sentient's searchable copy straight away, not at the next sync ten minutes later.",
        "The same announcement from two parts of Sentient, worded the same apart from punctuation, is said once. Critical alerts are never held back.",
        "Asking about something by another name (\"boiler\" when the memory says \"radiator\") now finds it, using your own word list. No AI call.",
        "Diagnostics now show which parts of Sentient are working, degraded or not yet checked (voice, cameras, heating), what Sentient's own backups contain, and when two parts of Sentient asked for opposite things at once (for example voice on, then motion lights off). Watching only: nothing is blocked or changed.",
        "Heating: the learned warm-up speed is checked against later warm-ups, and the error is shown only once there's enough history. No saving is claimed and the heating isn't changed.",
    ],
    "1.5.9": [
        "The morning briefing is given once a day even if Home Assistant or Sentient restarts in the morning. Before, the day it was given was forgotten on a restart, and a motion sensor coming back after the restart counted as someone walking into the room, so the briefing came again.",
    ],
    "1.5.8": [
        "Music through Alexa: without Music Assistant, \"play something calm in the kitchen\" can ask that room's Echo to play it from Spotify (or Amazon Music, Apple Music, Deezer, TuneIn, iHeartRadio) — by name, a playlist, artist, song or mood. At the night volume in quiet hours; \"stop the music\" pauses it. Off until switched on: Settings → Voice → Music through Alexa. When it's on it's used even with Music Assistant installed, which can't play Spotify without Premium.",
    ],
    "1.5.7": [
        "Someone up early no longer gets the morning briefing during quiet hours: it waits until they end, then comes the first time they're seen moving (in the morning briefing rooms, if chosen). The old way is a switch: Settings → Briefings → Morning briefing in quiet hours.",
        "Things held back for quiet hours are passed on as \"Overnight: …\", never \"While you were away\" — nobody had gone out. Something nobody heard is \"Earlier: …\".",
        "A newer reading of the same thing (the hutch at 0°, then -1°) replaces the older one instead of both being said, and in the half hour after quiet hours the morning briefing goes first, so what it covers isn't said again.",
        "A remark that talks about a different room from the one where something changed (the bathroom window opening, then \"the air quality in the living room is poor\") is held back instead of said.",
        "A remark never claims the house did something itself (\"so I have switched on the air purifier\" when it had come on by itself): a remark can only speak, so one that says otherwise is held back.",
        "When the doorbell camera looks and sees nobody (\"empty street view\"), no \"At the door\" notification is sent to phones.",
        "The Setup doctor warns when camera security uses a sensor that covers a whole Frigate camera while that camera has zones: people walking past in the street set it off. It names the zone sensor to use instead.",
    ],
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
