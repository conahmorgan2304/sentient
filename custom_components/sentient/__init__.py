"""Sentient — an evolving AI brain for Home Assistant."""

from __future__ import annotations

from dataclasses import dataclass
import logging
from pathlib import Path
from typing import Any

import voluptuous as vol

from homeassistant.config_entries import ConfigEntry
from homeassistant.const import Platform
from homeassistant.const import EVENT_HOMEASSISTANT_STARTED
from homeassistant.core import HomeAssistant, ServiceCall, ServiceResponse, SupportsResponse
from homeassistant.helpers import config_validation as cv
from homeassistant.helpers import llm
from homeassistant.helpers.service import async_register_admin_service
from homeassistant.exceptions import ServiceValidationError

from .const import CONF_ASK_LATER, CONF_SPEECH_DELIVERY_HISTORY
from .voice_checks import VoiceChecks
from .updates import UpdateChecker
from .sessions import Sessions
from .control import Control
from .incidents import Incidents
from .evidence import Evidence
from .ai import AIGate
from .animals import Animals
from .away import Away
from .watchdog import Watchdog
from .guard import Guard
from .screen import Screen
from .appliances import Appliances
from .const import (
    DOMAIN,
    EVENT_MODE_CHANGED,
    PARTY_STYLES,
    MEMORY_CATEGORIES,
    PRIORITIES,
    SERVICE_ADOPT,
    SERVICE_FORGET_ROUTINE,
    SERVICE_ASK,
    SERVICE_BRIEFING,
    SERVICE_CANCEL_WATCH,
    SERVICE_FORGET,
    SERVICE_LEARN,
    SERVICE_REMIND,
    SERVICE_REMEMBER,
    SERVICE_SAY,
    SERVICE_TIDY,
    SERVICE_WATCH,
    STATIC_URL,
    FRONTEND_URL,
    PANEL_URL_PATH,
    CONF_SIDEBAR,
    DEFAULT_SIDEBAR,
    CONF_PERSONA,
)
from .llm_api import SentientAPI
from .memory import MemoryStore
from .presence import ActivityTracker
from .learning import Learner
from .notice import Noticer
from .upkeep import Upkeep, gather_briefing
from .health import HouseHealth
from .spots import Spots
from .motion_lights import MotionLights
from .timeline import Timeline
from .notifier import Notifier
from .welcome import Welcome
from .mutes import Mutes
from .critical import CriticalAlerts
from .shortcuts import Shortcuts
from .left_open import LeftOpen
from .visitors import Visitors
from .feedback import Feedback
from .heating import Heating
from .nightwatch import NightWatch
from .followthrough import FollowThrough
from .items import Items
from .kinds import KINDS
from .offers import Offers
from .announce import Announcements
from .alarms import Alarms
from .checks import CameraChecks
from .camsecurity import CameraSecurity
from .party import Party
from .followyou import FollowYou, Loops
from .conversations import Conversations
from .curiosity import Curiosity
from .reflection import Reflection
from .drive import Drive
from .autooff import AutoOff
from .foresight import Foresight
from .sleep import Sleep
from .watches import WatchManager
from .speech import SpeechRouter

_LOGGER = logging.getLogger(__name__)

PLATFORMS: list[Platform] = [Platform.CONVERSATION, Platform.SENSOR, Platform.SWITCH]

CONFIG_SCHEMA = cv.config_entry_only_config_schema(DOMAIN)


@dataclass
class SentientRuntime:
    """Everything a loaded Sentient entry owns."""

    options: dict[str, Any]
    evidence: Any
    sessions: Any
    updates: Any
    voice_checks: Any
    control: Any
    incidents: Any
    memory: MemoryStore
    speech: SpeechRouter
    tracker: ActivityTracker
    watches: WatchManager
    noticer: Noticer
    learner: Learner
    upkeep: Upkeep
    visitors: Visitors
    spots: Spots
    health: HouseHealth
    motion_lights: MotionLights
    timeline: Timeline
    notifier: Notifier
    welcome: Welcome
    mutes: Mutes
    critical: CriticalAlerts
    shortcuts: Shortcuts
    left_open: LeftOpen
    feedback: Feedback
    suggestions: Any
    heating: Heating
    night_watch: NightWatch
    followthrough: FollowThrough
    lists: dict[str, Items]
    offers: Offers
    announcements: Announcements
    alarms: Alarms
    checks: CameraChecks
    camsec: CameraSecurity
    party: Party
    followyou: FollowYou
    loops: Loops
    conversations: Conversations
    curiosity: Curiosity
    reflection: Reflection
    drive: Drive
    autooff: AutoOff
    foresight: Foresight
    sleep: Sleep
    ai: AIGate
    appliances: Appliances
    animals: Animals
    away: Away
    selftest: Any
    rain: Any
    pets: Any
    autobackup: Any
    bins: Any
    faces: Any
    rules: Any
    clips: Any
    clipwatch: Any
    watchdog: Watchdog
    screen: Screen
    threads: Any = None  # things to ask about later
    selfaware: Any = None  # what it can do now, and how its behaviour is going down
    proposals: Any = None  # settings changes it would make, waiting for a yes
    holiday: Any = None  # holiday mode
    trust: Any = None  # do, say-then-do or ask first, by kind of device
    profiles: Any = None  # room profiles
    parcels: Any = None  # outside information and parcels on their way
    maintenance: Any = None  # filters, descaling and services due by use
    cheap_power: Any = None  # cheap-electricity suggestions
    goals: Any = None  # goals over days or weeks
    books: Any = None  # audiobooks and ebooks with bookmarks
    recipes: Any = None  # recipes and cooking mode
    focus: Any = None  # focused voice tools


type SentientConfigEntry = ConfigEntry[SentientRuntime]


SAY_SCHEMA = vol.Schema(
    {
        vol.Required("message"): cv.string,
        vol.Optional("priority", default="normal"): vol.In(PRIORITIES),
        vol.Optional("speaker"): vol.Any(cv.entity_ids, cv.string),
    }
)
REMEMBER_SCHEMA = vol.Schema(
    {
        vol.Required("text"): cv.string,
        vol.Optional("category", default="fact"): vol.In(MEMORY_CATEGORIES),
    }
)
FORGET_SCHEMA = vol.Schema({vol.Required("what"): cv.string})
WATCH_SCHEMA = vol.Schema(
    {
        vol.Required("what"): cv.string,
        vol.Required("entity"): cv.string,
        vol.Optional("to_state"): cv.string,
        vol.Optional("above"): vol.Coerce(float),
        vol.Optional("below"): vol.Coerce(float),
        vol.Optional("question"): cv.string,
        vol.Optional("look_for"): cv.string,
        vol.Optional("message"): cv.string,
        vol.Optional("until"): cv.string,
        vol.Optional("minutes"): vol.Coerce(float),
        vol.Optional("repeat", default=False): cv.boolean,
        vol.Optional("priority", default="normal"): vol.In(PRIORITIES),
        vol.Optional("speaker"): cv.string,
        vol.Optional("wake_on"): vol.Any(cv.entity_ids, cv.string),
        vol.Optional("spot"): cv.string,
    }
)
ASK_SCHEMA = vol.Schema(
    {
        vol.Required("question"): cv.string,
        vol.Optional("priority", default="normal"): vol.In(PRIORITIES),
        vol.Optional("speaker"): cv.string,
        vol.Optional("extra_prompt"): cv.string,
        vol.Optional("preannounce"): cv.boolean,
    }
)
REMIND_SCHEMA = vol.Schema(
    {
        vol.Required("message"): cv.string,
        vol.Optional("at"): cv.string,
        vol.Optional("minutes"): vol.Coerce(float),
        vol.Optional("speaker"): cv.string,
        vol.Optional("priority", default="urgent"): vol.In(PRIORITIES),
    }
)
CANCEL_WATCH_SCHEMA = vol.Schema({vol.Required("what"): cv.string})
PARTY_SCHEMA = vol.Schema(
    {
        vol.Optional("on", default=True): cv.boolean,
        vol.Optional("style"): vol.In(PARTY_STYLES),
        vol.Optional("minutes"): vol.Coerce(float),
        vol.Optional("area"): cv.string,
        vol.Optional("lights"): cv.entity_ids,
    }
)


def _entry_options(entry: ConfigEntry) -> dict[str, Any]:
    return {**entry.data, **entry.options}


async def async_setup(hass: HomeAssistant, config: dict) -> bool:
    """Register the LLM API, services and the logo once."""
    hass.data.setdefault(DOMAIN, {})
    if getattr(hass, "http", None) is not None:
        from pathlib import Path

        from homeassistant.components.http import StaticPathConfig

        try:
            await hass.http.async_register_static_paths(
                [
                    StaticPathConfig(STATIC_URL, str(Path(__file__).parent / "brand"), True),
                    StaticPathConfig(FRONTEND_URL, str(Path(__file__).parent / "frontend"), False),
                ]
            )
        except RuntimeError:  # already registered (e.g. after a reload)
            pass
    hass.data[DOMAIN]["unregister_api"] = llm.async_register_api(hass, SentientAPI(hass))
    from .voice_api import GuardedAPI

    hass.data[DOMAIN]["unregister_guarded_api"] = llm.async_register_api(hass, GuardedAPI(hass))
    _register_services(hass)
    from .websocket import async_register as _register_websocket

    _register_websocket(hass)
    if "frontend" in hass.config.components and getattr(hass, "http", None) is not None:
        # dashboard cards, ready for any dashboard (custom:sentient-hub-card, custom:sentient-shows-card)
        from homeassistant.components.frontend import add_extra_js_url
        from homeassistant.loader import async_get_integration

        version = (await async_get_integration(hass, DOMAIN)).version
        # each card's address only changes when that card's file does, so a browser's saved copy of
        # the page keeps working across updates that don't touch the cards
        tags = await hass.async_add_executor_job(_card_tags)
        base = FRONTEND_URL
        # 0.80.1–0.83.0 also copied the cards into config/www/sentient: that folder is the user's, so take
        # back only the copies Sentient put there (and the folder, if that leaves it empty)
        await hass.async_add_executor_job(_remove_old_card_copies, Path(hass.config.path("www")))
        for card in CARDS:
            add_extra_js_url(hass, f"{base}/{card}.js?v={tags.get(card, version)}")

        # also as dashboard resources: an app or browser holding an old copy of the page loads those
        # with the dashboard, so the cards are never "not found" after an update
        async def _resources(_event=None) -> None:
            try:
                await async_register_card_resources(hass, version, tags, base)
            except Exception as err:  # noqa: BLE001 - the extra JS above still works in most places
                _LOGGER.debug("Couldn't add the cards as dashboard resources: %s", err)

        if hass.is_running:
            hass.async_create_task(_resources())
        else:
            hass.bus.async_listen_once(EVENT_HOMEASSISTANT_STARTED, _resources)
    return True


def scheduled_by_alarm(alarms: list[dict[str, Any]], entity_id: str, state: str, minute: int, within: int = 60) -> bool:
    """One of Sentient's own enabled, repeating alarms already switches this entity to this state at about this
    time (the heated blanket off at 23:00) — so the house switching it isn't a habit to learn or offer."""
    service = {"on": "turn_on", "off": "turn_off", "open": "open_cover", "closed": "close_cover"}.get(state)
    for alarm in alarms:
        if not alarm.get("enabled", True) or not alarm.get("days"):
            continue
        try:
            hh, mm = (int(x) for x in str(alarm.get("time") or "").split(":")[:2])
        except ValueError:
            continue
        gap = abs(hh * 60 + mm - minute)
        if min(gap, 1440 - gap) > within:
            continue
        for action in alarm.get("actions") or []:
            name = str(action.get("action") or action.get("service") or "")
            targets = (action.get("target") or {}).get("entity_id") or (action.get("data") or {}).get("entity_id") or []
            targets = [targets] if isinstance(targets, str) else list(targets)
            if entity_id in targets and service and name.endswith("." + service):
                return True
    return False


CARDS = ("sentient-shows-card", "sentient-hub-card", "sentient-screen-card")
LOCAL_CARDS_URL = "/local/sentient"  # where 0.80.1–0.83.0 put copies of the cards; resources there move back


def _remove_old_card_copies(www: Path) -> list[str]:
    """Delete the card copies older versions left in config/www/sentient — only Sentient's own card files."""
    folder = www / "sentient"
    removed: list[str] = []
    if not folder.is_dir():
        return removed
    for card in CARDS:
        target = folder / f"{card}.js"
        try:
            if target.is_file() and f'customElements.define("{card}"' in target.read_text(errors="ignore"):
                target.unlink()
                removed.append(target.name)
        except OSError:
            pass
    try:
        if removed and not any(folder.iterdir()):
            folder.rmdir()
    except OSError:
        pass
    return removed


def _card_tags(cards: tuple[str, ...] = CARDS) -> dict[str, str]:
    """A short fingerprint of each file, for its address."""
    import hashlib
    from pathlib import Path

    out = {}
    for card in cards:
        try:
            out[card] = hashlib.md5((Path(__file__).parent / "frontend" / f"{card}.js").read_bytes()).hexdigest()[:10]  # noqa: S324
        except OSError:
            pass
    return out


async def async_register_card_resources(hass: HomeAssistant, version: str, tags: dict[str, str] | None = None,
                                        base: str = FRONTEND_URL) -> list[str]:
    """Make sure each card is a module resource on the dashboards, at this version."""
    try:
        from homeassistant.components.lovelace.const import LOVELACE_DATA
    except ImportError:
        return []
    data = hass.data.get(LOVELACE_DATA)
    resources = getattr(data, "resources", None)
    if resources is None or not hasattr(resources, "async_create_item"):
        return []  # YAML-mode resources: nothing we can add
    if not getattr(resources, "loaded", True):
        await resources.async_load()
        resources.loaded = True
    done = []
    for card in CARDS:
        url = f"{base}/{card}.js?v={(tags or {}).get(card, version)}"
        # either address: one from before the cards moved to /local is updated in place
        mine = [r for r in resources.async_items()
                if str(r.get("url", "")).split("?")[0] in (f"{FRONTEND_URL}/{card}.js", f"{LOCAL_CARDS_URL}/{card}.js")]
        if not mine:
            await resources.async_create_item({"res_type": "module", "url": url})
            done.append(url)
            continue
        for extra in mine[1:]:
            await resources.async_delete_item(extra["id"])
        if mine[0].get("url") != url:
            await resources.async_update_item(mine[0]["id"], {"res_type": "module", "url": url})
            done.append(url)
    return done


async def async_setup_entry(hass: HomeAssistant, entry: SentientConfigEntry) -> bool:
    options = _entry_options(entry)
    memory = MemoryStore(hass, entry.entry_id)
    await memory.async_load()
    from .own import OwnActions

    own = OwnActions(hass, entry.entry_id)  # first, so everything after can mark what it does as the house's own
    await own.async_start()
    hass.data[DOMAIN]["own"] = own
    entry.async_on_unload(own.async_stop)
    timeline = Timeline(hass, entry.entry_id)
    await timeline.async_start()
    entry.async_on_unload(timeline.async_stop)
    notifier = Notifier(hass, options)
    notifier.async_start()
    entry.async_on_unload(notifier.async_stop)
    tracker = ActivityTracker(hass, options)
    await tracker.async_start()
    entry.async_on_unload(tracker.async_stop)
    speech = SpeechRouter(hass, options, tracker)
    ai = AIGate(hass, entry.entry_id, options)
    await ai.async_start()
    watches = WatchManager(hass, entry.entry_id, options, speech)
    watches.gate = ai
    spots = Spots(hass, entry.entry_id, options)
    await spots.async_load()
    watches.spots = spots
    await watches.async_start()
    entry.async_on_unload(watches.async_stop)
    feedback = Feedback(hass, entry.entry_id)
    await feedback.async_load()
    from .suggestions import Suggestions

    suggestions = Suggestions(hass, entry.entry_id)
    await suggestions.async_load()
    noticer = Noticer(hass, options, speech, memory, watches)
    noticer.feedback = feedback
    noticer.async_start()
    entry.async_on_unload(noticer.async_stop)
    learner = Learner(hass, entry.entry_id, options, speech, tracker)
    learner.feedback = feedback
    await learner.async_start()
    entry.async_on_unload(learner.async_stop)
    upkeep = Upkeep(hass, lambda: entry.runtime_data)
    health = HouseHealth(hass, options, lambda: getattr(entry, "runtime_data", None))
    health.was_offline_before = lambda: {k.split(":", 1)[1] for k in (timeline._problems or {}) if k.startswith("offline:")}
    health.async_start()
    entry.async_on_unload(health.async_stop)
    visitors = Visitors(hass, entry.entry_id, options, speech, watches)
    await visitors.async_start()
    entry.async_on_unload(visitors.async_stop)
    timeline.attach_visitors(visitors.log)
    motion_lights = MotionLights(hass, entry.entry_id, speech)
    await motion_lights.async_load()
    motion_lights.async_start()
    entry.async_on_unload(motion_lights.async_stop)
    learner.skip = lambda: motion_lights.managed_lights
    mutes = Mutes(hass, entry.entry_id)
    await mutes.async_load()
    speech.mutes = mutes
    mutes.feedback = feedback
    critical = CriticalAlerts(hass, options, lambda: entry.runtime_data)
    left_open = LeftOpen(hass, options, lambda: entry.runtime_data)
    shortcuts = Shortcuts(hass, entry.entry_id)
    await shortcuts.async_load()
    heating = Heating(hass, entry.entry_id, options, timeline)
    heating.learner = learner
    await heating.async_start()
    entry.async_on_unload(heating.async_stop)
    night_watch = NightWatch(hass, options, lambda: entry.runtime_data)
    lists = {kind: Items(hass, entry.entry_id, kind, KINDS[kind]["defaults"]) for kind in KINDS if "view_of" not in KINDS[kind]}
    for items in lists.values():
        await items.async_load()
    get_rt = lambda: entry.runtime_data  # noqa: E731
    offers = Offers(hass, lists["offers"], get_rt)
    announcements = Announcements(hass, lists["announce"], get_rt)
    alarms = Alarms(hass, lists["alarms"], get_rt)
    checks = CameraChecks(hass, lists["checks"], get_rt)
    autooff = AutoOff(hass, lists["autooff"], get_rt)
    from .rules import Rules

    rules = Rules(hass, lists["rules"], get_rt)
    camsec = CameraSecurity(hass, options, get_rt)
    party = Party(hass, entry.entry_id, options, get_rt)
    followthrough = FollowThrough(hass, speech, timeline)
    followthrough.async_start()
    entry.async_on_unload(followthrough.async_stop)
    followyou = FollowYou(hass, entry.entry_id, options, get_rt)
    loops = Loops(hass, entry.entry_id, options, get_rt)
    conversations = Conversations(hass, entry.entry_id, options)
    curiosity = Curiosity(hass, entry.entry_id, options, get_rt)
    reflection = Reflection(hass, entry.entry_id, options, get_rt)
    drive = Drive(hass, entry.entry_id, options, get_rt)
    foresight = Foresight(hass, options, get_rt)
    sleep = Sleep(hass, entry.entry_id, options, get_rt)
    learner.woke_on = sleep.woke_on
    appliances = Appliances(hass, entry.entry_id, options, get_rt)
    animals = Animals(hass, options, get_rt)
    away = Away(hass, entry.entry_id, options, get_rt)
    from .pets import Pets
    from .rain import Rain
    from .selftest import SelfTest

    selftest = SelfTest(hass, entry.entry_id, options, get_rt)
    rain = Rain(hass, options, get_rt)
    pets = Pets(hass, options, get_rt)
    from .backup import AutoBackup

    autobackup = AutoBackup(hass, entry, options)
    from .bins import Bins

    bins = Bins(hass, options, get_rt)
    from .faces import Faces

    faces = Faces(hass, entry.entry_id, options, get_rt)
    from .clips import Clips

    clips = Clips(hass, options, get_rt)
    from .clipwatch import ClipWatch

    clipwatch = ClipWatch(hass, entry.entry_id, options, get_rt)
    def _house_did() -> dict[str, set[int]]:  # away lights, and everything else the house switched itself
        out = {k: set(v) for k, v in own.times().items()}
        for entity, stamps in away.times().items():
            out.setdefault(entity, set()).update(stamps)
        return out

    learner.extra_automated = _house_did
    learner.covered = lambda entity, state, minute: scheduled_by_alarm(lists["alarms"].listing(), entity, state, minute)
    watchdog = Watchdog(hass, entry.entry_id, options, get_rt)
    screen = Screen(hass, options, get_rt)
    conversations.timeline = timeline
    guard = Guard(hass)
    guard.async_start()
    entry.async_on_unload(guard.async_stop)
    conversations.guard = guard
    notifier.on_snooze = lambda what, delay: watches.add_reminder(what, minutes=delay.total_seconds() / 60)
    noticer.skip = appliances.handled  # appliances (and the rest of their device) say "finished" themselves, without the AI
    shortcuts.on_run = sleep.routine_ran
    speech.on_unheard = followyou.unheard
    welcome = Welcome(hass, options, lambda: entry.runtime_data)
    evidence = Evidence(hass, entry.entry_id, options)
    await evidence.async_start()
    entry.async_on_unload(evidence.async_stop)
    updates = UpdateChecker(hass)
    await updates.async_start(options)
    entry.async_on_unload(updates.stop)
    voice_checks = VoiceChecks(hass, options)
    entry.async_on_unload(voice_checks.stop)
    sessions = Sessions(options)
    entry.async_on_unload(sessions.stop)
    incidents = Incidents(hass, entry.entry_id, options)
    await incidents.async_start()
    entry.async_on_unload(incidents.async_stop)
    from .focus import Focus
    from .threads import Threads

    threads = Threads(hass, entry.entry_id)
    await threads.async_load()
    from .goals import Goals
    from .selfaware import SelfAware

    selfaware = SelfAware(hass, options, get_rt)
    from .tariff import CheapPower

    cheap_power = CheapPower(hass, entry.entry_id, options, get_rt)
    from .maintenance import Maintenance

    maintenance = Maintenance(hass, lists["maintenance"], get_rt)
    from .world import Parcels

    parcels = Parcels(hass, entry.entry_id, options, get_rt)
    visitors.expecting = lambda: parcels.trackers != [] and parcels.expecting_today
    from .rooms import RoomProfiles

    profiles = RoomProfiles(hass, lists["rooms"])
    speech.profiles = profiles
    speech.phone = notifier
    from .trust import Trust

    trust = Trust(hass, entry.entry_id, options, get_rt)
    learner.trust = trust
    heating.trust = trust
    from .holiday import Holiday

    holiday = Holiday(hass, entry.entry_id, options, get_rt)
    speech.holiday = holiday
    notifier.holiday = holiday
    from .proposals import Proposals

    proposals = Proposals(hass, entry, get_rt)
    await proposals.async_start()
    goals = Goals(hass, entry.entry_id, options, get_rt)
    from .books import Books
    from .recipes import Recipes

    books = Books(hass, entry.entry_id, options, get_rt)
    recipes = Recipes(hass, entry.entry_id, options, get_rt)
    entry.runtime_data = SentientRuntime(
        evidence=evidence, sessions=sessions, updates=updates, voice_checks=voice_checks, control=Control(hass, options, timeline), incidents=incidents,
        options=options, memory=memory, speech=speech, tracker=tracker, watches=watches,
        noticer=noticer, learner=learner, upkeep=upkeep, visitors=visitors, spots=spots, health=health,
        motion_lights=motion_lights, timeline=timeline, notifier=notifier, welcome=welcome, mutes=mutes, critical=critical, shortcuts=shortcuts, left_open=left_open,
        feedback=feedback, heating=heating, night_watch=night_watch, followthrough=followthrough,
        lists=lists, offers=offers, announcements=announcements, alarms=alarms, checks=checks, camsec=camsec,
        party=party, followyou=followyou, loops=loops, conversations=conversations, curiosity=curiosity,
        reflection=reflection, drive=drive, autooff=autooff, foresight=foresight, sleep=sleep,
        ai=ai, appliances=appliances, animals=animals, watchdog=watchdog, screen=screen, away=away, suggestions=suggestions,
        selftest=selftest, rain=rain, pets=pets, autobackup=autobackup, bins=bins, faces=faces, rules=rules, clips=clips, clipwatch=clipwatch,
        threads=threads, focus=Focus(), selfaware=selfaware, proposals=proposals, holiday=holiday, trust=trust, profiles=profiles, parcels=parcels, maintenance=maintenance, cheap_power=cheap_power, goals=goals, books=books, recipes=recipes,
    )
    # things that listen for events and use the runtime start only once it exists (an event during set-up
    # would otherwise find no runtime_data)
    for engine in (upkeep, critical, left_open, welcome):
        engine.async_start()
        entry.async_on_unload(engine.async_stop)
    for engine in (followyou, loops, conversations, curiosity, reflection, drive, foresight, sleep, appliances, animals, watchdog, screen, away, selftest, rain, pets, autobackup, bins, faces, clipwatch, selfaware, goals, books, recipes, holiday, trust, parcels, maintenance, cheap_power):
        await engine.async_start()
        entry.async_on_unload(engine.async_stop)
    await party.async_start()
    entry.async_on_unload(party.async_stop)
    night_watch.async_start()
    entry.async_on_unload(night_watch.async_stop)
    for engine in (offers, announcements, alarms, checks, camsec, autooff, rules):
        engine.async_start()
        entry.async_on_unload(engine.async_stop)
    entry.async_on_unload(entry.add_update_listener(_async_options_updated))
    await _async_register_panel(hass, entry)
    await hass.config_entries.async_forward_entry_setups(entry, PLATFORMS)
    return True


async def async_unload_entry(hass: HomeAssistant, entry: SentientConfigEntry) -> bool:
    return await hass.config_entries.async_unload_platforms(entry, PLATFORMS)


async def _async_register_panel(hass: HomeAssistant, entry: ConfigEntry) -> None:
    """Sentient's own sidebar page — part of the integration, not an editable dashboard."""
    options = _entry_options(entry)
    if not options.get(CONF_SIDEBAR, DEFAULT_SIDEBAR) or "frontend" not in hass.config.components:
        return
    from homeassistant.components import frontend, panel_custom
    from homeassistant.loader import async_get_integration

    version = (await async_get_integration(hass, DOMAIN)).version
    if PANEL_URL_PATH in hass.data.get(frontend.DATA_PANELS, {}):
        frontend.async_remove_panel(hass, PANEL_URL_PATH)
    await panel_custom.async_register_panel(
        hass,
        frontend_url_path=PANEL_URL_PATH,
        webcomponent_name="sentient-panel",
        module_url=f"{FRONTEND_URL}/sentient-panel.js?v={(await hass.async_add_executor_job(_card_tags, ('sentient-panel',))).get('sentient-panel', version)}",
        sidebar_title=options.get(CONF_PERSONA) or entry.title,
        sidebar_icon="mdi:brain",
        require_admin=False,
        config={},
    )
    entry.async_on_unload(lambda: frontend.async_remove_panel(hass, PANEL_URL_PATH))


async def _async_options_updated(hass: HomeAssistant, entry: SentientConfigEntry) -> None:
    options = _entry_options(entry)
    entry.runtime_data.voice_checks.stop()
    entry.runtime_data.sessions.stop()  # settings changes invalidate pending authorisations
    entry.runtime_data.options = options
    entry.runtime_data.updates.configure(options)
    entry.runtime_data.voice_checks.options = options
    if not options.get(CONF_ASK_LATER):
        entry.runtime_data.threads.clear()  # switched off: nothing is kept to ask later
    if not options.get("privacy_log", True):
        entry.runtime_data.ai.forget_sent()  # switched off: the record of what left the house goes too
    if not options.get(CONF_SPEECH_DELIVERY_HISTORY, True):
        entry.runtime_data.speech.deliveries.clear()
    entry.runtime_data.speech.options = options
    entry.runtime_data.tracker.options = options
    entry.runtime_data.watches.options = options
    entry.runtime_data.noticer.options = options
    entry.runtime_data.learner.options = options
    entry.runtime_data.visitors.options = options
    entry.runtime_data.spots.options = options
    entry.runtime_data.health.options = options
    entry.runtime_data.notifier.options = options
    entry.runtime_data.welcome.options = options
    entry.runtime_data.critical.options = options
    entry.runtime_data.left_open.options = options
    entry.runtime_data.heating.options = options
    entry.runtime_data.night_watch.options = options
    entry.runtime_data.camsec.options = options
    entry.runtime_data.party.options = options
    for name in ("sessions", "control", "incidents", "evidence", "followyou", "loops", "conversations", "curiosity", "reflection", "drive", "foresight", "sleep", "ai", "appliances", "animals", "watchdog", "screen", "away", "selftest", "rain", "pets", "autobackup", "bins", "faces", "clips", "clipwatch", "selfaware", "goals", "books", "recipes", "holiday", "trust", "parcels", "cheap_power"):
        getattr(entry.runtime_data, name).options = options
    entry.runtime_data.drive.async_setup_triggers()
    hass.bus.async_fire(EVENT_MODE_CHANGED, {"options": True})  # switches refresh what they show
    entry.runtime_data.welcome.async_start()
    entry.runtime_data.health.async_check()
    if "frontend" in hass.config.components:
        from homeassistant.components import frontend

        if PANEL_URL_PATH in hass.data.get(frontend.DATA_PANELS, {}):
            frontend.async_remove_panel(hass, PANEL_URL_PATH)
        await _async_register_panel(hass, entry)


def _runtime(hass: HomeAssistant) -> SentientRuntime:
    entries = hass.config_entries.async_loaded_entries(DOMAIN)
    if not entries:
        raise ServiceValidationError("Sentient is not set up")
    return entries[0].runtime_data


def _register_services(hass: HomeAssistant) -> None:
    async def say(call: ServiceCall) -> ServiceResponse:
        rt = _runtime(hass)
        speaker = call.data.get("speaker")
        targets: list[str] | None = None
        if isinstance(speaker, list):
            targets = speaker
        elif isinstance(speaker, str) and speaker:
            found = rt.speech.find_speaker(speaker)
            targets = [found] if found else None
        result = await rt.speech.async_say(
            call.data["message"], call.data["priority"], targets, source="service"
        )
        return result.as_dict()

    async def remember(call: ServiceCall) -> ServiceResponse:
        memory = _runtime(hass).memory.add(call.data["text"], call.data["category"], source="service")
        return {"saved": memory.text, "id": memory.id}

    async def forget(call: ServiceCall) -> ServiceResponse:
        removed = _runtime(hass).memory.forget(call.data["what"])
        return {"forgotten": removed.text if removed else None}

    async def watch(call: ServiceCall) -> ServiceResponse:
        data = dict(call.data)
        created = _runtime(hass).watches.add(data.pop("what"), data.pop("entity"), **data)
        return created.summary()

    async def adopt_routine(call: ServiceCall) -> ServiceResponse:
        learner = _runtime(hass).learner
        routine = learner.adopt(call.data["routine"], call.data["adopt"]) if "adopt" in call.data else None
        if routine is None and "adopt" not in call.data:
            found = learner._find(call.data["routine"])
            routine = learner.adopt(found.id, not found.adopted) if found else None
        if routine is None:
            raise ServiceValidationError("No learned routine matches that")
        return {"routine": routine.describe(), "house_does_it": routine.adopted}

    async def forget_routine(call: ServiceCall) -> ServiceResponse:
        learner = _runtime(hass).learner
        if call.data.get("restore"):
            back = learner.undismiss(call.data.get("routine") or None)
            return {"restored": back, "note": "They can be learned again from the next nightly check."}
        item = learner.dismiss(call.data.get("routine") or "")
        if item is None:
            raise ServiceValidationError("No learned routine matches that")
        return {"deleted": item.describe()}

    async def briefing(call: ServiceCall) -> ServiceResponse:
        rt = _runtime(hass)
        if call.data.get("speak", True):
            return await rt.upkeep.async_speak_briefing(call.data.get("priority", "normal"))
        return await gather_briefing(hass, rt)

    async def tidy(call: ServiceCall) -> ServiceResponse:
        return await _runtime(hass).upkeep.async_tidy()

    async def learn(call: ServiceCall) -> ServiceResponse:
        rt = _runtime(hass)
        await rt.learner.async_learn()
        heating = await rt.heating.async_learn()
        await rt.night_watch.async_learn()
        return {
            "routines": [r.summary() for r in rt.learner.all()],
            "heating": heating,
            "night_watch": rt.night_watch.describe(),
        }

    async def weekly_report(call: ServiceCall) -> ServiceResponse:
        return _runtime(hass).upkeep.weekly_report()

    async def party(call: ServiceCall) -> ServiceResponse:
        rt = _runtime(hass)
        if not call.data["on"]:
            return await rt.party.async_end()
        lights = call.data.get("lights") or (rt.party.lights_for(call.data["area"]) if call.data.get("area") else None)
        if call.data.get("area") and not lights:
            return {"error": f"No lights found in {call.data['area']}"}
        return await rt.party.async_party(call.data.get("style"), call.data.get("minutes"), lights)

    async def ask(call: ServiceCall) -> ServiceResponse:
        rt = _runtime(hass)
        targets = None
        if speaker := call.data.get("speaker"):
            found = rt.speech.find_speaker(speaker)
            targets = [found] if found else None
        result = await rt.speech.async_ask(
            call.data["question"], call.data["priority"], targets, source="service",
            extra_prompt=call.data.get("extra_prompt"), preannounce=call.data.get("preannounce"),
        )
        return result.as_dict()

    async def remind(call: ServiceCall) -> ServiceResponse:
        created = _runtime(hass).watches.add_reminder(
            call.data["message"], until=call.data.get("at"), minutes=call.data.get("minutes"),
            speaker=call.data.get("speaker"), priority=call.data["priority"],
        )
        return created.summary()

    async def cancel_watch(call: ServiceCall) -> ServiceResponse:
        removed = _runtime(hass).watches.cancel(call.data["what"])
        return {"cancelled": removed.what if removed else None}

    hass.services.async_register(
        DOMAIN, SERVICE_WATCH, watch, schema=WATCH_SCHEMA, supports_response=SupportsResponse.OPTIONAL
    )
    hass.services.async_register(
        DOMAIN, SERVICE_BRIEFING, briefing,
        schema=vol.Schema({
            vol.Optional("speak", default=True): cv.boolean,
            vol.Optional("priority", default="normal"): vol.In(PRIORITIES),
        }),
        supports_response=SupportsResponse.OPTIONAL,
    )
    # admin only: rewrites the memory store
    async_register_admin_service(hass, DOMAIN, SERVICE_TIDY, tidy, supports_response=SupportsResponse.OPTIONAL)
    hass.services.async_register(
        DOMAIN, SERVICE_ADOPT, adopt_routine,
        schema=vol.Schema({vol.Required("routine"): cv.string, vol.Optional("adopt"): cv.boolean}),
        supports_response=SupportsResponse.OPTIONAL,
    )
    hass.services.async_register(
        DOMAIN, SERVICE_FORGET_ROUTINE, forget_routine,
        schema=vol.Schema({vol.Optional("routine"): cv.string, vol.Optional("restore", default=False): cv.boolean}),
        supports_response=SupportsResponse.OPTIONAL,
    )
    hass.services.async_register(DOMAIN, "weekly_report", weekly_report, supports_response=SupportsResponse.OPTIONAL)
    hass.services.async_register(
        DOMAIN, "party", party, schema=PARTY_SCHEMA, supports_response=SupportsResponse.OPTIONAL
    )
    async_register_admin_service(hass, DOMAIN, SERVICE_LEARN, learn, supports_response=SupportsResponse.OPTIONAL)
    hass.services.async_register(
        DOMAIN, SERVICE_ASK, ask, schema=ASK_SCHEMA, supports_response=SupportsResponse.OPTIONAL
    )
    hass.services.async_register(
        DOMAIN, SERVICE_REMIND, remind, schema=REMIND_SCHEMA, supports_response=SupportsResponse.OPTIONAL
    )
    hass.services.async_register(
        DOMAIN, SERVICE_CANCEL_WATCH, cancel_watch, schema=CANCEL_WATCH_SCHEMA,
        supports_response=SupportsResponse.OPTIONAL,
    )
    hass.services.async_register(
        DOMAIN, SERVICE_SAY, say, schema=SAY_SCHEMA, supports_response=SupportsResponse.OPTIONAL
    )
    hass.services.async_register(
        DOMAIN, SERVICE_REMEMBER, remember, schema=REMEMBER_SCHEMA,
        supports_response=SupportsResponse.OPTIONAL,
    )
    async def reflect(call: ServiceCall) -> ServiceResponse:
        return await _runtime(hass).reflection.async_reflect()

    async def house_asleep(call: ServiceCall) -> ServiceResponse:
        return await _runtime(hass).sleep.async_set(call.data["asleep"], "set by hand")

    hass.services.async_register(
        DOMAIN, "set_asleep", house_asleep, schema=vol.Schema({vol.Required("asleep"): cv.boolean}),
        supports_response=SupportsResponse.OPTIONAL,
    )

    async def show_on_screen(call: ServiceCall) -> ServiceResponse:
        return _runtime(hass).screen.show(
            call.data["message"], "service", alert=call.data.get("alert", False), camera=call.data.get("camera"),
            minutes=call.data.get("minutes"),
        )

    async def clear_screen(call: ServiceCall) -> None:
        _runtime(hass).screen.dismiss(call.data.get("card_id"))

    async def morning_card(call: ServiceCall) -> ServiceResponse:
        rt = _runtime(hass)
        rt.screen.morning = None
        return await rt.screen.async_morning() or {}

    hass.services.async_register(
        DOMAIN, "show_on_screen", show_on_screen, supports_response=SupportsResponse.OPTIONAL,
        schema=vol.Schema({vol.Required("message"): cv.string, vol.Optional("camera"): cv.entity_id,
                           vol.Optional("minutes"): vol.Coerce(float), vol.Optional("alert"): cv.boolean}),
    )
    hass.services.async_register(DOMAIN, "clear_screen", clear_screen,
                                 schema=vol.Schema({vol.Optional("card_id"): cv.string}))
    hass.services.async_register(DOMAIN, "morning_card", morning_card, supports_response=SupportsResponse.OPTIONAL)

    async def check_now(call: ServiceCall) -> ServiceResponse:
        """Run one of the optional checks straight away (works even when its schedule is off)."""
        rt, what = _runtime(hass), call.data["what"]
        if what == "self_test":
            return await rt.selftest.async_run(force=True)
        if what == "rain":
            return await rt.rain.async_check(force=True)
        if what == "hutch":
            return await rt.pets.async_hutch(force=True)
        if what == "bins":
            return await rt.bins.async_check(force=True)
        if what == "faces_forget":
            # deletes from Frigate's face library: only when face recognition is on and forgetting has a time set
            from .const import CONF_FACE_FORGET_HOURS

            if not (rt.faces.enabled or rt.faces.teaching) or float(rt.options.get(CONF_FACE_FORGET_HOURS) or 0) <= 0:
                raise ServiceValidationError("Forgetting unnamed faces is off: turn on faces and set how many hours to keep "
                                         "unnamed faces in Settings first")
            return await rt.faces.async_forget_old()
        if what == "clip_watch":
            cams = rt.clipwatch.cameras()
            return await rt.clipwatch.async_latest(cams[0]) if cams else {"error": "no clip camera chosen"}
        if what == "faces":
            cams = rt.faces.teach_cameras()
            return await rt.faces.async_ask(cams[0], force=True) if cams else {"error": "no camera chosen"}
        return await rt.pets.async_cat(force=True)

    async_register_admin_service(
        hass, DOMAIN, "check_now", check_now, supports_response=SupportsResponse.OPTIONAL,
        schema=vol.Schema({vol.Required("what"): vol.In(["self_test", "rain", "hutch", "cat", "bins", "faces", "faces_forget", "clip_watch"])}),
    )

    async def maintenance_done(call: ServiceCall) -> ServiceResponse:
        out = _runtime(hass).maintenance.done(call.data["job"])
        if out is None:
            raise ServiceValidationError(f"No maintenance job called {call.data['job']}")
        return out

    hass.services.async_register(DOMAIN, "maintenance_done", maintenance_done, supports_response=SupportsResponse.OPTIONAL,
                                 schema=vol.Schema({vol.Required("job"): cv.string}))

    async def notice(call: ServiceCall) -> ServiceResponse:
        """Pass something through Sentient's speech rules (and, by default, its decision whether it's worth saying)."""
        return await _runtime(hass).noticer.async_notice(
            call.data["message"], call.data.get("entity_id"), call.data["decide"], call.data["priority"])

    hass.services.async_register(
        DOMAIN, "notice", notice, supports_response=SupportsResponse.OPTIONAL,
        schema=vol.Schema({vol.Required("message"): cv.string, vol.Optional("entity_id"): cv.entity_id,
                           vol.Optional("decide", default=True): cv.boolean,
                           vol.Optional("priority", default="normal"): vol.In(["low", "normal", "urgent"])}),
    )

    async def look_ahead(call: ServiceCall) -> ServiceResponse:
        return await _runtime(hass).foresight.async_look_ahead(speak=call.data.get("speak", False))

    hass.services.async_register(
        DOMAIN, "look_ahead", look_ahead, schema=vol.Schema({vol.Optional("speak", default=False): cv.boolean}),
        supports_response=SupportsResponse.OPTIONAL,
    )

    async def drive_check(call: ServiceCall) -> ServiceResponse:
        return await _runtime(hass).drive.async_check(force=True)

    async def wonder(call: ServiceCall) -> ServiceResponse:
        rt = _runtime(hass)
        if call.data.get("dismiss"):
            rt.curiosity.dismiss(call.data["dismiss"])
        if call.data.get("question"):
            rt.curiosity.wonder(call.data["question"], source="service")
        if call.data.get("ask_now"):
            return await rt.curiosity.async_ask(force=True)
        return rt.curiosity.listing()

    async def follow_up(call: ServiceCall) -> ServiceResponse:
        d = call.data
        return _runtime(hass).loops.add(
            d["minutes"], d["about"], d.get("entity"), d.get("state"), d.get("above"), d.get("below"),
            d.get("if_done"), d.get("if_not"), d.get("speaker"),
        )

    async def cancel_follow_up(call: ServiceCall) -> ServiceResponse:
        gone = _runtime(hass).loops.cancel(call.data["about"])
        return {"cancelled": gone}

    hass.services.async_register(
        DOMAIN, "cancel_follow_up", cancel_follow_up, schema=vol.Schema({vol.Required("about"): cv.string}),
        supports_response=SupportsResponse.OPTIONAL,
    )
    hass.services.async_register(DOMAIN, "reflect", reflect, supports_response=SupportsResponse.OPTIONAL)
    hass.services.async_register(DOMAIN, "check_drive", drive_check, supports_response=SupportsResponse.OPTIONAL)
    hass.services.async_register(
        DOMAIN, "wonder", wonder,
        schema=vol.Schema({vol.Optional("question"): cv.string, vol.Optional("dismiss"): cv.string,
                           vol.Optional("ask_now", default=False): cv.boolean}),
        supports_response=SupportsResponse.OPTIONAL,
    )
    hass.services.async_register(
        DOMAIN, "follow_up", follow_up,
        schema=vol.Schema({
            vol.Required("minutes"): vol.All(vol.Coerce(float), vol.Range(min=1, max=1440)),
            vol.Required("about"): cv.string,
            vol.Optional("entity"): cv.entity_id,
            vol.Optional("state"): cv.string,
            vol.Optional("above"): vol.Coerce(float),
            vol.Optional("below"): vol.Coerce(float),
            vol.Optional("if_done"): cv.string,
            vol.Optional("if_not"): cv.string,
            vol.Optional("speaker"): cv.string,
        }),
        supports_response=SupportsResponse.OPTIONAL,
    )

    async def run_routine(call: ServiceCall) -> ServiceResponse:
        rt = _runtime(hass)
        result = await rt.shortcuts.async_run(call.data["name"], call.context, call.data.get("details"))
        if result.get("said") and call.data.get("speak", True):
            await rt.speech.async_say(result["said"], "urgent", source="routine")
        if not result.get("error"):
            rt.timeline.add("mode", f"Ran {result['routine']}", result.get("said") or None)
        return result

    hass.services.async_register(
        DOMAIN, "run_routine", run_routine,
        schema=vol.Schema({vol.Required("name"): cv.string, vol.Optional("speak", default=True): cv.boolean,
                           vol.Optional("details"): dict}),
        supports_response=SupportsResponse.OPTIONAL,
    )

    async def hush_service(call: ServiceCall) -> ServiceResponse:
        from datetime import timedelta

        from homeassistant.util import dt as dt_util

        from .switch import hush

        entries = hass.config_entries.async_loaded_entries(DOMAIN)
        if not entries:
            raise ServiceValidationError("Sentient is not set up")
        minutes = float(call.data["minutes"])
        until = dt_util.utcnow() + timedelta(minutes=minutes) if minutes > 0 else None
        hush(hass, entries[0], until)
        return {"hushed_until": until.isoformat() if until else None}

    hass.services.async_register(
        DOMAIN, "hush", hush_service,
        schema=vol.Schema({vol.Required("minutes"): vol.All(vol.Coerce(float), vol.Range(min=0, max=720))}),
        supports_response=SupportsResponse.OPTIONAL,
    )
    hass.services.async_register(
        DOMAIN, SERVICE_FORGET, forget, schema=FORGET_SCHEMA,
        supports_response=SupportsResponse.OPTIONAL,
    )
