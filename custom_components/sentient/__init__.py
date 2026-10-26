"""Sentient — an evolving AI brain for Home Assistant."""

from __future__ import annotations

from dataclasses import dataclass
import logging
from typing import Any

import voluptuous as vol

from homeassistant.config_entries import ConfigEntry
from homeassistant.const import Platform
from homeassistant.core import HomeAssistant, ServiceCall, ServiceResponse, SupportsResponse
from homeassistant.helpers import config_validation as cv
from homeassistant.helpers import llm

from .const import (
    DOMAIN,
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
from .watches import WatchManager
from .speech import SpeechRouter

_LOGGER = logging.getLogger(__name__)

PLATFORMS: list[Platform] = [Platform.SENSOR, Platform.SWITCH]

CONFIG_SCHEMA = cv.config_entry_only_config_schema(DOMAIN)


@dataclass
class SentientRuntime:
    """Everything a loaded Sentient entry owns."""

    options: dict[str, Any]
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
    heating: Heating
    night_watch: NightWatch
    followthrough: FollowThrough


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
    _register_services(hass)
    from .websocket import async_register as _register_websocket

    _register_websocket(hass)
    return True


async def async_setup_entry(hass: HomeAssistant, entry: SentientConfigEntry) -> bool:
    options = _entry_options(entry)
    memory = MemoryStore(hass, entry.entry_id)
    await memory.async_load()
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
    watches = WatchManager(hass, entry.entry_id, options, speech)
    spots = Spots(hass, entry.entry_id, options)
    await spots.async_load()
    watches.spots = spots
    await watches.async_start()
    entry.async_on_unload(watches.async_stop)
    feedback = Feedback(hass, entry.entry_id)
    await feedback.async_load()
    noticer = Noticer(hass, options, speech, memory, watches)
    noticer.feedback = feedback
    noticer.async_start()
    entry.async_on_unload(noticer.async_stop)
    learner = Learner(hass, entry.entry_id, options, speech, tracker)
    learner.feedback = feedback
    await learner.async_start()
    entry.async_on_unload(learner.async_stop)
    upkeep = Upkeep(hass, lambda: entry.runtime_data)
    upkeep.async_start()
    entry.async_on_unload(upkeep.async_stop)
    health = HouseHealth(hass, options)
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
    critical.async_start()
    entry.async_on_unload(critical.async_stop)
    left_open = LeftOpen(hass, options, lambda: entry.runtime_data)
    left_open.async_start()
    entry.async_on_unload(left_open.async_stop)
    shortcuts = Shortcuts(hass, entry.entry_id)
    await shortcuts.async_load()
    heating = Heating(hass, entry.entry_id, options, timeline)
    heating.learner = learner
    await heating.async_start()
    entry.async_on_unload(heating.async_stop)
    night_watch = NightWatch(hass, options, lambda: entry.runtime_data)
    followthrough = FollowThrough(hass, speech, timeline)
    followthrough.async_start()
    entry.async_on_unload(followthrough.async_stop)
    welcome = Welcome(hass, options, lambda: entry.runtime_data)
    welcome.async_start()
    entry.async_on_unload(welcome.async_stop)
    entry.runtime_data = SentientRuntime(
        options=options, memory=memory, speech=speech, tracker=tracker, watches=watches,
        noticer=noticer, learner=learner, upkeep=upkeep, visitors=visitors, spots=spots, health=health,
        motion_lights=motion_lights, timeline=timeline, notifier=notifier, welcome=welcome, mutes=mutes, critical=critical, shortcuts=shortcuts, left_open=left_open,
        feedback=feedback, heating=heating, night_watch=night_watch, followthrough=followthrough,
    )
    night_watch.async_start()
    entry.async_on_unload(night_watch.async_stop)
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
        module_url=f"{FRONTEND_URL}/sentient-panel.js?v={version}",
        sidebar_title=options.get(CONF_PERSONA) or entry.title,
        sidebar_icon="mdi:brain",
        require_admin=False,
        config={},
    )
    entry.async_on_unload(lambda: frontend.async_remove_panel(hass, PANEL_URL_PATH))


async def _async_options_updated(hass: HomeAssistant, entry: SentientConfigEntry) -> None:
    options = _entry_options(entry)
    entry.runtime_data.options = options
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
        raise vol.Invalid("Sentient is not set up")
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
            raise vol.Invalid("No learned routine matches that")
        return {"routine": routine.describe(), "house_does_it": routine.adopted}

    async def forget_routine(call: ServiceCall) -> ServiceResponse:
        learner = _runtime(hass).learner
        if call.data.get("restore"):
            back = learner.undismiss(call.data.get("routine") or None)
            return {"restored": back, "note": "They can be learned again from the next nightly check."}
        item = learner.dismiss(call.data.get("routine") or "")
        if item is None:
            raise vol.Invalid("No learned routine matches that")
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
    hass.services.async_register(DOMAIN, SERVICE_TIDY, tidy, supports_response=SupportsResponse.OPTIONAL)
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
        DOMAIN, SERVICE_LEARN, learn, supports_response=SupportsResponse.OPTIONAL
    )
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
    async def run_routine(call: ServiceCall) -> ServiceResponse:
        rt = _runtime(hass)
        result = await rt.shortcuts.async_run(call.data["name"], call.context)
        if result.get("said") and call.data.get("speak", True):
            await rt.speech.async_say(result["said"], "urgent", source="routine")
        if not result.get("error"):
            rt.timeline.add("mode", f"Ran {result['routine']}", result.get("said") or None)
        return result

    hass.services.async_register(
        DOMAIN, "run_routine", run_routine,
        schema=vol.Schema({vol.Required("name"): cv.string, vol.Optional("speak", default=True): cv.boolean}),
        supports_response=SupportsResponse.OPTIONAL,
    )

    async def hush_service(call: ServiceCall) -> ServiceResponse:
        from datetime import timedelta

        from homeassistant.util import dt as dt_util

        from .switch import hush

        entries = hass.config_entries.async_loaded_entries(DOMAIN)
        if not entries:
            raise vol.Invalid("Sentient is not set up")
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
