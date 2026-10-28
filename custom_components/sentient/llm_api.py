"""Sentient as an LLM API.

Any conversation agent (Google AI, OpenAI, Ollama, ...) can tick "Sentient"
under *Control Home Assistant*. It then sees the house's memories in its
prompt and gets tools to remember, recall, forget and speak through the house.
That gives every voice in the house one shared brain.
"""

from __future__ import annotations

import logging
import re
from typing import TYPE_CHECKING, Any

import voluptuous as vol

from homeassistant.core import HomeAssistant
from homeassistant.exceptions import HomeAssistantError
from homeassistant.helpers import area_registry as ar
from homeassistant.helpers import config_validation as cv
from homeassistant.helpers import device_registry as dr
from homeassistant.helpers import entity_registry as er
from homeassistant.helpers import llm
from homeassistant.util import dt as dt_util
from homeassistant.util.json import JsonObjectType

from .const import (
    CONF_FALLBACK_SPEAKERS,
    CONF_RELEVANT_MEMORIES,
    CONF_CONFIRM,
    CONFIRM_KINDS,
    CONF_PROMPT_RECENT,
    CONF_BRIEFING_WEATHER,
    personality_line,
    CONF_HOUSE_RULES,
    CONF_PERSONA,
    CONF_PROMPT_MEMORIES,
    DEFAULT_PERSONA,
    DEFAULT_PROMPT_MEMORIES,
    DOMAIN,
    LLM_API_ID,
    MEMORY_CATEGORIES,
    PRIORITIES,
)

if TYPE_CHECKING:
    from . import SentientRuntime


def _runtime(hass: HomeAssistant) -> SentientRuntime | None:
    entries = hass.config_entries.async_loaded_entries(DOMAIN)
    return entries[0].runtime_data if entries else None


_LOGGER = logging.getLogger(__name__)


def _clips_on(hass: HomeAssistant) -> bool:
    rt = _runtime(hass)
    return rt is not None and bool(rt.options.get("event_clips", False))


def _rules_on(hass: HomeAssistant) -> bool:
    rt = _runtime(hass)
    return rt is not None and bool(rt.options.get("rules", False))


class _Tool(llm.Tool):
    """Every Sentient tool says which integration provides it."""

    integration = DOMAIN

    def __init_subclass__(cls, **kwargs: Any) -> None:
        """Check every tool's arguments first, and never let a tool crash the whole conversation.

        Home Assistant passes the model's arguments straight through, so a missing one would raise
        a KeyError and the voice would fail. Bad arguments go back to the model as an error it can fix.
        """
        super().__init_subclass__(**kwargs)
        inner = cls.__dict__.get("async_call")
        if inner is None:
            return

        async def async_call(self, hass, tool_input, llm_context):  # type: ignore[no-untyped-def]
            try:
                tool_input.tool_args = self.parameters(dict(tool_input.tool_args or {}))
            except vol.Invalid as err:
                return {"error": f"wrong arguments: {err}"}
            try:
                return await inner(self, hass, tool_input, llm_context)
            except HomeAssistantError:
                raise
            except Exception as err:  # noqa: BLE001
                _LOGGER.warning("Sentient tool %s failed: %r", self.name, err)
                return {"error": f"that didn't work ({type(err).__name__})"}

        cls.async_call = async_call


def _read_only() -> Any:
    """Tool annotations for look-up tools (newer Home Assistant only)."""
    if (annotations := getattr(llm, "ToolAnnotations", None)) is None:
        return None
    return annotations(read_only=True, destructive=False, idempotent=True, open_world=False)


class RememberTool(_Tool):
    name = "sentient_remember"
    description = (
        "Save a durable fact to the house's long-term memory so every voice and "
        "automation knows it later. Use for preferences, people, routines and facts "
        "the household tells you, or asks you to remember. One short sentence. "
        "Never store passwords, card or account numbers, or health details."
    )
    parameters = vol.Schema(
        {
            vol.Required("text"): cv.string,
            vol.Optional("category", default="fact"): vol.In(MEMORY_CATEGORIES),
        }
    )

    async def async_call(
        self, hass: HomeAssistant, tool_input: llm.ToolInput, llm_context: llm.LLMContext
    ) -> JsonObjectType:
        rt = _runtime(hass)
        if rt is None:
            return {"error": "Sentient is not loaded"}
        memory = rt.memory.add(
            tool_input.tool_args["text"],
            tool_input.tool_args.get("category", "fact"),
            source=llm_context.platform,
        )
        return {"saved": memory.text, "id": memory.id}


class CorrectionTool(_Tool):
    name = "sentient_correction"
    description = (
        "When someone corrects you — 'no, I meant the lamp', 'not that one', 'that's the wrong room', "
        "'I said the girls' room' — first put it right, then call this with one short lesson so it never "
        "happens again, in any room or voice. Say what was meant, and where if it matters, e.g. "
        "'In the living room, \"the light\" means the lamp, not the ceiling light.' or "
        "'\"The big light\" means the ceiling light.' Not for ordinary facts — use sentient_remember for those."
    )
    parameters = vol.Schema({vol.Required("lesson"): cv.string})

    async def async_call(
        self, hass: HomeAssistant, tool_input: llm.ToolInput, llm_context: llm.LLMContext
    ) -> JsonObjectType:
        rt = _runtime(hass)
        if rt is None:
            return {"error": "Sentient is not loaded"}
        memory = rt.memory.add(tool_input.tool_args["lesson"], "correction", source=llm_context.platform)
        rt.timeline.add("learned", f"Corrected: {memory.text}", "from a conversation")
        return {"learned": memory.text}


class RecallTool(_Tool):
    annotations = _read_only()
    name = "sentient_recall"
    description = (
        "Search the house's long-term memory. Use before answering questions about "
        "the household's people, preferences, routines or anything you were told earlier."
    )
    parameters = vol.Schema({vol.Required("query"): cv.string})

    async def async_call(
        self, hass: HomeAssistant, tool_input: llm.ToolInput, llm_context: llm.LLMContext
    ) -> JsonObjectType:
        rt = _runtime(hass)
        if rt is None:
            return {"error": "Sentient is not loaded"}
        found = rt.memory.search(tool_input.tool_args["query"])
        return {"memories": [{"id": m.id, "text": m.text, "category": m.category} for m in found]}


class ForgetTool(_Tool):
    name = "sentient_forget"
    description = "Remove something from the house's long-term memory when asked to forget it."
    parameters = vol.Schema({vol.Required("what"): cv.string})

    async def async_call(
        self, hass: HomeAssistant, tool_input: llm.ToolInput, llm_context: llm.LLMContext
    ) -> JsonObjectType:
        rt = _runtime(hass)
        if rt is None:
            return {"error": "Sentient is not loaded"}
        removed = rt.memory.forget(tool_input.tool_args["what"])
        return {"forgotten": removed.text if removed else None}


STOPWORDS = {"the", "and", "you", "what", "who", "how", "when", "where", "why", "are", "was", "has", "have", "with", "that",
             "this", "for", "can", "does", "did", "there", "they", "them", "our", "your", "tell", "please", "about", "any",
             "anything", "today", "now", "right", "just", "get", "got", "his", "her", "its", "into", "from", "out", "off"}
RECENT_SKIP = {"held", "digest", "mind", "loop", "learned"}
EVERYWHERE = re.compile(r"(the )?(everywhere|every ?(one|body|room|speaker)s?|all( the)?( (speakers|rooms))?|(the )?whole house|all over( the house)?|house)")


class AnnounceTool(_Tool):
    name = "sentient_announce"
    description = (
        "Say something out loud through the house's speakers, e.g. 'tell the kitchen "
        "dinner is ready'. 'speaker' can be one room, several ('kitchen and upstairs') or "
        "'everywhere' for every speaker in the house. Leave it empty to use the room where someone "
        "was last seen. Respects quiet hours unless priority is urgent."
    )
    parameters = vol.Schema(
        {
            vol.Required("message"): cv.string,
            vol.Optional("speaker"): cv.string,
            vol.Optional("priority", default="normal"): vol.In(PRIORITIES),
        }
    )

    async def async_call(
        self, hass: HomeAssistant, tool_input: llm.ToolInput, llm_context: llm.LLMContext
    ) -> JsonObjectType:
        rt = _runtime(hass)
        if rt is None:
            return {"error": "Sentient is not loaded"}
        args = tool_input.tool_args
        targets = None
        if wanted := (args.get("speaker") or "").strip():
            if EVERYWHERE.fullmatch(wanted.lower()):
                targets = rt.speech.speakers + [s for s in rt.speech.usable(rt.options.get(CONF_FALLBACK_SPEAKERS))
                                                if s not in rt.speech.speakers]
            else:
                targets = []
                for part in re.split(r",|\band\b|&|\+", wanted):
                    if not (part := part.strip()):
                        continue
                    found = rt.speech.find_speaker(part)
                    if found is None:
                        return {"spoken": False, "reason": f"no speaker matches '{part}'",
                                "speakers": rt.speech.describe_rooms()}
                    if found not in targets:
                        targets.append(found)
        result = await rt.speech.async_say(
            args["message"], args.get("priority", "normal"), targets, source="conversation"
        )
        return result.as_dict()


class WatchTool(_Tool):
    name = "sentient_watch"
    description = (
        "Keep an eye on something and speak up when it happens. Two kinds: "
        "(1) a device or sensor reaching a state or crossing a number, e.g. 'tell me when the "
        "washing machine finishes' (entity + to_state) or 'if the bedroom goes above 24' "
        "(entity + above); (2) a camera, e.g. 'watch the garden until 6 and tell me if the cats "
        "go near the rabbit pen' (entity = the camera or its area name, question = a yes/no "
        "question about the image, look_for = the object, e.g. cat, person, car). "
        "Use 'until' for a clock time (e.g. '18:00') or 'minutes'. For a camera, 'wake_on' "
        "can name a zone or object sensor (e.g. 'front garden person occupancy') so it only "
        "looks when something enters that zone. Confirm briefly using the local time "
        "returned; never read the id aloud. For a plain reminder at a time, use sentient_remind. "
        "If a named spot (see sentient_spot) is mentioned, only that part of the picture is checked."
    )
    parameters = vol.Schema(
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
            vol.Optional("repeat"): cv.boolean,
            vol.Optional("priority"): vol.In(PRIORITIES),
            vol.Optional("wake_on"): cv.string,
            vol.Optional("spot"): cv.string,
        }
    )

    async def async_call(
        self, hass: HomeAssistant, tool_input: llm.ToolInput, llm_context: llm.LLMContext
    ) -> JsonObjectType:
        rt = _runtime(hass)
        if rt is None:
            return {"error": "Sentient is not loaded"}
        args = dict(tool_input.tool_args)
        try:
            watch = rt.watches.add(args.pop("what"), args.pop("entity"), **args)
        except HomeAssistantError as err:
            return {"error": str(err)}
        return {"watching": watch.summary()}


class RemindTool(_Tool):
    name = "sentient_remind"
    description = (
        "Remind someone of something at a time, spoken in whichever room people are in, e.g. "
        "'remind me at 6 to put the oven on' (at='18:00') or 'in 20 minutes check the "
        "washing' (minutes=20). 'message' is the exact sentence to say then, written to the "
        "listener (e.g. 'Time to put the oven on.'). Confirm with the local time returned."
    )
    parameters = vol.Schema(
        {
            vol.Required("message"): cv.string,
            vol.Optional("at"): cv.string,
            vol.Optional("minutes"): vol.Coerce(float),
            vol.Optional("speaker"): cv.string,
        }
    )

    async def async_call(
        self, hass: HomeAssistant, tool_input: llm.ToolInput, llm_context: llm.LLMContext
    ) -> JsonObjectType:
        rt = _runtime(hass)
        if rt is None:
            return {"error": "Sentient is not loaded"}
        args = tool_input.tool_args
        try:
            reminder = rt.watches.add_reminder(
                args["message"], until=args.get("at"), minutes=args.get("minutes"), speaker=args.get("speaker")
            )
        except HomeAssistantError as err:
            return {"error": str(err)}
        return {"reminder": reminder.summary()}


class ListWatchesTool(_Tool):
    annotations = _read_only()
    name = "sentient_watches"
    description = "List what the house is keeping an eye on and any reminders, with local times."
    parameters = vol.Schema({})

    async def async_call(
        self, hass: HomeAssistant, tool_input: llm.ToolInput, llm_context: llm.LLMContext
    ) -> JsonObjectType:
        rt = _runtime(hass)
        if rt is None:
            return {"error": "Sentient is not loaded"}
        return {"watches": [w.summary() for w in rt.watches.watches]}


class CancelWatchTool(_Tool):
    name = "sentient_cancel_watch"
    description = "Stop keeping an eye on something. 'what' can be words from the watch, e.g. 'the cats'."
    parameters = vol.Schema({vol.Required("what"): cv.string})

    async def async_call(
        self, hass: HomeAssistant, tool_input: llm.ToolInput, llm_context: llm.LLMContext
    ) -> JsonObjectType:
        rt = _runtime(hass)
        if rt is None:
            return {"error": "Sentient is not loaded"}
        removed = rt.watches.cancel(tool_input.tool_args["what"])
        return {"cancelled": removed.what if removed else None}


class RuleTool(_Tool):
    name = "sentient_make_rule"
    description = (
        "Make (or remove) a standing rule: 'when <something> <becomes on/opens/off/home>, <do something>', with "
        "optional conditions — e.g. 'when the back door opens after dark, put the kitchen light on at 30%'. "
        "First call with confirmed false: you get the rule back in plain words — read it back exactly and ask "
        "'shall I set that up?'. Only after a clear yes, call again with the same details and confirmed true. "
        "'do' is a list of actions like {action: 'light.turn_on', target: 'kitchen light', brightness_pct: 30}. "
        "To delete one, give its name and remove true. Rules can never arm, disarm or unlock anything."
    )
    parameters = vol.Schema({
        vol.Required("name"): cv.string,
        vol.Optional("when"): cv.string,
        vol.Optional("becomes"): cv.string,
        vol.Optional("after_dark"): cv.boolean,
        vol.Optional("between"): cv.string,
        vol.Optional("only_if"): cv.string,
        vol.Optional("only_if_is"): cv.string,
        vol.Optional("do"): [vol.Schema({
            vol.Required("action"): cv.string,
            vol.Required("target"): cv.string,
            vol.Optional("brightness_pct"): vol.Coerce(int),
            vol.Optional("temperature"): vol.Coerce(float),
        })],
        vol.Optional("say"): cv.string,
        vol.Optional("confirmed", default=False): cv.boolean,
        vol.Optional("remove", default=False): cv.boolean,
    })

    async def async_call(
        self, hass: HomeAssistant, tool_input: llm.ToolInput, llm_context: llm.LLMContext
    ) -> JsonObjectType:
        from .rules import describe, safe_actions, state_word
        from .watches import resolve_entity

        rt = _runtime(hass)
        if rt is None:
            return {"error": "Sentient is not loaded"}
        args = tool_input.tool_args
        items = rt.lists["rules"]
        if args.get("remove"):
            found = items.find(args["name"])
            if found is None:
                return {"error": f"no rule called '{args['name']}'", "rules": [r["name"] for r in items.listing()]}
            if not args.get("confirmed"):
                return {"read_back": f"Delete the rule '{found['name']}': {describe(hass, found)}",
                        "next": "Ask if they're sure; only after a yes, call again with remove true and confirmed true."}
            items.remove(found["id"])
            return {"removed": found["name"]}
        when = resolve_entity(hass, args.get("when") or "")
        if when is None:
            return {"error": f"I can't find '{args.get('when')}' — ask which device they mean"}
        actions = []
        for a in args.get("do") or []:
            domain = a["action"].split(".", 1)[0]
            target = resolve_entity(hass, a["target"], domain) or resolve_entity(hass, a["target"])
            if target is None:
                return {"error": f"I can't find '{a['target']}' — ask which device they mean"}
            data = {k: a[k] for k in ("brightness_pct", "temperature") if k in a}
            actions.append({"action": a["action"], "target": {"entity_id": target}, **({"data": data} if data else {})})
        if not actions and not args.get("say"):
            return {"error": "the rule needs something to do or say"}
        if why := safe_actions(actions):
            return {"error": why, "say": "Say plainly that rules can't do that."}
        item = {
            "name": args["name"], "enabled": True, "when_entity": when, "when_to": state_word(args.get("becomes") or "on"),
            "after_dark": bool(args.get("after_dark")), "times": args.get("between") or "",
            "only_if": resolve_entity(hass, args["only_if"]) if args.get("only_if") else None,
            "only_if_state": state_word(args.get("only_if_is") or "") if args.get("only_if") else "",
            "actions": actions, "say": args.get("say") or "", "made_by": "voice",
        }
        text = describe(hass, item)
        if not args.get("confirmed"):
            return {"read_back": text, "next": "Read this back and ask 'shall I set that up?'. Only after a clear "
                                               "yes, call sentient_make_rule again with exactly the same details and confirmed true."}
        existing = items.find(args["name"])
        if existing:
            item["id"] = existing["id"]
        items.upsert(item)
        rt.timeline.add("mode", f"New rule: {args['name']}", text)
        return {"made": text}


class WhatHappenedTool(_Tool):
    name = "sentient_what_happened"
    description = (
        "Watch the latest recorded clip from a camera and say what actually happened — for 'what happened at the "
        "door?', 'what was that in the garden?', 'who was at the gate just now?'. 'camera' is the camera's name or "
        "area. Uses the event clip, not a live picture; for what's there right now, use sentient_look."
    )
    parameters = vol.Schema({vol.Required("camera"): cv.string, vol.Optional("question"): cv.string})

    async def async_call(
        self, hass: HomeAssistant, tool_input: llm.ToolInput, llm_context: llm.LLMContext
    ) -> JsonObjectType:
        from .watches import resolve_entity

        rt = _runtime(hass)
        if rt is None:
            return {"error": "Sentient is not loaded"}
        camera = resolve_entity(hass, tool_input.tool_args["camera"], "camera")
        if camera is None or not camera.startswith("camera."):
            return {"error": f"no camera called '{tool_input.tool_args['camera']}'"}
        return await rt.clips.async_what_happened(camera, tool_input.tool_args.get("question"), force=True)


class LookTool(_Tool):
    name = "sentient_look"
    description = (
        "Look through a camera right now and answer a question about what it sees, e.g. "
        "'what's going on in the garden?', 'is there a parcel at the front door?', 'are the "
        "bins out?', 'is the car on the drive?'. 'camera' can be the camera's name or its area. If the "
        "question is about a named spot (the drive, the rabbit pen…), pass it as 'spot' — the right camera "
        "is used automatically. Answer the person in your own words from the result."
    )
    parameters = vol.Schema(
        {vol.Required("camera"): cv.string, vol.Required("question"): cv.string, vol.Optional("spot"): cv.string}
    )

    async def async_call(
        self, hass: HomeAssistant, tool_input: llm.ToolInput, llm_context: llm.LLMContext
    ) -> JsonObjectType:
        rt = _runtime(hass)
        if rt is None:
            return {"error": "Sentient is not loaded"}
        try:
            args = tool_input.tool_args
            return await rt.watches.async_look(args["camera"], args["question"], args.get("spot"))
        except HomeAssistantError as err:
            return {"error": str(err)}


class RoutinesTool(_Tool):
    annotations = _read_only()
    name = "sentient_routines"
    description = (
        "List the household routines learned from history (e.g. 'the hallway light usually goes "
        "on around 5:30pm on weekdays') and which ones the house now does by itself."
    )
    parameters = vol.Schema({})

    async def async_call(
        self, hass: HomeAssistant, tool_input: llm.ToolInput, llm_context: llm.LLMContext
    ) -> JsonObjectType:
        rt = _runtime(hass)
        if rt is None:
            return {"error": "Sentient is not loaded"}
        return {"routines": [r.summary() for r in rt.learner.all()]}


class AdoptRoutineTool(_Tool):
    name = "sentient_adopt_routine"
    description = (
        "Start (adopt=true) or stop (adopt=false) the house doing a learned routine by itself, "
        "e.g. after someone says yes to 'want me to do that for you?', or 'stop turning the "
        "hallway light on for me'. 'routine' is its id or words from it."
    )
    parameters = vol.Schema({vol.Required("routine"): cv.string, vol.Optional("adopt", default=True): cv.boolean})

    async def async_call(
        self, hass: HomeAssistant, tool_input: llm.ToolInput, llm_context: llm.LLMContext
    ) -> JsonObjectType:
        rt = _runtime(hass)
        if rt is None:
            return {"error": "Sentient is not loaded"}
        args = tool_input.tool_args
        routine = rt.learner.adopt(args["routine"], args.get("adopt", True))
        if routine is None:
            return {"error": "No learned routine matches that"}
        return {"routine": routine.describe(), "house_does_it": routine.adopted}


class ForgetRoutineTool(_Tool):
    name = "sentient_forget_routine"
    description = (
        "Delete a learned routine or habit for good when someone doesn't want it ('forget that the "
        "kitchen light goes on at six', 'that's not a routine, delete it'). The house stops doing it "
        "and never learns or offers it again. 'routine' is its id or words from it."
    )
    parameters = vol.Schema({vol.Required("routine"): cv.string})

    async def async_call(
        self, hass: HomeAssistant, tool_input: llm.ToolInput, llm_context: llm.LLMContext
    ) -> JsonObjectType:
        rt = _runtime(hass)
        if rt is None:
            return {"error": "Sentient is not loaded"}
        item = rt.learner.dismiss(tool_input.tool_args["routine"])
        if item is None:
            return {"error": "No learned routine matches that"}
        return {"deleted": item.describe()}


class HeatingTool(_Tool):
    name = "sentient_heating"
    description = (
        "What the house has learned about the heating: how fast each zone warms up, the temperature it's "
        "usually set to, when people usually get up, when it would need to start to be warm by then, and "
        "whether it warms up early by itself. Read-only."
    )
    parameters = vol.Schema({})
    annotations = _read_only()

    async def async_call(
        self, hass: HomeAssistant, tool_input: llm.ToolInput, llm_context: llm.LLMContext
    ) -> JsonObjectType:
        rt = _runtime(hass)
        return rt.heating.report() if rt is not None else {"error": "Sentient is not loaded"}


class NightWatchTool(_Tool):
    name = "sentient_night_watch"
    description = (
        "The night watch: which doors and gates it watches, recent openings at unusual times (and whether "
        "anyone was up inside first), and whether it says alerts out loud. For 'did anything happen in the "
        "night?' or 'was that gate opening anything to worry about?'. Read-only."
    )
    parameters = vol.Schema({})
    annotations = _read_only()

    async def async_call(
        self, hass: HomeAssistant, tool_input: llm.ToolInput, llm_context: llm.LLMContext
    ) -> JsonObjectType:
        rt = _runtime(hass)
        if rt is None:
            return {"error": "Sentient is not loaded"}
        return {**rt.night_watch.describe(), "camera_security": rt.camsec.describe()}


class WeeklyTool(_Tool):
    name = "sentient_weekly_report"
    description = (
        "What the house learned and noticed this week: new habits, what it does for people now, what it's "
        "learned to keep quiet about, heating, night watch, new memories and how much it spoke. For "
        "'what have you learned this week?'. Summarise it in a few sentences."
    )
    parameters = vol.Schema({})
    annotations = _read_only()

    async def async_call(
        self, hass: HomeAssistant, tool_input: llm.ToolInput, llm_context: llm.LLMContext
    ) -> JsonObjectType:
        rt = _runtime(hass)
        if rt is None:
            return {"error": "Sentient is not loaded"}
        from .weekly import build

        return build(rt)


class AcceptOfferTool(_Tool):
    name = "sentient_accept_offer"
    description = (
        "The answer to one of the house's offers ('fancy a brew?', 'shall I put the heating on?'): "
        "accept=true does it, accept=false is a no (and it'll ask less). 'offer' is its id or name; "
        "'note' is any detail they gave."
    )
    parameters = vol.Schema({vol.Required("offer"): cv.string, vol.Optional("accept", default=True): cv.boolean,
                             vol.Optional("note"): cv.string})

    async def async_call(
        self, hass: HomeAssistant, tool_input: llm.ToolInput, llm_context: llm.LLMContext
    ) -> JsonObjectType:
        rt = _runtime(hass)
        if rt is None:
            return {"error": "Sentient is not loaded"}
        a = tool_input.tool_args
        return await rt.offers.async_accept(a["offer"], a.get("accept", True), a.get("note"))


class AlarmTool(_Tool):
    name = "sentient_alarm"
    description = (
        "Alarms and appointments. action 'set': name, time (HH:MM, 24-hour), and either days "
        "(list of mon..sun, or 'weekdays'/'weekends'/'every day') for a repeating alarm, or date "
        "(YYYY-MM-DD) for a one-off on another day (neither = next time it comes round); optional "
        "description and routine (the name of one of the household's named routines to run when it "
        "goes off). action 'list' shows them and when each next goes off. action 'cancel' with name "
        "deletes one; 'off'/'on' with name pauses or resumes it."
    )
    parameters = vol.Schema({
        vol.Required("action"): vol.In(["set", "list", "cancel", "off", "on"]),
        vol.Optional("name"): cv.string, vol.Optional("time"): cv.string,
        vol.Optional("days"): vol.Any(cv.string, [cv.string]), vol.Optional("date"): cv.string,
        vol.Optional("description"): cv.string, vol.Optional("routine"): cv.string,
    })

    async def async_call(
        self, hass: HomeAssistant, tool_input: llm.ToolInput, llm_context: llm.LLMContext
    ) -> JsonObjectType:
        rt = _runtime(hass)
        if rt is None:
            return {"error": "Sentient is not loaded"}
        a = tool_input.tool_args
        action = a["action"]
        if action == "list":
            return {"alarms": rt.alarms.listing()}
        if not a.get("name"):
            return {"error": "Which alarm?"}
        if action in ("cancel", "off", "on"):
            item = rt.lists["alarms"].find(a["name"])
            if item is None:
                return {"error": f"No alarm called {a['name']}", "alarms": [x["name"] for x in rt.alarms.listing()]}
            if action == "cancel":
                rt.lists["alarms"].remove(item["id"])
                return {"cancelled": item["name"]}
            rt.lists["alarms"].update(item["id"], enabled=action == "on")
            return {"alarm": item["name"], "on": action == "on"}
        if not a.get("time"):
            return {"error": "What time?"}
        from .kinds import DAYS

        days = a.get("days") or []
        if isinstance(days, str):
            low = days.lower()
            days = (DAYS if "every" in low or "daily" in low else DAYS[:5] if "weekday" in low
                    else DAYS[5:] if "weekend" in low else [d for d in DAYS if d in low])
        if a.get("routine") and rt.shortcuts.find(a["routine"]) is None:
            return {"error": f"There's no routine called {a['routine']}", "routines": [r.name for r in rt.shortcuts.items.values()]}
        item = rt.alarms.set(a["name"], a["time"], days, a.get("date"), a.get("description") or "", a.get("routine"))
        nxt = next((x for x in rt.alarms.listing() if x["id"] == item["id"]), {})
        return {"set": item["name"], "time": str(item["time"])[:5], "days": item["days"], "next": nxt.get("next")}


class PartyTool(_Tool):
    name = "sentient_party"
    description = (
        "Light shows: lights put on a colour show, then go back exactly as they were. Use for 'disco "
        "mode', 'party lights', 'light show', 'chill lights', 'Halloween lights', 'Christmas lights' and "
        "'stop the disco'. action 'start' (optional style: disco, party, rainbow, chill, halloween or "
        "christmas; minutes; area = a room's lights, or 'everywhere' for every light in the house), 'stop', "
        "or 'status'. Starting again while it's on changes the style or room. If it says it's following the "
        "sound, the lights react to the music in the room."
    )
    parameters = vol.Schema({
        vol.Required("action"): vol.In(["start", "stop", "status"]),
        vol.Optional("style"): cv.string, vol.Optional("minutes"): vol.Coerce(float), vol.Optional("area"): cv.string,
    })

    async def async_call(
        self, hass: HomeAssistant, tool_input: llm.ToolInput, llm_context: llm.LLMContext
    ) -> JsonObjectType:
        rt = _runtime(hass)
        if rt is None:
            return {"error": "Sentient is not loaded"}
        a = tool_input.tool_args
        if a["action"] == "status":
            return rt.party.describe()
        if a["action"] == "stop":
            return await rt.party.async_end()
        style = (a.get("style") or "").strip().lower() or None
        lights = rt.party.lights_for(a.get("area"), llm_context.device_id) if a.get("area") else None
        if a.get("area") and not lights:
            return {"error": f"No lights found in {a['area']}"}
        if lights is None and not rt.party.lights_for() and llm_context.device_id:
            lights = rt.party.lights_for(None, llm_context.device_id)
        return await rt.party.async_party(style, a.get("minutes"), lights)


class BriefingTool(_Tool):
    name = "sentient_briefing"
    description = (
        "Get what matters right now for a briefing or 'what's going on?': reminders due today, "
        "what the house is keeping an eye on, routines coming up, windows or doors open, the "
        "weather and what was said recently. Turn it into a few natural spoken sentences; "
        "skip anything empty."
    )
    parameters = vol.Schema({})

    async def async_call(
        self, hass: HomeAssistant, tool_input: llm.ToolInput, llm_context: llm.LLMContext
    ) -> JsonObjectType:
        rt = _runtime(hass)
        if rt is None:
            return {"error": "Sentient is not loaded"}
        from .upkeep import gather_briefing

        return await gather_briefing(hass, rt)


class VisitorsTool(_Tool):
    annotations = _read_only()
    name = "sentient_visitors"
    description = (
        "Who came to the door recently (from the doorbell camera), e.g. 'did anyone come round?', "
        "'has a parcel come?'. Gives local times and plain descriptions, never names."
    )
    parameters = vol.Schema({})

    async def async_call(
        self, hass: HomeAssistant, tool_input: llm.ToolInput, llm_context: llm.LLMContext
    ) -> JsonObjectType:
        rt = _runtime(hass)
        if rt is None:
            return {"error": "Sentient is not loaded"}
        return {"visitors": rt.visitors.summary(10)}


class SpotTool(_Tool):
    name = "sentient_spot"
    description = (
        "Teach the house a named area inside a camera picture, e.g. 'on the garden camera, the "
        "rabbit pen is a spot' — it finds it in the current picture and remembers where it is. "
        "Later watches and looks that mention it only check that part of the picture. Use "
        "forget=true to remove one, or no name to list the spots."
    )
    parameters = vol.Schema(
        {vol.Optional("camera"): cv.string, vol.Optional("name"): cv.string, vol.Optional("forget"): cv.boolean}
    )

    async def async_call(
        self, hass: HomeAssistant, tool_input: llm.ToolInput, llm_context: llm.LLMContext
    ) -> JsonObjectType:
        rt = _runtime(hass)
        if rt is None:
            return {"error": "Sentient is not loaded"}
        args = tool_input.tool_args
        if not args.get("name"):
            return {"spots": rt.spots.listing()}
        if args.get("forget"):
            return {"forgotten": rt.spots.forget(args["name"])}
        from .watches import resolve_entity

        camera = resolve_entity(hass, args.get("camera") or "", "camera")
        if camera is None or not camera.startswith("camera."):
            return {"error": "Which camera is it on?"}
        rt.watches.spend("question", force=True)
        try:
            spot = await rt.spots.async_learn(camera, args["name"])
        except HomeAssistantError as err:
            return {"error": str(err)}
        return {"learned": spot["name"], "camera": hass.states.get(camera).name}


class MotionLightsTool(_Tool):
    name = "sentient_motion_lights"
    description = (
        "Lights that come on with movement after dark. With no light, lists them. With a light and "
        "on=false/true, stops or restarts that light following motion, e.g. 'stop the landing light "
        "coming on with motion'."
    )
    parameters = vol.Schema({vol.Optional("light"): cv.string, vol.Optional("on"): cv.boolean})

    async def async_call(
        self, hass: HomeAssistant, tool_input: llm.ToolInput, llm_context: llm.LLMContext
    ) -> JsonObjectType:
        rt = _runtime(hass)
        if rt is None:
            return {"error": "Sentient is not loaded"}
        args = tool_input.tool_args
        ml = rt.motion_lights
        if not args.get("light") or "on" not in args:
            return {"motion_lights": ml.describe()}
        rule = ml.find(args["light"])
        if rule is None:
            return {"error": "No motion light matches that", "motion_lights": [d["light"] for d in ml.describe()]}
        ml.set_enabled(rule, bool(args["on"]))
        st = hass.states.get(rule.light)
        return {"light": st.name if st else rule.light, "follows_motion": rule.enabled}


class TimelineTool(_Tool):
    annotations = _read_only()
    name = "sentient_timeline"
    description = (
        "What the house did and noticed recently, newest first: what it said or held back, doorbell "
        "visitors, watches and reminders going off, motion lights, devices going offline. Use for "
        "'what happened while I was out?', 'did anything happen overnight?'."
    )
    parameters = vol.Schema({vol.Optional("hours", default=12): vol.Coerce(float)})

    async def async_call(
        self, hass: HomeAssistant, tool_input: llm.ToolInput, llm_context: llm.LLMContext
    ) -> JsonObjectType:
        rt = _runtime(hass)
        if rt is None:
            return {"error": "Sentient is not loaded"}
        hours = max(0.5, min(72.0, float(tool_input.tool_args.get("hours", 12))))
        items = rt.timeline.since(hours, ["said", "visitor", "watch", "light", "health"], 40)
        from .watches import local_when

        return {
            "hours": hours,
            "events": [
                {"when": local_when(dt_util.parse_datetime(i["time"])), "what": i["text"], **({"detail": i["detail"]} if i.get("detail") else {})}
                for i in items
            ],
        }


class QuietTool(_Tool):
    name = "sentient_quiet"
    description = (
        "Hush the house ('be quiet for an hour': minutes=60, 'you can talk again': minutes=0) — it then "
        "says nothing that isn't urgent. guests=true/false turns guest mode on or off: no remarks or "
        "routine offers, and personal household details stay private."
    )
    parameters = vol.Schema({vol.Optional("minutes"): vol.Coerce(float), vol.Optional("guests"): cv.boolean})

    async def async_call(
        self, hass: HomeAssistant, tool_input: llm.ToolInput, llm_context: llm.LLMContext
    ) -> JsonObjectType:
        from datetime import timedelta

        from .switch import hush, set_guest_mode
        from .watches import local_when

        entries = hass.config_entries.async_loaded_entries(DOMAIN)
        if not entries:
            return {"error": "Sentient is not loaded"}
        entry, args = entries[0], tool_input.tool_args
        out: dict[str, Any] = {}
        if "minutes" in args:
            minutes = max(0.0, min(720.0, float(args["minutes"])))
            until = dt_util.utcnow() + timedelta(minutes=minutes) if minutes else None
            hush(hass, entry, until)
            out["hushed_until"] = local_when(until) if until else None
        if "guests" in args:
            set_guest_mode(hass, entry, bool(args["guests"]))
            out["guest_mode"] = bool(args["guests"])
        return out or {"hushed": entry.runtime_data.speech.hushed(), "guest_mode": entry.runtime_data.speech.guest_mode}


class ShushTool(_Tool):
    name = "sentient_shush"
    description = (
        "Stop the house mentioning something: 'stop telling me about the washing machine' (what='washing "
        "machine' or its entity id), optionally for some hours ('for tonight' ≈ 12). undo=true lets it talk "
        "about it again (no 'what' clears every shush). No arguments lists what's shushed. Urgent and "
        "safety alerts always get through."
    )
    parameters = vol.Schema(
        {vol.Optional("what"): cv.string, vol.Optional("hours"): vol.Coerce(float), vol.Optional("undo"): cv.boolean}
    )

    async def async_call(
        self, hass: HomeAssistant, tool_input: llm.ToolInput, llm_context: llm.LLMContext
    ) -> JsonObjectType:
        rt = _runtime(hass)
        if rt is None:
            return {"error": "Sentient is not loaded"}
        args = tool_input.tool_args
        if args.get("undo"):
            removed = rt.mutes.remove(args.get("what"))
            rt.timeline.add("mode", f"Unshushed: {args.get('what') or 'everything'}")
            return {"unshushed": removed}
        if not args.get("what"):
            return {"shushed": rt.mutes.listing(), "learned_from_feedback": rt.feedback.listing()}
        from .watches import resolve_entity

        target = resolve_entity(hass, args["what"]) or args["what"]
        mute = rt.mutes.add(target, args.get("hours"))
        name = hass.states.get(target).name if hass.states.get(target) else target
        rt.timeline.add("mode", f"Shushed: {name}", f"for {args['hours']:g} hours" if args.get("hours") else "until unshushed")
        return {"shushed": name, "until": mute["until"]}


class RunRoutineTool(_Tool):
    name = "sentient_run_routine"
    description = (
        "Run one of the household's named routines (e.g. 'goodnight', 'good morning', 'leaving'). "
        "Returns what to tell the person — say it in your reply (the house doesn't announce it). Some "
        "routines take details (listed in your instructions, e.g. which rooms): pass them in 'details' as "
        "{name: value}, a list for several."
    )
    parameters = vol.Schema({vol.Required("name"): cv.string, vol.Optional("details"): dict})

    async def async_call(
        self, hass: HomeAssistant, tool_input: llm.ToolInput, llm_context: llm.LLMContext
    ) -> JsonObjectType:
        rt = _runtime(hass)
        if rt is None:
            return {"error": "Sentient is not loaded"}
        result = await rt.shortcuts.async_run(
            tool_input.tool_args["name"], llm_context.context, tool_input.tool_args.get("details")
        )
        if not result.get("error"):
            rt.timeline.add("mode", f"Ran {result['routine']} (by voice)", result.get("said") or None)
        return result


class EnergyTool(_Tool):
    annotations = _read_only()
    name = "sentient_energy"
    description = (
        "Electricity used today by the devices on the Energy dashboard, compared with the past week at "
        "the same time of day: biggest users and anything using far more than usual. For 'what's using "
        "all the electricity?', 'has the heater been on a lot today?'."
    )
    parameters = vol.Schema({})

    async def async_call(
        self, hass: HomeAssistant, tool_input: llm.ToolInput, llm_context: llm.LLMContext
    ) -> JsonObjectType:
        from .energy import async_report, describe

        report = await async_report(hass)
        if not report.get("enabled", True):
            return {"error": report["note"]}
        return {
            "cost_today_gbp": report.get("cost_today"),
            "up_to": report.get("up_to"),
            "total_kwh_today": report.get("total_kwh"),
            "usual_by_now": report.get("usual_kwh"),
            "top": report.get("devices", [])[:6],
            "unusual": describe(report),
        }


class SecurityTool(_Tool):
    annotations = _read_only()
    name = "sentient_security"
    description = (
        "Is the house secure? Lists doors, windows, gates and garage doors that are open, locks that are "
        "unlocked, and the alarm's state. Read-only — it never locks, unlocks, arms or disarms anything. "
        "For 'is everything locked up?', 'have I left a window open?'."
    )
    parameters = vol.Schema({})

    async def async_call(
        self, hass: HomeAssistant, tool_input: llm.ToolInput, llm_context: llm.LLMContext
    ) -> JsonObjectType:
        from .security import check

        return check(hass)


class ScenesTool(_Tool):
    name = "sentient_scene"
    description = (
        "Set the mood: with no 'name', lists the home's scenes and named routines with what's in them, so "
        "you can pick the best one for a request like 'something cosy for movie night'. With 'name', "
        "turns that scene on (or runs that routine)."
    )
    parameters = vol.Schema({vol.Optional("name"): cv.string})

    async def async_call(
        self, hass: HomeAssistant, tool_input: llm.ToolInput, llm_context: llm.LLMContext
    ) -> JsonObjectType:
        rt = _runtime(hass)
        scenes = [st for st in hass.states.async_all("scene")]
        name = (tool_input.tool_args.get("name") or "").strip()
        if not name:
            listing = []
            for st in scenes:
                members = st.attributes.get("entity_id") or []
                listing.append({
                    "name": st.name,
                    "contains": [hass.states.get(e).name if hass.states.get(e) else e for e in members][:8],
                })
            routines = [s.name for s in rt.shortcuts.items.values()] if rt else []
            return {"scenes": listing, "routines": routines}
        low = name.lower()
        match = next((st for st in scenes if st.entity_id == name or st.name.lower() == low), None) or next(
            (st for st in scenes if low in st.name.lower()), None
        )
        if match:
            await hass.services.async_call("scene", "turn_on", {"entity_id": match.entity_id}, blocking=True,
                                           context=llm_context.context)
            return {"scene_on": match.name}
        if rt and rt.shortcuts.find(name):
            return await rt.shortcuts.async_run(name, llm_context.context)
        return {"error": f"No scene or routine called {name}"}


class FollowUpTool(_Tool):
    name = "sentient_follow_up"
    description = (
        "Check back on something later and tell people how it went — close the loop instead of forgetting. "
        "Use when you start something that takes time (heating a room, a wash, a delivery due, a door someone "
        "said they'd shut). Give 'minutes' and 'about' (a few words). To check a device, give 'entity' and "
        "'state', or 'above'/'below' for numbers; optionally 'if_done' and 'if_not', the exact sentences to "
        "say either way. Without an entity, 'if_done' (or 'about') is simply said then."
    )
    parameters = vol.Schema(
        {
            vol.Required("minutes"): vol.Coerce(float),
            vol.Required("about"): cv.string,
            vol.Optional("entity"): cv.string,
            vol.Optional("state"): cv.string,
            vol.Optional("above"): vol.Coerce(float),
            vol.Optional("below"): vol.Coerce(float),
            vol.Optional("if_done"): cv.string,
            vol.Optional("if_not"): cv.string,
            vol.Optional("cancel"): cv.boolean,
        }
    )

    async def async_call(
        self, hass: HomeAssistant, tool_input: llm.ToolInput, llm_context: llm.LLMContext
    ) -> JsonObjectType:
        rt = _runtime(hass)
        if rt is None:
            return {"error": "Sentient is not loaded"}
        a = tool_input.tool_args
        if a.get("cancel"):
            gone = rt.loops.cancel(a["about"])
            return {"cancelled": gone} if gone else {"error": "No follow-up like that"}
        entity = a.get("entity")
        if entity and hass.states.get(entity) is None:
            from .watches import resolve_entity

            entity = resolve_entity(hass, entity)
            if entity is None:
                return {"error": f"I can't find {a['entity']}"}
        speaker = None
        if llm_context.device_id:
            speaker = next((s for s in rt.speech.speakers
                            if (e := er.async_get(hass).async_get(s)) and e.device_id == llm_context.device_id), None)
        return {"follow_up": rt.loops.add(
            a["minutes"], a["about"], entity, a.get("state"), a.get("above"), a.get("below"),
            a.get("if_done"), a.get("if_not"), speaker,
        )}


class ConversationsTool(_Tool):
    annotations = _read_only()
    name = "sentient_conversations"
    description = (
        "What was asked and said in earlier conversations, on any speaker in the house — for 'what was I "
        "asking earlier?', 'what did you tell me about…', or picking up where someone left off in another "
        "room. Optional 'query' words and 'days' (default 2)."
    )
    parameters = vol.Schema({vol.Optional("query"): cv.string, vol.Optional("days"): vol.Coerce(float)})

    async def async_call(
        self, hass: HomeAssistant, tool_input: llm.ToolInput, llm_context: llm.LLMContext
    ) -> JsonObjectType:
        rt = _runtime(hass)
        if rt is None:
            return {"error": "Sentient is not loaded"}
        if rt.speech.guest_mode:
            return {"error": "Guests are visiting: earlier conversations stay private"}
        a = tool_input.tool_args
        return {"conversations": rt.conversations.search(a.get("query"), float(a.get("days") or 2))}


class WhyTool(_Tool):
    annotations = _read_only()
    name = "sentient_why"
    description = (
        "Explain why something happened or changed — 'why did the landing light go off?', 'who turned the "
        "heating up?', 'why did you say that?' — or why something WASN'T said: 'why didn't you tell me the "
        "washing had finished?'. Give 'what' (a device, a room or words from what was said). Returns when it last "
        "changed, whether a person (and in which app), an automation or the house did it, the house's own log "
        "around then (including anything held back and why), and things it noticed but chose not to say. "
        "Answer from these facts in a sentence or two; never guess."
    )
    parameters = vol.Schema({vol.Required("what"): cv.string, vol.Optional("hours"): vol.Coerce(float)})

    async def async_call(
        self, hass: HomeAssistant, tool_input: llm.ToolInput, llm_context: llm.LLMContext
    ) -> JsonObjectType:
        rt = _runtime(hass)
        if rt is None:
            return {"error": "Sentient is not loaded"}
        from .explain import async_explain

        a = tool_input.tool_args
        return await async_explain(hass, rt, a["what"], float(a.get("hours") or 12))


class HouseCheckTool(_Tool):
    annotations = _read_only()
    name = "sentient_house_check"
    description = (
        "Everything about the house at once, for a broad request — 'get the house ready for a cold night', "
        "'anything I need to sort before bed?', 'check the house', 'get ready for the morning'. Returns doors and "
        "windows open and the alarm, lights and fans left on, the heating now and when it needs to start to be "
        "warm tomorrow, tomorrow's weather, animals living outside, appliances running, today's biggest energy "
        "users and anything offline or wearing out. Then do what's needed with your other tools (never the "
        "alarm unless asked), and say in two or three sentences what you did and what still needs a person."
    )
    parameters = vol.Schema({})

    async def async_call(
        self, hass: HomeAssistant, tool_input: llm.ToolInput, llm_context: llm.LLMContext
    ) -> JsonObjectType:
        rt = _runtime(hass)
        if rt is None:
            return {"error": "Sentient is not loaded"}
        from .security import check

        sec = check(hass)
        out: dict[str, Any] = {
            "security": {"summary": sec["summary"], "open": [i["name"] for i in sec["open"]],
                         "unlocked": [i["name"] for i in sec["unlocked"]],
                         "alarm": [f"{a['name']}: {a['state']}" for a in sec["alarms"]]},
            "house": "asleep" if rt.sleep.asleep else "awake",
        }
        on = [s.name for s in hass.states.async_all(("light", "fan"))
              if s.state == "on" and not isinstance(s.attributes.get("entity_id"), list)]
        out["lights_and_fans_on"] = on[:20]
        out["heating"] = [{k: z[k] for k in ("name", "now", "comfortable_at", "warm_by", "needs_to_start") if z.get(k) is not None}
                          for z in rt.heating.report()["zones"]]
        for st in hass.states.async_all("climate"):
            out.setdefault("thermostats", []).append(
                f"{st.name}: {st.state}, set to {st.attributes.get('temperature')}°, now {st.attributes.get('current_temperature')}°")
        try:
            tomorrow = await rt.foresight.gather()
            out["tomorrow"] = {k: v for k, v in tomorrow.items() if k in ("tomorrow", "weather", "calendar", "alarms_tomorrow")}
        except Exception:  # noqa: BLE001
            pass
        if animals := rt.animals.readings():
            out["animals_outside"] = animals
        if rt.options.get("battery_forecast", True) and (batteries := rt.watchdog.battery_forecasts(30)):
            out["batteries_running_out"] = [b["text"] for b in batteries[:5]]
        if running := [a for a in rt.appliances.listing() if a["running"]]:
            out["appliances_running"] = [f"{a['name']} since {a['started']}" + (f", done about {a['done_about']}" if a.get("done_about") else "")
                                         for a in running]
        try:
            from .energy import async_report, describe

            report = await async_report(hass)
            if report.get("devices"):
                out["energy_today"] = {"biggest": [f"{d['name']} {d['kwh']} kWh" for d in report["devices"][:3]],
                                       "unusual": describe(report)}
        except Exception:  # noqa: BLE001
            pass
        if problems := rt.health.describe():
            out["problems"] = problems[:8]
        return out


class SentientAPI(llm.API):
    """Shared brain exposed to conversation agents."""

    def __init__(self, hass: HomeAssistant) -> None:
        super().__init__(hass=hass, id=LLM_API_ID, name="Sentient")

    async def async_get_api_instance(self, llm_context: llm.LLMContext) -> llm.APIInstance:
        conversation_id = None
        try:
            from homeassistant.components.conversation.chat_log import current_chat_log

            if (chat := current_chat_log.get()) is not None:
                conversation_id = chat.conversation_id
        except ImportError:
            pass
        if conversation_id and (rt := _runtime(self.hass)) is not None:
            rt.conversations.attach(conversation_id, llm_context.device_id, llm_context.platform)
        return llm.APIInstance(
            api=self,
            api_prompt=self._prompt(llm_context, conversation_id),
            llm_context=llm_context,
            tools=[RememberTool(), CorrectionTool(), HouseCheckTool(), RecallTool(), ForgetTool(), AnnounceTool(),
                   WatchTool(), RemindTool(), ListWatchesTool(), CancelWatchTool(), LookTool(),
                   RoutinesTool(), AdoptRoutineTool(), ForgetRoutineTool(), *([RuleTool()] if _rules_on(self.hass) else []), *([WhatHappenedTool()] if _clips_on(self.hass) else []), AcceptOfferTool(), AlarmTool(), PartyTool(), HeatingTool(), NightWatchTool(), WeeklyTool(), BriefingTool(), VisitorsTool(), SpotTool(), MotionLightsTool(), TimelineTool(), QuietTool(), ShushTool(), RunRoutineTool(), EnergyTool(), SecurityTool(), ScenesTool(), FollowUpTool(), ConversationsTool(), WhyTool()],
        )

    def _prompt(self, llm_context: llm.LLMContext, conversation_id: str | None = None) -> str:
        rt = _runtime(self.hass)
        if rt is None:
            return "Sentient (the house's long-term brain) is not loaded right now."
        opts: dict[str, Any] = rt.options
        persona = opts.get(CONF_PERSONA) or DEFAULT_PERSONA
        lines = [
            f"You are {persona}, the brain of this home. The same memory is shared by "
            "every voice and automation in the house.",
            personality_line(opts),
            f"Local time: {dt_util.now().strftime('%A %d %B %Y, %H:%M')}.",
        ]
        weather_id = opts.get(CONF_BRIEFING_WEATHER)
        weather = self.hass.states.get(weather_id) if weather_id else next(
            (w for w in self.hass.states.async_all("weather") if w.attributes.get("temperature") is not None), None)
        if weather is not None and weather.attributes.get("temperature") is not None:
            unit = weather.attributes.get("temperature_unit") or "°C"
            lines.append(
                f"Outside right now: {round(float(weather.attributes['temperature']))}{unit}, "
                f"{str(weather.state).replace('partlycloudy', 'partly cloudy').replace('-', ' ')}. This is the only "
                "outside temperature: never give a room or thermostat temperature as the weather. If another "
                "tool gives a different current temperature, this one is right."
            )
        elif weather_id or self.hass.states.async_all("weather"):
            lines.append(
                "The outside temperature isn't known right now (the weather hasn't updated). If asked, say so: "
                "never give a room or thermostat temperature as the weather."
            )
        if rules := (opts.get(CONF_HOUSE_RULES) or "").strip():
            lines.append(f"House rules you must always follow:\n{rules}")
        room = self._room_of_device(llm_context.device_id)
        if room:
            lines.append(f"The person talking to you is in the {room}.")
        if opts.get(CONF_PROMPT_RECENT, True):
            lines.extend(self._recent_lines(rt))
        if confirm := [CONFIRM_KINDS[k] for k in opts.get(CONF_CONFIRM) or [] if k in CONFIRM_KINDS]:
            lines.append(
                "Ask first: before you " + "; or ".join(confirm) + ", say in a few words exactly what you're about "
                "to do and ask \"shall I?\". Only do it after a clear yes in their reply; anything else, leave it. "
                "Turning things off, stopping them or lowering the heating never needs asking."
            )
        if rt.speech.guest_mode:
            lines.append(
                "Guests are visiting: don't bring up personal household details, routines or memories "
                "unless someone explicitly asks."
            )
        usual = rt.sleep.usual()
        if usual["bedtime"] or usual["up"]:
            lines.append(
                "The household usually " + " and ".join(
                    x for x in (f"goes to bed around {usual['bedtime']}" if usual["bedtime"] else "",
                                f"is up around {usual['up']}" if usual["up"] else "") if x
                ) + "."
            )
        if rt.sleep.asleep:
            lines.append(f"The house is asleep (since {rt.sleep.describe()['since']}): keep replies short and quiet.")
        if rt.speech.hushed():
            lines.append("The house has been asked to stay quiet: keep replies short and don't announce anything.")
        elif rt.speech.quiet_now():
            lines.append("It is quiet hours: keep replies short and don't announce elsewhere unless urgent.")
        lessons = [m for m in rt.memory.items if m.category == "correction"][-15:]
        if lessons:
            lines.append("You've been corrected on these before — get them right first time:")
            lines.extend(f"- {m.text}" for m in lessons)
        memories = self._memories(rt, int(opts.get(CONF_PROMPT_MEMORIES, DEFAULT_PROMPT_MEMORIES)),
                                  bool(opts.get(CONF_RELEVANT_MEMORIES, True)))
        if memories:
            lines.append("Things you know about this household:")
            lines.extend(f"- {m.text}" for m in memories)
            lines.append("Use sentient_recall for anything not listed here.")
        if not rt.speech.guest_mode and (talk := rt.conversations.prompt_lines(exclude=conversation_id)):
            lines.append(
                "Recent conversations on the house's speakers (any room) — pick up where they left off if it's "
                "relevant; sentient_conversations has more:"
            )
            lines.extend(talk)
        if drive := rt.drive.prompt_line():
            lines.append(drive)
        if not rt.speech.guest_mode and (seen := rt.faces.prompt_line()):
            lines.append(seen)
        if rt.loops.items:
            lines.append("Follow-ups you've set: " + "; ".join(f"{i['about']} at {i['at']}" for i in rt.loops.listing()[:6]) + ".")
        if spots := rt.spots.describe():
            lines.append(
                "Named camera spots — for questions about these, call sentient_look with the spot; it picks "
                "the camera that can see it: " + "; ".join(spots[:15]) + "."
            )
        offline = [i["name"] for i in rt.health.issues if i.get("kind") == "offline"]
        if offline:
            lines.append(
                "Offline right now (commands to these won't work — say so instead of claiming it's done): "
                + ", ".join(offline[:12]) + "."
            )
        lines.append(
            "After controlling a device, only say it's done if the tool result says it worked; if a device is "
            "unavailable or the action failed, say so plainly."
        )
        routines = sorted(rt.learner.all(), key=lambda r: (r.adopted, r.support), reverse=True)[:10]
        if routines:
            lines.append("Household routines and habits learned from history:")
            lines.extend(
                f"- {r.describe()}{' (the house does this now)' if r.adopted else ''}" for r in routines
            )
        if rt.shortcuts.items:
            lines.append(
                "Named routines you can run with sentient_run_routine: "
                + "; ".join(
                    s.name + (" (details: " + ", ".join(f"{k} = {w}" for k, w in s.input_names().items()) + ")"
                              if s.input_names() else "")
                    for s in rt.shortcuts.items.values()
                ) + "."
            )
        if problems := rt.health.describe():
            lines.append("House health problems right now: " + "; ".join(problems[:6]) + ".")
        if visits := rt.visitors.today():
            lines.append(f"Visitors to the door today: {len(visits)}. Use sentient_visitors for details.")
        watches = rt.watches.watches
        if watches:
            lines.append("You are currently keeping an eye on (and reminders due):")
            lines.extend(
                f"- {'reminder: ' if w.kind == 'reminder' else ''}{w.what} "
                f"({'at' if w.kind == 'reminder' else 'until'} {w.summary()['until']})"
                for w in watches[:10]
            )
        lines.append(
            "When someone asks you to keep an eye on something, or to tell them when something "
            "happens, use sentient_watch; to be reminded at a time, use sentient_remind. Never just "
            "promise to remember."
        )
        lines.append("When someone asks why something happened or who did it, use sentient_why rather than guessing.")
        lines.append("When someone corrects you, put it right and save the lesson with sentient_correction.")
        if gate := getattr(rt, "ai", None):
            if gate.failing or gate.capped:
                lines.append("The house's background AI is unavailable right now, so its own remarks are in plain words.")
        if running := rt.appliances.prompt_line():
            lines.append(running)
        lines.append(
            "When you start something that takes a while to show results (heating, a wash, a delivery), set a "
            "sentient_follow_up so you check back and say how it went."
        )
        lines.append(
            "When someone tells you something worth knowing later, or asks you to "
            "remember it, save it with sentient_remember. Never use the words "
            + ", ".join(f'"{w}"' for w in rt.speech.banned)
            + " when addressing people."
        )
        return "\n".join(lines)

    @staticmethod
    def _asked() -> str:
        """What was just said to the voice (this turn's words are already in the chat log)."""
        try:
            from homeassistant.components.conversation.chat_log import current_chat_log

            log = current_chat_log.get()
            said = [c.content for c in (log.content if log else []) if getattr(c, "role", "") == "user" and c.content]
            return " ".join(said[-2:])
        except Exception:  # noqa: BLE001
            return ""

    def _memories(self, rt: Any, limit: int, relevant: bool) -> list[Any]:
        """The newest memories — or, once there are more than fit, the ones that match what was asked plus the newest."""
        everything = [m for m in rt.memory.items if m.category != "correction"]
        newest = list(reversed(everything))
        if not relevant or len(everything) <= limit or limit <= 0:
            return [m for m in rt.memory.recent(limit + 20) if m.category != "correction"][:limit]
        words = {w for w in re.findall(r"[a-z]{3,}", self._asked().lower()) if w not in STOPWORDS}
        if not words:
            return newest[:limit]
        scored = sorted(((len(words & set(re.findall(r"[a-z]{3,}", m.text.lower()))), i, m) for i, m in enumerate(newest)),
                        key=lambda t: (-t[0], t[1]))
        matched = [m for score, _, m in scored if score > 0][: limit // 2]
        rest = [m for m in newest if m not in matched][: limit - len(matched)]
        return matched + rest

    def _recent_lines(self, rt: Any) -> list[str]:
        """The last hour at a glance, and where people seem to be — so 'what just happened?' needs no tool."""
        out: list[str] = []
        areas = ar.async_get(self.hass)
        busy = [a.name for a_id in rt.tracker.occupied_areas() if (a := areas.async_get_area(a_id))]
        if busy:  # nothing convincing: say nothing (never that the house is empty)
            out.append("Movement lately in: " + ", ".join(busy[:4]) + ".")
        recent = [i for i in rt.timeline.since(1, None, 30) if i["kind"] not in RECENT_SKIP][:5]
        if recent:
            out.append("In the last hour (newest first; sentient_timeline has more):")
            for item in recent:
                when = dt_util.parse_datetime(item["time"])
                out.append(f"- {dt_util.as_local(when):%H:%M} {item['text']}" if when else f"- {item['text']}")
        return out

    def _room_of_device(self, device_id: str | None) -> str | None:
        if not device_id:
            return None
        device = dr.async_get(self.hass).async_get(device_id)
        if device is None or not device.area_id:
            return None
        area = ar.async_get(self.hass).async_get_area(device.area_id)
        return area.name if area else None
