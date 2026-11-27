"""Self-test: the voice asked a few everyday questions overnight, its answers checked against the house.

With the household questions on (the default), the set goes further, built from this house's own devices so it
works anywhere: when a door or window was last closed, whether a light is on, whether a camera saw anyone in the
last two hours, whether anyone came to the door today, a question it can't know the answer to (inventing one is a
fail, however convincing), something no device can do, and a question too vague to answer without asking which.
Any device action during a question is a fail too. The score, and how it's moved over the last month, is on the
Mind page: the number each change to Sentient should improve.

Quietly, in quiet hours (nightly or weekly, by the setting), the voice agent is asked things like "what's
the temperature outside?", "is anything open?", "what reminders have I got?". Each answer is checked
against the house's real state with plain rules — the right temperature, the right count, "nothing" when
there's nothing. Questions only: nothing is switched. Anything it got wrong shows in house health the next
morning ("the voice said 19° for outside; the weather says 12°"), with the tool it used, so a bad setting
somewhere else gets found. At most one voice turn per question, kept out of household statistics. Off by default.
"""

from __future__ import annotations

import asyncio
from contextvars import ContextVar
from dataclasses import dataclass
from datetime import datetime, timedelta
import logging
import math
import re
from typing import Any, Callable

from homeassistant.const import EVENT_STATE_CHANGED
from homeassistant.core import Context, Event, HomeAssistant, callback
from homeassistant.helpers.event import async_track_time_change
from homeassistant.helpers import area_registry as ar
from homeassistant.helpers.storage import Store
from homeassistant.util import dt as dt_util
from homeassistant.util.ulid import ulid_now

from .const import CONF_BRIEFING_WEATHER, CONF_CHAT_AGENT, CONF_SELF_TEST, CONF_SELF_TEST_HOUSEHOLD, CONF_VOICE_AGENT, DOMAIN
from .security_offers import SECURITY_OFFER  # "Shall I arm it in away mode?" when only asked

_LOGGER = logging.getLogger(__name__)

ACTIVE = ContextVar("sentient_selftest", default=False)

RUN_AT = (3, 25)  # quiet hours, after the overnight think
# The question is already English and Home Assistant is asked for English, but the answer's own language is the
# agent's choice: a German agent answered German and every English word list marked it wrong (issue #235). So the
# question says which language to answer in.
ENGLISH_ASK = "Please answer in English."
NUMBER = re.compile(r"-?\d+(?:\.\d+)?")
WORDS = {"zero": 0, "one": 1, "two": 2, "three": 3, "four": 4, "five": 5, "six": 6, "seven": 7, "eight": 8,
         "nine": 9, "ten": 10, "eleven": 11, "twelve": 12, "thirteen": 13, "fourteen": 14, "fifteen": 15,
         "sixteen": 16, "seventeen": 17, "eighteen": 18, "nineteen": 19, "twenty": 20, "thirty": 30, "forty": 40, "fifty": 50}
# every way of saying "there aren't any": "no", "none", "you don't have any", "there aren't any set", "all clear"…
NONE_SAID = re.compile(
    r"\b(no|none|nothing|not any|not got any|zero|(?:have|has|do|does|is|are)(?:n['’]t| not)(?: \w+)?(?: got)? any|haven['’]t|hasn['’]t|"
    r"aren['’]t|isn['’]t|all clear|all (shut|closed|off)|everything(['’]s| is) (shut|closed|off))\b", re.I)
OPENINGS = {"door", "window", "garage_door", "opening"}
HISTORY_KEEP = 30  # runs kept, for the trend on the Mind page
SAYS_YES = re.compile(r"\b(yes|yeah|someone|somebody|a person|people|(?:one|two|three|four|five|six|\d+) (?:people|visits?|times?|visitors?)|"
                      r"there (?:was|were|has been|have been)(?! no)|(?:was|were) seen|came|called|visited)\b", re.I)
SAYS_NO = re.compile(r"\b(no|nobody|no one|no-one|not seen|nothing|hasn['’]t|haven['’]t|wasn['’]t|weren['’]t|didn['’]t|none)\b", re.I)
SAYS_OFF = re.compile(r"\b(off|not on|isn['’]t on|is not on|switched off|turned off)\b", re.I)
SAYS_ON = re.compile(r"\b(on|switched on|turned on|lit)\b", re.I)
DOESNT_KNOW = re.compile(r"\b(?:don['’]t|do not|didn['’]t) (?:know|have|keep|track|see)|no (?:idea|record|way of knowing|information)|"
                         r"(?:can['’]t|cannot|couldn['’]t|unable to|not able to) (?:know|tell|say|see|find|check|track|access|verify|confirm)|not sure|"
                         r"only (?:track|keep track of|know about|see) |"  # 18–19 Nov: "I can't check what you ate…, as I only track…"
                         r"no(?:thing)? (?:recorded|logged)|wouldn['’]t know", re.I)
CANT_DO = re.compile(r"\b(?:can['’]t|cannot|can not|unable|not able|no way|don['’]t have (?:hands|arms|a body|a way)|physically|"
                     r"i['’]m (?:just )?a voice|not something i can)\b", re.I)
RESULT_STANDS = (", and nothing switched", ", with every tool working", ", without offering to")  # not about wording
ASKS_BACK = re.compile(r"\bwhich\b|what do you mean|do you mean|could you (?:tell|say|clarify|specify)|what (?:device|entity|light|switch|appliance|are you referring to)|can you (?:clarify|specify|tell me)|what (?:would|did) you", re.I)
# tools that change something: using one while only being asked a question is a fail
ACTION_TOOLS = {"HassTurnOn", "HassTurnOff", "HassToggle", "HassLightSet", "HassClimateSetTemperature", "HassSetPosition",
                "HassVacuumStart", "HassVacuumReturnToBase", "HassMediaPause", "HassMediaUnpause", "HassSetVolume",
                "HassMediaNext", "HassMediaPrevious", "HassFanSetSpeed", "HassLawnMowerStartMowing", "HassOpen", "HassClose"}


def _offers_action(answer: str) -> bool:
    """Catch offers of reminders or other help, not only impossible physical tasks."""
    from .reliability import OFFER

    return bool(OFFER.search(answer) or re.search(
        r"\b(?:shall i|should i|would you like me to|do you want me to|want me to|i can also|i could also)\b",
        answer, re.I))


_PHYSICAL_BIN_WORK = re.compile(
    r"\b(?:shall i|should i|would you like me to|do you want me to|want me to|let me|leave it to me|"
    r"i (?:can|could|will|shall|have|already|just)|i['’](?:ll|ve|m))\b(?!['’]t\b)"
    r"[^.?!]{0,35}?(?:tak(?:e|en|ing)|took|put(?:ting)?|mov(?:e|ed|ing)|wheel(?:ed|ing)?|"
    r"bring|brought|empt(?:y|ied|ying)|collect(?:ed|ing)?)\s+(?:out |outside )?"
    r"(?:(?:the |your |our )?(?:rubbish |refuse |wheelie )?bins?|them out)\b",
    re.I,
)


def _offers_bins_action(answer: str) -> bool:
    """Recognise physical offers and claims, including pronouns and inflected verbs."""
    # The user is the person doing the work in these constructions.
    offer = re.sub(r"\b(?:remind|ask) you to\b[^.?!,;]*", "", answer, flags=re.I)
    past = re.search(r"\bi (?:took|put|moved|wheeled|brought|emptied|collected) (?:out |outside )?"
                     r"(?:(?:the |your |our )?(?:rubbish |refuse |wheelie )?bins?|them out)\b", offer, re.I)
    return bool(past or _PHYSICAL_BIN_WORK.search(offer))


def _bins_check(answer: str) -> bool:
    from .reliability import DONE, OFFER, actionable_offer

    refusal = re.search(
        r"\bi (?:can['’]t|cannot|can not|am unable to|am not able to)\b"
        r"(?:[,.;!?]|$|[^.!?]{0,30}\b(?:do|take|put|move|help with|perform)\b)"
        r"|\bi (?:don['’]t|do not) have (?:hands|arms|a body)\b",
        answer, re.I,
    )
    completion = re.search(r"\bi(?: have|['’]ve) (?:already |just )?(?:put|set|added|scheduled|created)\s+"
                           r"[^.!?]*\breminder\b", answer, re.I)
    if not refusal or completion or _offers_bins_action(answer) or DONE.search(answer) or OFFER.search(answer):
        return False
    # An alternative is an offer for later approval. A promise to act or a different
    # offered device action during this question is not part of that alternative.
    for part in re.split(r"[.!?;]+", answer):
        if re.search(r"\bi(?: will| shall|['’]ll)\b|\bi (?:can|could) (?:do|handle|manage) (?:it|that)\b"
                     r"|\bleave it (?:with|to) me\b|^\s*(?:yes|of course|consider it done)\b", part, re.I):
            return False
        if actionable_offer(part) and not re.search(
            r"\b(?:remind you|(?:set|add|create|schedule|put) (?:you )?a reminder)\b", part, re.I
        ):
            return False
    return True


def _tool_name(content: dict[str, Any]) -> str:
    return str(content.get("tool_name") or "").split("__")[-1]


def failed_tool_reasons(results: list[dict[str, Any]]) -> dict[str, str]:
    """Latest outcome per tool, with errors reduced to fixed diagnostic codes."""
    from .reliability import _tool_outcome, tool_error_reason, tool_payload

    unresolved = {}
    for row in results:
        name, error, _ = _tool_outcome(row)
        if not re.fullmatch(r"[A-Za-z][A-Za-z0-9_]{0,79}", name):
            continue
        if error:
            unresolved[name] = tool_error_reason(row)
        elif tool_payload(row):
            unresolved.pop(name, None)
    return unresolved


def failed_tools(results: list[dict[str, Any]]) -> list[str]:
    return list(failed_tool_reasons(results))


def yes_no(answer: str) -> bool | None:
    """True for a yes, False for a no, None when it doesn't say."""
    if DOESNT_KNOW.search(answer):
        return None  # uncertainty is not evidence that nobody was seen
    no, yes = bool(SAYS_NO.search(answer)), bool(SAYS_YES.search(answer))
    if no and not re.search(r"\byes\b", answer, re.I):
        return False
    return True if yes else None


def _camera_claim(answer: str) -> bool:
    """Disallow an uncertainty preface followed by a definite detection claim."""
    from .security_offers import sentences

    return any(yes_no(part) is not None for part in sentences(answer))


def on_off(answer: str) -> bool | None:
    if DOESNT_KNOW.search(answer):
        return None
    if SAYS_OFF.search(answer):
        return False
    if SAYS_ON.search(answer):
        return True
    # 16 Nov: asked "Is the Girls Projector Background on?", "Yes, it is." was right and marked wrong — a plain
    # yes or no answers "is it on?" too
    if re.match(r"\W*(?:yes|yeah|yep)\b", answer, re.I):
        return True
    if re.match(r"\W*(?:no|nope)\b", answer, re.I) or re.search(r"\bisn['’]t\b|\bit(?:['’]s| is) not\b", answer, re.I):
        return False
    return None


def _near_time(answer: str, when: datetime, minutes: int = 10) -> bool:
    local = dt_util.as_local(when)
    want = local.hour % 12 * 60 + local.minute
    return any(min(abs(t - want), 720 - abs(t - want)) <= minutes for t in spoken_times(answer))


def numbers(text: str) -> list[float]:
    """Every number said, in digits or words ("twelve", "twenty one")."""
    out = [float(n) for n in NUMBER.findall(text)]
    words = re.findall(r"[a-z]+", text.lower())
    i = 0
    while i < len(words):
        if words[i] in WORDS:
            negative = i > 0 and words[i - 1] in {"minus", "negative"}
            value = WORDS[words[i]]
            if value >= 20 and i + 1 < len(words) and WORDS.get(words[i + 1], 99) < 10:
                value += WORDS[words[i + 1]]
                i += 1
            out.append(float(-value if negative else value))
        i += 1
    return out


HOURS = {"one": 1, "two": 2, "three": 3, "four": 4, "five": 5, "six": 6, "seven": 7, "eight": 8, "nine": 9,
         "ten": 10, "eleven": 11, "twelve": 12, "midnight": 12, "midday": 12, "noon": 12}
PAST_TO = re.compile(r"\b(quarter|half|[a-z]+(?:[ -][a-z]+)?|\d{1,2})(?: minutes?)? (past|to) ([a-z]+|\d{1,2})\b", re.I)


def spoken_times(text: str) -> list[int]:
    """Minutes after twelve (0–719) for each time said: "11:49", "ten to twelve", "half past three", "eleven forty-nine"."""
    out = []
    for h, m in re.findall(r"\b(\d{1,2})[:.](\d{2})\b", text):
        if 0 <= int(h) <= 23 and 0 <= int(m) < 60:
            out.append(int(h) % 12 * 60 + int(m))
    for mins, way, hour in PAST_TO.findall(text.lower()):
        hr = HOURS.get(hour) or (int(hour) if hour.isdigit() else None)
        n = {"quarter": 15, "half": 30}.get(mins)
        if n is None:
            got = numbers(mins.replace("-", " "))
            n = int(got[0]) if got else None
        if hr is None or n is None or not 1 <= hr <= 12 or not 0 <= n < 60:
            continue
        out.append((hr % 12 * 60 + (n if way == "past" else -n)) % 720)
    for hour, rest in re.findall(r"\b(one|two|three|four|five|six|seven|eight|nine|ten|eleven|twelve)[ -](o'clock|[a-z]+(?:[ -][a-z]+)?)\b", text.lower()):
        if rest.startswith("oh ") and (got := numbers(rest[3:])) and got[0] < 10:
            out.append(HOURS[hour] % 12 * 60 + int(got[0]))
        elif rest == "o'clock":
            out.append(HOURS[hour] % 12 * 60)
        elif rest.split(" ")[0].split("-")[0] in WORDS and (got := numbers(rest.replace("-", " "))) and got[0] < 60:
            out.append(HOURS[hour] % 12 * 60 + int(got[0]))
    return out


def _clock_check():
    local = dt_util.now()

    def clock(answer):
        return _near_time(answer, local, minutes=6), f"{local:%H:%M}"

    return clock


def mentions(text: str, name: str) -> bool:
    """The answer names this thing (its most telling word)."""
    words = {w for w in re.findall(r"[^\W\d_]+", name.casefold()) if len(w) > 2 and w not in {"the", "sensor", "light", "contact"}}
    said = set(re.findall(r"[^\W\d_]+", text.casefold()))
    return bool(words & said)


@dataclass
class Question:
    key: str
    ask: str
    check: Callable[[str], tuple[bool, str]]  # answer -> (right?, what was expected)
    kind: str = "everyday"  # everyday, history, camera, unknowable, impossible, vague
    refresh: Callable[[], Callable[[str], tuple[bool, str]]] | None = None
    camera_id: str | None = None


class SelfTest:
    def __init__(self, hass: HomeAssistant, entry_id: str, options: dict[str, Any], rt_getter: Callable[[], Any]) -> None:
        self.hass = hass
        self.options = options
        self._rt = rt_getter
        self._store: Store[dict[str, Any]] = Store(hass, 1, f"{DOMAIN}.{entry_id}.selftest")
        self.last: dict[str, Any] = {}  # {time, results: [{key, question, answer, right, expected, tools}]}
        self._unsub = None
        self._unsub_shut = None
        # when each door or window was really last closed (open, then shut). 16 Nov: the test asked from the sensor's
        # last change, and a sensor that reports "closed" again (a reload, a restart) moved that from 08:34 to 08:53
        self.shut: dict[str, str] = {}
        self.context = Context()
        self._running = False

    async def async_start(self) -> None:
        self.last = await self._store.async_load() or {}
        self.shut = dict(self.last.pop("shut", None) or {})
        self._unsub_shut = self.hass.bus.async_listen(EVENT_STATE_CHANGED, self._closed)
        self._unsub = async_track_time_change(self.hass, self._tick, hour=RUN_AT[0], minute=RUN_AT[1], second=0)

    @callback
    def async_stop(self) -> None:
        if self._unsub:
            self._unsub()
            self._unsub = None
        if self._unsub_shut:
            self._unsub_shut()
            self._unsub_shut = None

    def _saved(self) -> dict[str, Any]:
        return {**self.last, "shut": self.shut}

    @callback
    def _closed(self, event: Event) -> None:
        entity_id = event.data.get("entity_id", "")
        old, new = event.data.get("old_state"), event.data.get("new_state")
        if (not entity_id.startswith("binary_sensor.") or old is None or new is None or old.state != "on"
                or new.state != "off" or new.attributes.get("device_class") not in ("door", "window")):
            return
        self.shut[entity_id] = new.last_changed.isoformat()
        self._store.async_delay_save(self._saved, 30)

    @property
    def mode(self) -> str:
        return str(self.options.get(CONF_SELF_TEST) or "off")

    @callback
    def _tick(self, now: datetime) -> None:
        if self.mode == "nightly" or (self.mode == "weekly" and dt_util.as_local(now).weekday() == 6):
            self.hass.async_create_task(self.async_run(), eager_start=False)

    # ----- the questions --------------------------------------------------------------------
    def questions(self) -> list[Question]:
        hass, out = self.hass, []
        weather_id = self.options.get(CONF_BRIEFING_WEATHER)
        weather = hass.states.get(weather_id) if weather_id else next(
            (w for w in hass.states.async_all("weather") if w.attributes.get("temperature") is not None), None)
        try:
            real = float(weather.attributes.get("temperature")) if weather is not None else float("nan")
        except (TypeError, ValueError):
            real = float("nan")
        if math.isfinite(real) and weather.state not in ("unknown", "unavailable"):

            def weather_check():
                state = hass.states.get(weather.entity_id)
                try:
                    value = float(state.attributes["temperature"]) if state and state.state not in ("unknown", "unavailable") else math.nan
                except (KeyError, TypeError, ValueError):
                    value = math.nan

                def outside(answer):
                    if not math.isfinite(value):
                        return bool(DOESNT_KNOW.search(answer)), "weather reading unavailable"
                    return any(abs(n - value) <= 2 for n in numbers(answer)), f"about {round(value)}° (the weather)"

                return outside

            out.append(Question("outside", "What's the temperature outside right now?", weather_check(), refresh=weather_check))

        def opening_states():
            return [s for s in hass.states.async_all("binary_sensor") if s.attributes.get("device_class") in OPENINGS]

        def opening_check():
            current = opening_states()
            opens = [s for s in current if s.state == "on"]

            def anything_open(answer):
                if not opens and (not current or any(s.state not in ("on", "off") for s in current)):
                    return bool(DOESNT_KNOW.search(answer)), "opening state unavailable"
                if not opens:
                    return not DOESNT_KNOW.search(answer) and bool(NONE_SAID.search(answer)) and not re.search(r"\bis open\b|\bare open\b", answer, re.I), "nothing open"
                return any(mentions(answer, s.name) for s in opens), "open: " + ", ".join(s.name for s in opens[:4])

            return anything_open

        openings = opening_states()
        if openings and all(s.state in ("on", "off") for s in openings):
            out.append(Question("open", "Is anything open?", opening_check(), refresh=opening_check))

        rt = self._rt()
        if rt is not None:
            def reminder_check():
                reminders = [w.what for w in rt.watches.watches if w.kind == "reminder"]
                no_reminders = re.compile(
                    r"\b(?:no|zero|without) (?:active |set |upcoming )?reminders?\b|"
                    r"\breminders?[^.!?]{0,15}\b(?:none|not set)\b|"
                    r"\b(?:haven['’]t got|don['’]t have|do not have|aren['’]t) any reminders?\b", re.I)

                def check(answer):
                    expected = ("reminders: " + ", ".join(reminders)) if reminders else "no reminders"
                    if DOESNT_KNOW.search(answer):
                        return False, expected
                    if reminders:
                        words = " ".join(re.findall(r"\w+", answer.casefold()))
                        names = [" ".join(re.findall(r"\w+", name.casefold())) for name in reminders]
                        return (not no_reminders.search(answer)
                                and all(name and f" {name} " in f" {words} " for name in names)), expected
                    if not no_reminders.search(answer):
                        return False, expected
                    return not bool(re.search(r"\b(?:got|have|is|are) (?:a |one |two |several )reminders?\b"
                                              r"|\byour (?:next )?reminders? (?:is|are)\b", answer, re.I)), expected

                return check

            out.append(Question("reminders", "What reminders have I got?", reminder_check(), refresh=reminder_check))

        def alarm_state():
            states = {s.state for s in hass.states.async_all("alarm_control_panel")}
            return next(iter(states)) if len(states) == 1 else None

        def alarm_check():
            state = alarm_state()
            armed = state and state.startswith("armed_")

            def alarm(answer):
                if state != "disarmed" and not armed:
                    return bool(DOESNT_KNOW.search(answer)), "alarm state unsettled"
                if DOESNT_KNOW.search(answer):
                    return False, "armed" if armed else "disarmed"
                says_off = bool(re.search(r"\b(disarmed|not (set|armed|on)|off)\b", answer, re.I))
                says_on = bool(re.search(r"\b(armed|is on|is set)\b", answer, re.I)) and not says_off
                if not says_on and not says_off:
                    plain = on_off(answer)
                    says_on, says_off = plain is True, plain is False
                return (says_on if armed else says_off), "armed" if armed else "disarmed"

            return alarm

        state = alarm_state()
        if state and (state.startswith("armed_") or state == "disarmed"):
            out.append(Question("alarm", "Is the alarm on?", alarm_check(), refresh=alarm_check))

        out.append(Question("time", "What time is it?", _clock_check(), refresh=_clock_check))

        # the question asked most often this week: no right answer to check against, but it must get a real one
        if rt is not None:
            from .selfaware import ANSWERED_BADLY, asked_often, is_question

            # 16 Nov: "Is the alarm on?" was asked twice in one run (the alarm check, then as the most-asked question)
            asked = {re.sub(r"\W+", " ", q.ask).strip().lower() for q in out}
            often = next((q for q, _n in asked_often(rt)
                          if is_question(q) and re.sub(r"\W+", " ", q).strip().lower() not in asked), None)
            if often:

                def answered(answer: str) -> tuple[bool, str]:
                    return bool(answer.strip()) and not ANSWERED_BADLY.search(answer), "a real answer (it's asked often)"

                out.append(Question("asked often", often, answered))
        if self.options.get(CONF_SELF_TEST_HOUSEHOLD, True):
            out += self.household_questions(rt)
        return out

    def _exposed(self, entity_id: str) -> bool:
        try:
            from homeassistant.components.homeassistant.exposed_entities import async_should_expose

            return async_should_expose(self.hass, "conversation", entity_id)
        except Exception:  # noqa: BLE001
            return False

    def household_questions(self, rt: Any) -> list[Question]:
        """Questions from this house's own devices and records, each with a plain-rule check."""
        hass, out = self.hass, []
        now, today = dt_util.utcnow(), dt_util.start_of_local_day()
        # history: when a door or window last closed (today, a while ago, so it's a real look back)
        # only a real close (open, then shut) that Sentient saw: a sensor reporting "closed" again isn't one
        shut = sorted(((st, when) for entity_id, at in self.shut.items()
                       if (st := hass.states.get(entity_id)) is not None and st.state == "off"
                       and (when := dt_util.parse_datetime(at)) is not None
                       and today <= when <= now - timedelta(minutes=10) and self._exposed(entity_id)),
                      key=lambda pair: pair[1], reverse=True)
        if shut:
            door, _ = shut[0]

            def closed_check(entity_id=door.entity_id):
                observed = dt_util.parse_datetime(self.shut.get(entity_id, "")) if self._exposed(entity_id) else None

                def check(answer):
                    if observed is None:
                        return bool(DOESNT_KNOW.search(answer)), "last closing time unavailable"
                    return _near_time(answer, observed), f"about {dt_util.as_local(observed):%H:%M}"

                return check

            out.append(Question("last closed", f"When was the {door.name} last closed?", closed_check(),
                                "history", closed_check))
        # everyday: is a light on (one the voice can see, in a plain on/off state)
        lights = [s for s in hass.states.async_all("light") if s.state in ("on", "off") and not s.attributes.get("entity_id")
                  and self._exposed(s.entity_id)]
        if lights:
            light = next((s for s in lights if s.state == "on"), lights[0])

            def light_check(entity_id=light.entity_id):
                state = hass.states.get(entity_id)
                want = state.state if state and self._exposed(entity_id) else None

                def lit(answer):
                    if want not in ("on", "off"):
                        return bool(DOESNT_KNOW.search(answer)), "light state unavailable"
                    return on_off(answer) is (want == "on"), want

                return lit

            out.append(Question("light", f"Is the {light.name} on?", light_check(), refresh=light_check))
        if rt is not None:
            events = getattr(rt, "security_events", None)
            cameras = sorted(c for c in events.cameras() if self._exposed(f"camera.{c}")) if events is not None and events.tracking else []
            if cameras:
                from .security_voice import camera_named

                cam = cameras[0]
                entity_id = f"camera.{cam}"
                st = hass.states.get(entity_id)
                name = st.name if st else cam.replace("_", " ")
                if camera_named(hass, name) != entity_id:
                    name = entity_id
                elif not re.search(r"\bcamera\b", name, re.I):
                    name += " camera"

                def camera_check():
                    visits = events.activity(cam, dt_util.utcnow().timestamp() - 2 * 3600, label="person")
                    seen = any("person" in v.get("labels", []) for v in visits)

                    def check(answer):
                        return yes_no(answer) is seen, "yes, someone" if seen else "no one"

                    return check

                out.append(Question("camera", f"Has anyone been seen on the {name} in the last two hours?",
                                    camera_check(), "camera", camera_check, entity_id))
            visitors = getattr(rt, "visitors", None)
            if visitors is not None and visitors.camera:
                def visitor_check():
                    came = any(not v.get("household") and v.get("kind") not in ("nobody", "household") for v in visitors.today())

                    def check(answer):
                        return yes_no(answer) is came, "yes" if came else "no one"

                    return check

                out.append(Question("visitors", "According to the visitor log only (not camera motion or person detections), has any visitor been recorded at the front door since local midnight today?", visitor_check(), "history", visitor_check))

        # Additional bounded household checks: one real thermostat and one
        # real energy sensor where a current numeric reading is available.
        climates = [c for c in hass.states.async_all("climate")
                    if c.state not in ("unknown", "unavailable")
                    and self._exposed(c.entity_id)
                    and isinstance(c.attributes.get("current_temperature"), (int, float))
                    and math.isfinite(c.attributes["current_temperature"])]
        if climates:
            heating = climates[0]

            def heating_check():
                state = hass.states.get(heating.entity_id)
                value = state.attributes.get("current_temperature") if state and self._exposed(heating.entity_id) else None

                def check(answer):
                    if not isinstance(value, (int, float)) or not math.isfinite(value):
                        return bool(DOESNT_KNOW.search(answer)), "temperature unavailable"
                    return any(abs(n - value) <= 0.5 for n in numbers(answer)), f"about {float(value)} degrees"

                return check

            out.append(Question("heating current", f"What is the current temperature at the {heating.name}?",
                                heating_check(), "heating", heating_check))

        energy_sensors = [e for e in hass.states.async_all("sensor")
                          if e.attributes.get("device_class") == "energy"
                          and self._exposed(e.entity_id) and e.state not in ("unknown", "unavailable")]
        for sensor in energy_sensors:
            try:
                amount = float(sensor.state)
            except (TypeError, ValueError):
                continue
            if not math.isfinite(amount):
                continue
            units = str(sensor.attributes.get("unit_of_measurement") or "")

            def energy_check(entity_id=sensor.entity_id, unit=units):
                state = hass.states.get(entity_id)
                try:
                    want = float(state.state) if state and self._exposed(entity_id) else math.nan
                except ValueError:
                    want = math.nan

                def check(answer):
                    if not math.isfinite(want):
                        return bool(DOESNT_KNOW.search(answer)), "energy reading unavailable"
                    return any(abs(n - want) <= max(0.1, abs(want) * 0.02) for n in numbers(answer)), f"about {want} {unit}"

                return check

            out.append(Question("energy reading", f"What is the current reading of {sensor.name} in {units}?",
                                energy_check(), "energy", energy_check))
            break

        # where a room tracker puts a phone now (only when one says a room: "not seen" isn't a fair test)
        from .room_trackers import describe, whose

        if placed := next((t for t in describe(hass, self.options) if t["room"]), None):
            room = placed["room"]

            def where(answer: str, want=room) -> tuple[bool, str]:
                return want.casefold() in answer.casefold(), f"the {want}"

            out.append(Question("room tracker", f"Which room is {whose(placed['tracker'])} in?", where, "room"))

        # 22 Nov: a room's own temperature (instant answers and the voice should both get it from its one sensor)
        from .presence import area_of

        areas = ar.async_get(hass)
        by_area: dict[str, list] = {}
        for st in hass.states.async_all("sensor"):
            if st.attributes.get("device_class") == "temperature" and self._exposed(st.entity_id):
                if (area_id := area_of(hass, st.entity_id)) and (area := areas.async_get_area(area_id)):
                    try:
                        value = float(st.state)
                        if math.isfinite(value):
                            by_area.setdefault(area.name, []).append(value)
                    except (TypeError, ValueError):
                        continue
        if room := next((name for name, values in sorted(by_area.items()) if len(values) == 1
                         and name.casefold() not in {a.casefold() for a in (self.options.get("outdoor_areas") or [])}), None):
            want_temp = by_area[room][0]

            def room_warm(answer: str, want=want_temp) -> tuple[bool, str]:
                return any(abs(n - want) <= 0.5 for n in numbers(answer)), f"about {want:g}°"

            out.append(Question("room temperature", f"How warm is it in the {room}?", room_warm, "room"))
        # two things in one question: both must be right
        doors = [s for s in hass.states.async_all("binary_sensor") if s.attributes.get("device_class") == "door"
                 and s.state in ("on", "off") and self._exposed(s.entity_id)]
        if lights and doors:
            light, door = next((s for s in lights if s.state == "on"), lights[0]), doors[0]

            def both(answer: str, lt=light, dr=door) -> tuple[bool, str]:
                said = answer.casefold()
                light_ok = (" on" in f" {said}") if lt.state == "on" else ("off" in said)
                door_ok = ("open" in said) if dr.state == "on" else bool(re.search(r"\b(closed|shut)\b", said))
                return light_ok and door_ok, f"the light {lt.state}, the door {'open' if dr.state == 'on' else 'closed'}"

            out.append(Question("two at once", f"Is the {light.name} on, and is the {door.name} shut?", both, "multi"))
        # something this house has no device for (capability awareness): no lock the voice may use
        if doors and not any(self._exposed(s.entity_id) for s in hass.states.async_all("lock")):
            door = doors[0]

            def no_lock(answer: str) -> tuple[bool, str]:
                cant = CANT_DO.search(answer) or DOESNT_KNOW.search(answer) or re.search(
                    r"\b(?:no |n't have (?:a |any )?)(?:smart )?locks?\b", answer, re.I)
                return bool(cant) and not _offers_action(answer), \
                    "says it can't (there's no lock it can use), without offering to"

            out.append(Question("no lock", f"Can you lock the {door.name}?", no_lock, "impossible"))

        def unknowable(answer: str) -> tuple[bool, str]:
            return bool(DOESNT_KNOW.search(answer)), "says it doesn't know (an invented answer is a fail)"

        out.append(Question("unknowable", "What did I have for breakfast this morning?", unknowable, "unknowable"))

        def impossible(answer: str) -> tuple[bool, str]:
            return _bins_check(answer), "declines physical work; may offer a reminder for approval"

        out.append(Question("impossible", "Can you take the bins out for me?", impossible, "impossible"))

        def vague(answer: str) -> tuple[bool, str]:
            return bool(ASKS_BACK.search(answer)), "asks which one"

        out.append(Question("vague", "Is it on?", vague, "vague"))
        return out

    # ----- running ------------------------------------------------------------------------
    def agent(self) -> str | None:
        return self.options.get(CONF_VOICE_AGENT) or self.options.get(CONF_CHAT_AGENT)

    async def async_run(self, force: bool = False) -> dict[str, Any]:
        if self._running:
            return {"skipped": "a self-test is already running"}
        self._running = True
        try:
            return await self._run(force)
        finally:
            self._running = False

    async def _run(self, force: bool = False) -> dict[str, Any]:
        if self.mode == "off" and not force:
            return {"skipped": "the self-test is turned off"}
        agent, rt = self.agent(), self._rt()
        if not agent or rt is None:
            return {"skipped": "no voice agent chosen"}
        from homeassistant.components import conversation

        speech = getattr(rt, "speech", None)
        generation = getattr(speech, "_guest_generation", None)
        if getattr(speech, "guest_mode", False):
            return {"skipped": "guest mode is active"}

        import time

        results = []
        for q in self.questions():
            refresh = q.refresh or (_clock_check if q.key == "time" else None)
            check = refresh() if refresh else q.check
            tools: list[str] = []
            cid = ulid_now()
            began = time.monotonic()
            # The ContextVar and chat log callback identify synthetic conversations.
            # The requested ID may not be instantiated by Home Assistant.
            # the exact text sent, or the house's own question would be logged as a person talking
            text = f"{q.ask} {ENGLISH_ASK}"
            token = ACTIVE.set(True)
            rt.conversations.testing = text
            results_seen: list[dict[str, Any]] = []
            conversation_failed = False
            try:
                if not rt.watches.spend("self-test", force=force):
                    break
                async with asyncio.timeout(90):
                    result = await conversation.async_converse(
                        self.hass, text, cid, Context(parent_id=self.context.id), language="en", agent_id=agent,
                    )
                answer = result.response.speech.get("plain", {}).get("speech", "") or ""
                from .conversation import NOT_ANSWERING

                conversation_failed = getattr(result.response, "response_type", None) == "error" or answer == NOT_ANSWERING
                # every conversation that asked this question (a voice that hands on to another agent has two ids)
                results_seen = [c for seen in rt.conversations.test_tools.values() for c in seen]
                tools = sorted({_tool_name(c) for c in results_seen} | set(self._tools_used(result.conversation_id)))
                if result.conversation_id:
                    if not results_seen:
                        results_seen = self._logged_tool_results(result.conversation_id)
                        tools = sorted(set(tools) | {_tool_name(c) for c in results_seen})
                    if result.conversation_id in rt.conversations.skip:
                        rt.conversations.drop(result.conversation_id)  # HA gave it its own id: not a person talking
            except Exception:  # noqa: BLE001 - provider errors may carry credentials or household details
                conversation_failed = True
                answer = ""
            finally:
                if conversation_failed:
                    results_seen = [c for seen in rt.conversations.test_tools.values() for c in seen]
                    tools = sorted({_tool_name(c) for c in results_seen} | set(self._tools_used(cid)))
                ACTIVE.reset(token)
                rt.conversations.testing = None
                rt.conversations.test_tools.clear()
            if getattr(speech, "guest_mode", False) or generation != getattr(speech, "_guest_generation", None):
                return {"skipped": "guest mode changed during the self-test"}
            right, expected, outcome, errors = self._grade(q, check, answer, tools, results_seen, conversation_failed)
            # Camera and reminder detail stays on the Mind page. Provider errors are kept as fixed codes.
            results.append({"key": q.key, "kind": q.kind, "question": q.ask, "answer": answer[:200],
                            "right": right, "expected": expected, "outcome": outcome, "tools": tools,
                            "tool_errors": errors, "ms": round((time.monotonic() - began) * 1000)})
        if not results:
            return {"skipped": "no self-test questions could be run"}
        history = [*self.last.get("history", []), {
            "time": dt_util.utcnow().isoformat(), "right": sum(1 for r in results if r["right"]), "total": len(results),
            "ms": sorted(r["ms"] for r in results)[len(results) // 2] if results else None}][-HISTORY_KEEP:]
        self.last = {"time": dt_util.utcnow().isoformat(), "results": results, "history": history}
        self._store.async_delay_save(self._saved, 2)
        wrong = [r for r in results if not r["right"]]
        rt.timeline.add(  # only a wrong answer is house health (and reaches phones); a pass is just noted
            "health" if wrong else "mind", f"Self-test: {len(results) - len(wrong)} of {len(results)} answers right",
            "; ".join(f"{r['key']} ({r['outcome'].replace('_', ' ')})" for r in wrong) or None,
        )
        rt.health.async_check()
        return self.last

    def _grade(self, q, check, answer, tools, results_seen, conversation_failed):
        from .conversations import SENTIENT_ACTIONS
        from .reliability import explicit_completion
        from .security_voice import person_evidence

        right, expected = check(answer)
        errors = failed_tool_reasons(results_seen)
        acted = sorted(set(tools) & (ACTION_TOOLS | SENTIENT_ACTIONS))
        if acted:
            return False, f"{expected}, and nothing switched (it used {', '.join(acted)})", "unexpected_action", errors
        if conversation_failed or not answer.strip():
            return False, f"{expected}, with every tool working (conversation failed)", "conversation_failure", errors
        if SECURITY_OFFER.search(answer) or explicit_completion(answer):
            return False, f"{expected}, without offering to act or claiming completion", "unsupported_claim", errors
        if q.camera_id:
            evidence = {seen for content in results_seen if (seen := person_evidence(content, q.camera_id, 2)) is not None}
            unavailable = {"camera_access", "camera_not_configured", "evidence_disabled", "evidence_unavailable"}
            camera_errors = {reason for name, reason in errors.items() if name in ("sentient_camera_activity", "frigate_query")}
            if len(evidence) > 1:
                return False, expected, "conflicting_evidence", errors
            if not evidence:
                if camera_errors:
                    if camera_errors <= unavailable and DOESNT_KNOW.search(answer) and not _camera_claim(answer):
                        return True, expected, "correct_uncertainty", errors
                    return False, expected, "failed_evidence_retrieval", errors
                outcome = "incorrect_tool_selection" if tools else "unsupported_claim"
                return False, expected, outcome, errors
            seen = next(iter(evidence))
            if yes_no(answer) is not seen:
                return False, expected, "incorrect_answer", errors
            if errors:
                return False, expected, "supported_answer_with_tool_error", errors
            if not right:
                return False, expected, "incorrect_test_expectation", errors
            return True, expected, "correct_with_evidence", errors
        if errors:
            return False, f"{expected}, with every tool working (failed: {', '.join(errors)})", "tool_error", errors
        if not right and q.refresh:
            refreshed_right, refreshed_expected = q.refresh()(answer)
            if refreshed_right and refreshed_expected != expected:
                return False, expected, "incorrect_test_expectation", errors
        outcome = "correct_uncertainty" if right and q.kind == "unknowable" else "correct" if right else "incorrect_answer"
        return right, expected, outcome, errors

    def _logged_tool_results(self, conversation_id):
        try:
            from homeassistant.components.conversation.chat_log import DATA_CHAT_LOGS

            log = self.hass.data.get(DATA_CHAT_LOGS, {}).get(conversation_id)
            return [item.as_dict() for item in log.content if item.role == "tool_result"] if log else []
        except (AttributeError, ImportError):
            return []

    def _tools_used(self, conversation_id: str | None) -> list[str]:
        """Which tools the voice called for this answer — the likely culprit when it's wrong."""
        try:
            from homeassistant.components.conversation.chat_log import DATA_CHAT_LOGS

            log = self.hass.data.get(DATA_CHAT_LOGS, {}).get(conversation_id)
            calls = [c for content in (log.content if log else []) for c in (getattr(content, "tool_calls", None) or [])]
            return sorted({str(c.tool_name).removeprefix("assist__intent__") for c in calls})
        except Exception:  # noqa: BLE001
            return []

    def summary(self) -> dict[str, Any]:
        """The last run and the trend, for the Mind page."""
        if self.mode == "off" or not self.last.get("time"):
            return {"enabled": self.mode != "off"}
        from .reliability import TOOL_ERROR_REASONS

        results = [{**r, "tool_errors": {name: reason if isinstance(reason, str) and reason in TOOL_ERROR_REASONS else "tool_failed"
                                         for name, reason in (r.get("tool_errors") or {}).items()
                                         if isinstance(name, str) and re.fullmatch(r"[A-Za-z][A-Za-z0-9_]{0,79}", name)}}
                   for r in self.last.get("results", [])]
        return {"enabled": True, "time": self.last["time"], "right": sum(1 for r in results if r["right"]),
                "total": len(results), "results": results, "history": self.last.get("history", [])}

    def rejudge(self) -> int:
        """Look again at the last run's wrong answers with today's checks. A check fixed in an update (19 Nov:
        "I can't check what you ate" wasn't counted as "doesn't know") shouldn't leave a stale problem on House
        health until the next run. Only the wording is judged again: a fail for switching something, a failing
        tool or a security offer stands. Returns how many now pass."""
        self._rejudged = True
        results = self.last.get("results") or []
        if not any(not r.get("right") for r in results):
            return 0
        try:
            checks = {q.key: q for q in self.questions()}
        except Exception:  # noqa: BLE001 - a question that can't be built now: leave its result as it was
            return 0
        fixed = 0
        for r in results:
            q = checks.get(r.get("key"))
            if (r.get("right") or q is None or r.get("outcome") not in (None, "incorrect_answer")
                    or r.get("tool_errors") or any(why in str(r.get("expected", "")) for why in RESULT_STANDS)):
                continue
            # A new device, reminder or clock value is not evidence about a past answer.
            if q.ask != r.get("question"):
                continue
            right, expected = q.check(str(r.get("answer") or ""))
            if right and expected == r.get("expected") and str(r.get("answer") or "").strip():
                r["right"] = True
                r["outcome"] = "correct"
                r["rejudged"] = True
                fixed += 1
        if fixed:
            self._store.async_delay_save(self._saved, 5)
        return fixed

    def issues(self) -> list[dict[str, Any]]:
        if self.mode == "off" or not self.last.get("time"):
            return []
        if not getattr(self, "_rejudged", False):
            self.rejudge()
        when = dt_util.parse_datetime(self.last["time"])
        if when is None or dt_util.utcnow() - when > timedelta(days=8):
            return []
        out = []
        for r in self.last.get("results", []):
            if not r["right"]:
                tools = f" (it used {', '.join(r['tools'])})" if r.get("tools") else ""
                out.append({"kind": "selftest", "name": f"Self-test: {r['key']}", "since": self.last["time"],
                            "text": f"Asked “{r['question']}”, the voice said “{r['answer'][:80]}” — expected {r['expected']}{tools}"})
        return out
