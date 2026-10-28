"""Self-test: the voice asked a few everyday questions overnight, its answers checked against the house.

Quietly, in quiet hours (nightly or weekly, by the setting), the voice agent is asked things like "what's
the temperature outside?", "is anything open?", "what reminders have I got?". Each answer is checked
against the house's real state with plain rules — the right temperature, the right count, "nothing" when
there's nothing. Questions only: nothing is switched. Anything it got wrong shows in house health the next
morning ("the voice said 19° for outside; the weather says 12°"), with the tool it used, so a bad setting
somewhere else gets found. One AI call per question, counted on the AI meter. Off by default.
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime, timedelta
import logging
import re
from typing import Any, Callable

from homeassistant.core import Context, HomeAssistant, callback
from homeassistant.helpers.event import async_track_time_change
from homeassistant.helpers.storage import Store
from homeassistant.util import dt as dt_util
from homeassistant.util.ulid import ulid_now

from .const import CONF_BRIEFING_WEATHER, CONF_CHAT_AGENT, CONF_SELF_TEST, CONF_VOICE_AGENT, DOMAIN

_LOGGER = logging.getLogger(__name__)

RUN_AT = (3, 25)  # quiet hours, after the overnight think
NUMBER = re.compile(r"-?\d+(?:\.\d+)?")
WORDS = {"zero": 0, "one": 1, "two": 2, "three": 3, "four": 4, "five": 5, "six": 6, "seven": 7, "eight": 8,
         "nine": 9, "ten": 10, "eleven": 11, "twelve": 12, "thirteen": 13, "fourteen": 14, "fifteen": 15,
         "sixteen": 16, "seventeen": 17, "eighteen": 18, "nineteen": 19, "twenty": 20, "thirty": 30, "forty": 40, "fifty": 50}
NONE_SAID = re.compile(r"\b(no|none|nothing|not any|haven't|have not|aren't|isn't|all (shut|closed|off)|everything('s| is) (shut|closed|off))\b", re.I)
OPENINGS = {"door", "window", "garage_door", "opening"}


def numbers(text: str) -> list[float]:
    """Every number said, in digits or words ("twelve", "twenty one")."""
    out = [float(n) for n in NUMBER.findall(text)]
    words = re.findall(r"[a-z]+", text.lower())
    i = 0
    while i < len(words):
        if words[i] in WORDS:
            value = WORDS[words[i]]
            if value >= 20 and i + 1 < len(words) and WORDS.get(words[i + 1], 99) < 10:
                value += WORDS[words[i + 1]]
                i += 1
            out.append(float(value))
        i += 1
    return out


HOURS = {"one": 1, "two": 2, "three": 3, "four": 4, "five": 5, "six": 6, "seven": 7, "eight": 8, "nine": 9,
         "ten": 10, "eleven": 11, "twelve": 12, "midnight": 12, "midday": 12, "noon": 12}
PAST_TO = re.compile(r"\b(quarter|half|[a-z]+(?:[ -][a-z]+)?|\d{1,2})(?: minutes?)? (past|to) ([a-z]+|\d{1,2})\b", re.I)


def spoken_times(text: str) -> list[int]:
    """Minutes after twelve (0–719) for each time said: "11:49", "ten to twelve", "half past three", "eleven forty-nine"."""
    out = []
    for h, m in re.findall(r"\b(\d{1,2})[:.](\d{2})\b", text):
        out.append(int(h) % 12 * 60 + int(m))
    for mins, way, hour in PAST_TO.findall(text.lower()):
        hr = HOURS.get(hour) or (int(hour) if hour.isdigit() else None)
        n = {"quarter": 15, "half": 30}.get(mins)
        if n is None:
            got = numbers(mins.replace("-", " "))
            n = int(got[0]) if got else None
        if hr is None or n is None:
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


def mentions(text: str, name: str) -> bool:
    """The answer names this thing (its most telling word)."""
    words = [w for w in re.findall(r"[a-z]+", name.lower()) if len(w) > 2 and w not in {"the", "sensor", "light", "contact"}]
    said = text.lower()
    return bool(words) and any(w in said for w in words)


@dataclass
class Question:
    key: str
    ask: str
    check: Callable[[str], tuple[bool, str]]  # answer -> (right?, what was expected)


class SelfTest:
    def __init__(self, hass: HomeAssistant, entry_id: str, options: dict[str, Any], rt_getter: Callable[[], Any]) -> None:
        self.hass = hass
        self.options = options
        self._rt = rt_getter
        self._store: Store[dict[str, Any]] = Store(hass, 1, f"{DOMAIN}.{entry_id}.selftest")
        self.last: dict[str, Any] = {}  # {time, results: [{key, question, answer, right, expected, tools}]}
        self._unsub = None
        self.context = Context()

    async def async_start(self) -> None:
        self.last = await self._store.async_load() or {}
        self._unsub = async_track_time_change(self.hass, self._tick, hour=RUN_AT[0], minute=RUN_AT[1], second=0)

    @callback
    def async_stop(self) -> None:
        if self._unsub:
            self._unsub()
            self._unsub = None

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
        if weather is not None and weather.attributes.get("temperature") is not None:
            real = float(weather.attributes["temperature"])

            def outside(answer: str) -> tuple[bool, str]:
                said = numbers(answer)
                return any(abs(n - real) <= 2 for n in said), f"about {round(real)}° (the weather)"

            out.append(Question("outside", "What's the temperature outside right now?", outside))

        opens = [s for s in hass.states.async_all("binary_sensor")
                 if s.attributes.get("device_class") in OPENINGS and s.state == "on"]

        def anything_open(answer: str) -> tuple[bool, str]:
            if not opens:
                return bool(NONE_SAID.search(answer)) and not re.search(r"\bis open\b|\bare open\b", answer, re.I), "nothing open"
            return any(mentions(answer, s.name) for s in opens), "open: " + ", ".join(s.name for s in opens[:4])

        out.append(Question("open", "Is anything open?", anything_open))

        rt = self._rt()
        if rt is not None:
            reminders = [w for w in rt.watches.watches if w.kind == "reminder"]

            def reminders_check(answer: str) -> tuple[bool, str]:
                if not reminders:
                    return bool(NONE_SAID.search(answer)), "no reminders"
                return any(mentions(answer, w.what) for w in reminders), "reminders: " + ", ".join(w.what for w in reminders[:3])

            out.append(Question("reminders", "What reminders have I got?", reminders_check))

        panels = [s for s in hass.states.async_all("alarm_control_panel") if s.state not in ("unavailable", "unknown")]
        if panels:
            armed = panels[0].state.startswith("armed") or panels[0].state == "triggered"

            def alarm(answer: str) -> tuple[bool, str]:
                says_off = bool(re.search(r"\b(disarmed|not (set|armed|on)|off)\b", answer, re.I))
                says_on = bool(re.search(r"\b(armed|is on|is set)\b", answer, re.I)) and not re.search(r"\bdisarmed\b", answer, re.I)
                return (says_on if armed else says_off), "armed" if armed else "disarmed"

            out.append(Question("alarm", "Is the alarm on?", alarm))

        local = dt_util.now()

        def clock(answer: str) -> tuple[bool, str]:
            now = local.hour % 12 * 60 + local.minute
            near = [t for t in spoken_times(answer) if min(abs(t - now), 720 - abs(t - now)) <= 6]  # rounding to fives is fine
            return bool(near), f"{local:%H:%M}"

        out.append(Question("time", "What time is it?", clock))
        return out

    # ----- running ------------------------------------------------------------------------
    def agent(self) -> str | None:
        return self.options.get(CONF_VOICE_AGENT) or self.options.get(CONF_CHAT_AGENT)

    async def async_run(self, force: bool = False) -> dict[str, Any]:
        if self.mode == "off" and not force:
            return {"skipped": "the self-test is turned off"}
        agent, rt = self.agent(), self._rt()
        if not agent or rt is None:
            return {"skipped": "no voice agent chosen"}
        from homeassistant.components import conversation

        results = []
        for q in self.questions():
            if not rt.watches.spend("self-test", force=force):
                break
            tools: list[str] = []
            cid = ulid_now()
            rt.conversations.skip.add(cid)
            try:
                result = await conversation.async_converse(
                    self.hass, q.ask, cid, Context(parent_id=self.context.id), language="en", agent_id=agent,
                )
                answer = result.response.speech.get("plain", {}).get("speech", "") or ""
                tools = self._tools_used(result.conversation_id)
            except Exception as err:  # noqa: BLE001
                answer = f"(no answer: {err})"
            right, expected = q.check(answer)
            results.append({"key": q.key, "question": q.ask, "answer": answer[:200], "right": right,
                            "expected": expected, "tools": tools})
        self.last = {"time": dt_util.utcnow().isoformat(), "results": results}
        self._store.async_delay_save(lambda: self.last, 2)
        wrong = [r for r in results if not r["right"]]
        rt.timeline.add(
            "health", f"Self-test: {len(results) - len(wrong)} of {len(results)} answers right",
            "; ".join(f"{r['question']} — said “{r['answer'][:60]}”, expected {r['expected']}" for r in wrong) or None,
        )
        rt.health.async_check()
        return self.last

    def _tools_used(self, conversation_id: str | None) -> list[str]:
        """Which tools the voice called for this answer — the likely culprit when it's wrong."""
        try:
            from homeassistant.components.conversation.chat_log import DATA_CHAT_LOGS

            log = self.hass.data.get(DATA_CHAT_LOGS, {}).get(conversation_id)
            calls = [c for content in (log.content if log else []) for c in (getattr(content, "tool_calls", None) or [])]
            return sorted({str(c.tool_name).removeprefix("assist__intent__") for c in calls})
        except Exception:  # noqa: BLE001
            return []

    def issues(self) -> list[dict[str, Any]]:
        if self.mode == "off" or not self.last.get("time"):
            return []
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
