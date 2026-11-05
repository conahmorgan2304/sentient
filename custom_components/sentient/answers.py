"""Answers to the house's own questions, read by plain rules before any AI.

"Fancy a brew?", "Want me to do that for you from now on?" — the reply is nearly always a yes or a no, so
it's read here first: cheaper and faster than the AI, and it can't mistake "it already does that" for a yes.
Only a reply these rules can't place goes to the AI, which then asks again if it's still unclear.
"""

from __future__ import annotations

import re

# "it already does that", "that's already set up", "we've got one for that"
DONE = re.compile(r"\b(?:already|got (?:one|that|it) (?:for|set)|it does (?:that|it)|that'?s (?:set up|sorted|done)|"
                  r"no need,? it)\b", re.I)
NO = re.compile(r"^(?:no|nope|nah|not (?:now|today|tonight|really|yet|at the moment|right now)|no thanks?|no thank you|"
                r"don'?t|do not|leave it|leave that|i'?m (?:ok|okay|fine|good)|it'?s (?:ok|okay|fine)|we'?re (?:ok|fine|good)|"
                r"no need|maybe later|later|another time|never ?mind|forget it|stop)\b", re.I)
YES = re.compile(r"^(?:yes|yeah|yep|yup|yea|aye|sure|ok|okay|alright|all right|please|go on|go ahead|do it|"
                 r"sounds good|why not|definitely|of course|please do|that'?d be (?:great|good|lovely)|lovely|great|"
                 r"perfect|go for it)\b", re.I)
NEGATED = re.compile(r"\b(?:no|not|don'?t|never|n'?t)\b", re.I)
MAX_WORDS = 8  # longer replies are more than a yes or a no: the AI reads those


def classify(text: str) -> str | None:
    """'done', 'no', 'yes', or None when it isn't a plain answer."""
    words = re.sub(r"[^\w\s']", " ", (text or "").lower()).split()
    if not words:
        return None
    said = " ".join(words)
    if DONE.search(said):
        return "done"
    if len(words) > MAX_WORDS:
        return None
    if NO.match(said):
        return "no"
    if YES.match(said) and not NEGATED.search(" ".join(words[1:]).replace("why not", "")):
        return "yes"
    return None
