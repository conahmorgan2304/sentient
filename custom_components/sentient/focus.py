"""Focused voice tools: give the voice only the Sentient tools that fit what was just asked.

A cheap model answers better and faster with a dozen tools than with forty. Plain word matching,
no AI. Memory, corrections, "why" and follow-ups are always included; anything that doesn't
clearly match a topic gets every tool, so nothing is ever lost; a plain device command ("turn the
kitchen light off") gets just the always-on ones, since Home Assistant's own tools do the work. The topics picked for a
conversation carry on to its next turn, so a bare "yes" or "the kitchen" still has the tools
the question before it needed.
"""

from __future__ import annotations

from collections import OrderedDict
from contextvars import ContextVar
import re

# the words of the request being answered (set by the "with backup" agent)
current_request: ContextVar[str | None] = ContextVar("sentient_current_request", default=None)
# set while answering through the Alexa skill (the reply is what the Echo says)
via_alexa: ContextVar[bool] = ContextVar("sentient_via_alexa", default=False)
# what a tool asked of the turn it ran in — {"hand_off": "Starting the book."} ends the conversation with those words
# once something long (a story being read) carries on by itself, so nothing waits for a reply over the top of it
turn_flags: ContextVar[dict | None] = ContextVar("sentient_turn_flags", default=None)


def hand_off(say: str) -> None:
    """End this voice turn with just these words and stop listening: the speaker is about to be busy."""
    flags = turn_flags.get()
    if flags is not None:
        flags["hand_off"] = say

ALWAYS = {
    "sentient_security_event", "sentient_security_feedback",
    "sentient_session", "sentient_clarify", "sentient_incidents", "sentient_remember", "sentient_correction", "sentient_undo_last", "sentient_recall",
    "sentient_forget", "sentient_why", "sentient_conversations", "sentient_follow_up", "sentient_accept_offer",
    "sentient_search", "sentient_ask_later", "sentient_self", "sentient_game", "sentient_game_set", "sentient_game_log",
    "sentient_history", "sentient_root_cause", "sentient_evidence",
}

TOPICS: dict[str, tuple[re.Pattern, set[str]]] = {
    name: (re.compile(words, re.I), set(tools)) for name, words, tools in [
        ("cameras", r"camera|\bdoor|gate|garden|shed|drive|parcel|deliver|post\b|visitor|someone|anyone|\bwho\b|\bsee\b|"
                    r"look|\bbins?\b|rabbit|hutch|\bcats?\b|\bcars?\b|what was that|outside",
         ["sentient_look", "sentient_spot", "sentient_what_happened", "sentient_visitors", "sentient_timeline",
          "sentient_camera_activity"]),
        ("heating", r"warm|cold|heat|temperature|degrees|radiator|boiler|thermostat|energy|electric|power|cost|bill|usage",
         ["sentient_heating", "sentient_energy"]),
        ("later", r"remind|watch|keep an eye|tell me when|let me know|\bwake|alarm|appointment|later|tomorrow|tonight|"
                  r"\bin (?:a|an|\d+)\b|minutes?|hours?|cancel|o'?clock",
         ["sentient_watch", "sentient_remind", "sentient_watches", "sentient_cancel_watch", "sentient_alarm"]),
        ("security", r"\block|secure|\bopen|\bshut|closed|window|\bdoor|alarm|night watch|\bsafe\b",
         ["sentient_security", "sentient_night_watch", "sentient_house_check", "sentient_security_audit"]),
        ("batteries", r"batter(?:y|ies)|charge\b|running (?:low|out)",
         ["sentient_batteries"]),
        ("warnings", r"warning|storm|flood|gale|snow|ice\b|weather",
         ["sentient_warnings"]),
        ("email", r"e-?mails?|inbox|\bmail\b",
         ["sentient_email"]),
        ("calendar", r"calendar|clash|diary|appointment|meeting|double.?booked|busy (?:today|tomorrow)|what'?s on\b|"
                     r"(?:this|next) week|weekend|any plans|my plans",
         ["sentient_calendar", "sentient_calendar_clashes"]),
        ("routines", r"good ?night|morning|\bbed\b|routine|hoover|vacuum|party|disco|light show|scene|briefing|"
                     r"motion light|\brule\b|every time|whenever|automat",
         ["sentient_routines", "sentient_adopt_routine", "sentient_forget_routine", "sentient_run_routine",
          "sentient_briefing", "sentient_party", "sentient_scenes", "sentient_motion_lights", "sentient_make_rule",
          "sentient_create_automation"]),
        ("quiet", r"quiet|shush|hush|stop (?:telling|saying|talking)|guests?|talk again|shut up",
         ["sentient_quiet", "sentient_shush"]),
        ("manuals", r"manual|instructions|warranty|guarantee|receipt|filter|descale|model number|how do (?:i|you) (?:clean|reset|change|fit)",
         ["sentient_manual"]),
        ("goals", r"\bgoals?\b|\baim\b|target|use less|cut (?:down|back)|reduce|save (?:energy|money)|how are we doing",
         ["sentient_goal", "sentient_energy", "sentient_heating"]),
        ("books", r"\bbook|read (?:to me|me|my|the|some|for|it|on)|carry on (?:reading|with)|keep reading|chapter|audiobook|"
                  r"stor(?:y|ies)|bedtime|\bpause\b|\bresume\b|where (?:was i|were we|did we)|play (?:the|my|a)\b",
         ["sentient_book"]),
        ("recipes", r"recipe|cook|bake|make (?:the|a|some)|ingredients|next step|step|what'?s next|repeat that|for dinner|for tea",
         ["sentient_recipe"]),
        ("past", r"yesterday|last (?:mon|tues|wednes|thurs|fri|satur|sun)day|last week|days? ago|when did|"
                 r"what did (?:we|i|you)|what happened (?:on|yesterday|last)|\bon (?:mon|tues|wednes|thurs|fri|satur|sun)day\b",
         ["sentient_past_days", "sentient_timeline", "sentient_conversations"]),
        ("plans", r"get (?:the )?(?:house|home|place|everything) ready|ready for|film night|movie night|going away|"
                  r"sort (?:the house|the place|everything) out|set (?:the house |things )?up for|\bplan\b|bedtime for everyone",
         ["sentient_plan"]),
        ("house", r"\bhouse\b|\bhome\b|happen|while i was|today|this week|check|anything|how'?s|status|announce|"
                  r"tell (?:the|every)|learn|(?:get|got|gets|come|came|comes) (?:back|in)\b|arriv|\bleft (?:home|the house|for|at)\b|went out|go(?:ne)? out",
         ["sentient_house_check", "sentient_weekly_report", "sentient_timeline", "sentient_announce",
          "sentient_what_happened"]),
    ]
}


class Focus:
    """Remembers each conversation's last topics, so short follow-ups keep their tools."""

    def __init__(self) -> None:
        self._last: OrderedDict[str, set[str]] = OrderedDict()

    def tools_for(self, text: str | None, conversation_id: str | None = None) -> set[str] | None:
        """The tool names to offer, or None for every tool."""
        if not text:
            return None
        topics = {name for name, (words, _tools) in TOPICS.items() if words.search(text)}
        before = self._last.get(conversation_id or "", set())
        # Multi-system investigations need tools outside the first keyword hit.
        # Prefer completeness to a smaller model prompt for these requests.
        investigating = bool(re.search(
            r"\b(why|investigat\w*|diagnos\w*|figure out|work out|find out|what caused|not working|stopped working)\b",
            text, re.I))
        if investigating and len(topics) >= 2:
            return None
        if not topics and not before:
            from .sessions import COMMAND

            if COMMAND.search(text):
                return set(ALWAYS)  # "turn the kitchen light off": Home Assistant's own tools do that
            return None  # nothing clearly matched: don't guess, offer everything
        if conversation_id:
            self._last[conversation_id] = topics or before
            self._last.move_to_end(conversation_id)
            while len(self._last) > 200:
                self._last.popitem(last=False)
        names = set(ALWAYS)
        for name in topics | before:
            names |= TOPICS[name][1]
        return names
