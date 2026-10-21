"""Sentient as an LLM API.

Any conversation agent (Google AI, OpenAI, Ollama, ...) can tick "Sentient"
under *Control Home Assistant*. It then sees the house's memories in its
prompt and gets tools to remember, recall, forget and speak through the house.
That gives every voice in the house one shared brain.
"""

from __future__ import annotations

from typing import TYPE_CHECKING, Any

import voluptuous as vol

from homeassistant.core import HomeAssistant
from homeassistant.exceptions import HomeAssistantError
from homeassistant.helpers import area_registry as ar
from homeassistant.helpers import config_validation as cv
from homeassistant.helpers import device_registry as dr
from homeassistant.helpers import llm
from homeassistant.util import dt as dt_util
from homeassistant.util.json import JsonObjectType

from .const import (
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


class RememberTool(llm.Tool):
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


class RecallTool(llm.Tool):
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


class ForgetTool(llm.Tool):
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


class AnnounceTool(llm.Tool):
    name = "sentient_announce"
    description = (
        "Say something out loud through the house's speakers, e.g. 'tell the kitchen "
        "dinner is ready'. Leave 'speaker' empty to use the room where someone was last "
        "seen. Respects quiet hours unless priority is urgent."
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
        if args.get("speaker"):
            found = rt.speech.find_speaker(args["speaker"])
            if found is None:
                return {"spoken": False, "reason": f"no speaker matches '{args['speaker']}'",
                        "speakers": rt.speech.describe_rooms()}
            targets = [found]
        result = await rt.speech.async_say(
            args["message"], args.get("priority", "normal"), targets, source="conversation"
        )
        return result.as_dict()


class WatchTool(llm.Tool):
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


class RemindTool(llm.Tool):
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


class ListWatchesTool(llm.Tool):
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


class CancelWatchTool(llm.Tool):
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


class LookTool(llm.Tool):
    name = "sentient_look"
    description = (
        "Look through a camera right now and answer a question about what it sees, e.g. "
        "'what's going on in the garden?', 'is there a parcel at the front door?', 'are the "
        "bins out?'. 'camera' can be the camera's name or its area. Answer the person in "
        "your own words from the result."
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


class RoutinesTool(llm.Tool):
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
        return {"routines": [r.summary() for r in rt.learner.routines.values()]}


class AdoptRoutineTool(llm.Tool):
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


class BriefingTool(llm.Tool):
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

        return gather_briefing(hass, rt)


class VisitorsTool(llm.Tool):
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


class SpotTool(llm.Tool):
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
        rt.watches.spend()
        try:
            spot = await rt.spots.async_learn(camera, args["name"])
        except HomeAssistantError as err:
            return {"error": str(err)}
        return {"learned": spot["name"], "camera": hass.states.get(camera).name}


class SentientAPI(llm.API):
    """Shared brain exposed to conversation agents."""

    def __init__(self, hass: HomeAssistant) -> None:
        super().__init__(hass=hass, id=LLM_API_ID, name="Sentient")

    async def async_get_api_instance(self, llm_context: llm.LLMContext) -> llm.APIInstance:
        return llm.APIInstance(
            api=self,
            api_prompt=self._prompt(llm_context),
            llm_context=llm_context,
            tools=[RememberTool(), RecallTool(), ForgetTool(), AnnounceTool(),
                   WatchTool(), RemindTool(), ListWatchesTool(), CancelWatchTool(), LookTool(),
                   RoutinesTool(), AdoptRoutineTool(), BriefingTool(), VisitorsTool(), SpotTool()],
        )

    def _prompt(self, llm_context: llm.LLMContext) -> str:
        rt = _runtime(self.hass)
        if rt is None:
            return "Sentient (the house's long-term brain) is not loaded right now."
        opts: dict[str, Any] = rt.options
        persona = opts.get(CONF_PERSONA) or DEFAULT_PERSONA
        lines = [
            f"You are {persona}, the brain of this home. The same memory is shared by "
            "every voice and automation in the house.",
            f"Local time: {dt_util.now().strftime('%A %d %B %Y, %H:%M')}.",
        ]
        if rules := (opts.get(CONF_HOUSE_RULES) or "").strip():
            lines.append(f"House rules you must always follow:\n{rules}")
        room = self._room_of_device(llm_context.device_id)
        if room:
            lines.append(f"The person talking to you is in the {room}.")
        if rt.speech.quiet_now():
            lines.append("It is quiet hours: keep replies short and don't announce elsewhere unless urgent.")
        memories = rt.memory.recent(int(opts.get(CONF_PROMPT_MEMORIES, DEFAULT_PROMPT_MEMORIES)))
        if memories:
            lines.append("Things you know about this household:")
            lines.extend(f"- {m.text}" for m in memories)
            lines.append("Use sentient_recall for anything not listed here.")
        routines = sorted(rt.learner.routines.values(), key=lambda r: r.support, reverse=True)[:8]
        if routines:
            lines.append("Household routines learned from history:")
            lines.extend(
                f"- {r.describe()}{' (the house does this now)' if r.adopted else ''}" for r in routines
            )
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
        lines.append(
            "When someone tells you something worth knowing later, or asks you to "
            "remember it, save it with sentient_remember. Never use the words "
            + ", ".join(f'"{w}"' for w in rt.speech.banned)
            + " when addressing people."
        )
        return "\n".join(lines)

    def _room_of_device(self, device_id: str | None) -> str | None:
        if not device_id:
            return None
        device = dr.async_get(self.hass).async_get(device_id)
        if device is None or not device.area_id:
            return None
        area = ar.async_get(self.hass).async_get_area(device.area_id)
        return area.name if area else None
