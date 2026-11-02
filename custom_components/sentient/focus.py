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

ALWAYS = {
    "sentient_session", "sentient_incidents", "sentient_remember", "sentient_correction", "sentient_recall",
    "sentient_forget", "sentient_why", "sentient_conversations", "sentient_follow_up", "sentient_accept_offer",
    "sentient_search", "sentient_ask_later", "sentient_self",
}

TOPICS: dict[str, tuple[re.Pattern, set[str]]] = {
    name: (re.compile(words, re.I), set(tools)) for name, words, tools in [
        ("cameras", r"camera|\bdoor|gate|garden|shed|drive|parcel|deliver|post\b|visitor|someone|anyone|\bwho\b|\bsee\b|"
                    r"look|\bbins?\b|rabbit|hutch|\bcats?\b|\bcars?\b|what was that|outside",
         ["sentient_look", "sentient_spot", "sentient_what_happened", "sentient_visitors", "sentient_timeline"]),
        ("heating", r"warm|cold|heat|temperature|degrees|radiator|boiler|thermostat|energy|electric|power|cost|bill|usage",
         ["sentient_heating", "sentient_energy"]),
        ("later", r"remind|watch|keep an eye|tell me when|let me know|\bwake|alarm|appointment|later|tomorrow|tonight|"
                  r"\bin (?:a|an|\d+)\b|minutes?|hours?|cancel|o'?clock",
         ["sentient_watch", "sentient_remind", "sentient_watches", "sentient_cancel_watch", "sentient_alarm"]),
        ("security", r"\block|secure|\bopen|\bshut|closed|window|\bdoor|alarm|night watch|\bsafe\b",
         ["sentient_security", "sentient_night_watch", "sentient_house_check"]),
        ("routines", r"good ?night|morning|\bbed\b|routine|hoover|vacuum|party|disco|light show|scene|briefing|"
                     r"motion light|\brule\b|every time|whenever|automat",
         ["sentient_routines", "sentient_adopt_routine", "sentient_forget_routine", "sentient_run_routine",
          "sentient_briefing", "sentient_party", "sentient_scenes", "sentient_motion_lights", "sentient_make_rule"]),
        ("quiet", r"quiet|shush|hush|stop (?:telling|saying|talking)|guests?|talk again|shut up",
         ["sentient_quiet", "sentient_shush"]),
        ("manuals", r"manual|instructions|warranty|guarantee|receipt|filter|descale|model number|how do (?:i|you) (?:clean|reset|change|fit)",
         ["sentient_manual"]),
        ("goals", r"\bgoals?\b|\baim\b|target|use less|cut (?:down|back)|reduce|save (?:energy|money)|how are we doing",
         ["sentient_goal", "sentient_energy", "sentient_heating"]),
        ("books", r"\bbook|read (?:to me|me|my|the|some|for)|carry on (?:reading|with)|chapter|audiobook|story|bedtime",
         ["sentient_book"]),
        ("recipes", r"recipe|cook|bake|make (?:the|a|some)|ingredients|next step|step|what'?s next|repeat that|for dinner|for tea",
         ["sentient_recipe"]),
        ("house", r"\bhouse\b|\bhome\b|happen|while i was|today|this week|check|anything|how'?s|status|announce|"
                  r"tell (?:the|every)|learn",
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
