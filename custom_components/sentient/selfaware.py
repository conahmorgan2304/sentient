"""Self-awareness: what Sentient can do right now, and how well its own behaviour is going down.

Everything here comes from records Sentient already keeps — the timeline (what was said, held back,
done), speaker delivery results, thumbs up/down, hushes, the AI meter and past conversations. No AI.
Every finding carries the numbers it rests on, so "have you been annoying us?" gets an answer it
can justify. A short self-check runs each night; with self-adjusting on, a kind of remark that keeps
being thumbed down or hushed is quietened through the same feedback the page's thumbs use (it fades
again over time). Nothing to do with safety, security or control is ever changed.
"""

from __future__ import annotations

from collections import Counter, defaultdict
from datetime import timedelta
import re
from typing import Any, Callable

from homeassistant.core import HomeAssistant, callback
from homeassistant.helpers.event import async_track_time_change
from homeassistant.util import dt as dt_util

from .const import CONF_SELF_ADJUST, CONF_SELF_CHECK

DAYS = 7
SPEAKER_MIN_TRIES = 3
SPEAKER_BAD = 0.3  # three in ten failing
CHATTY_MIN = 3  # remarks of one kind before it can be "too much"
REPEATED_QUESTION = 3
_WORDS = re.compile(r"[a-z0-9']+")
DOWN_FOR = timedelta(minutes=10)  # a restart or a tablet waking up isn't "unavailable"


def _down(st) -> bool:
    """Unavailable for a while, not just for the moment after a restart."""
    return st.state == "unavailable" and dt_util.utcnow() - st.last_changed >= DOWN_FOR


TRIVIAL = re.compile(r"\b(?:what(?:'s| is)? the time|what time|time is it|what(?:'s| is)? the date|what day|today's date)\b", re.I)
# remarks that are about safety or someone asking: never quietened, never counted as "chatty"
NEVER_QUIETEN = ("critical", "security", "alarm", "alexa:", "reply", "watch", "remind", "announce:")


def _kind(source: str | None) -> str:
    """'offer:Heating on' -> 'offer: Heating on', 'notice' -> 'remarks'."""
    s = str(source or "other")
    return {"notice": "remarks", "left_open": "left-open reminders"}.get(s, s.replace(":", ": ", 1))


def _key(text: str) -> str:
    return " ".join(w for w in _WORDS.findall(text.lower()) if len(w) > 2)


class SelfAware:
    def __init__(self, hass: HomeAssistant, options: dict[str, Any], rt_getter: Callable[[], Any]) -> None:
        self.hass = hass
        self.options = options
        self._rt = rt_getter
        self.last: dict[str, Any] | None = None
        self._unsub = None
        self._cache: tuple[Any, dict[str, Any]] | None = None

    @property
    def enabled(self) -> bool:
        return bool(self.options.get(CONF_SELF_CHECK, True))

    async def async_start(self) -> None:
        self._unsub = async_track_time_change(self.hass, self._nightly, hour=4, minute=45, second=0)

    @callback
    def async_stop(self) -> None:
        if self._unsub:
            self._unsub()
            self._unsub = None

    @callback
    def _nightly(self, _now) -> None:
        if not self.enabled:
            return
        report = self.check()
        rt = self._rt()
        if rt is None:
            return
        adjusted = self._adjust(report) if self.options.get(CONF_SELF_ADJUST) else []
        report["adjusted"] = adjusted
        self.last = report
        if report["findings"]:
            rt.timeline.add("mind", f"Self-check: {len(report['findings'])} thing{'s' if len(report['findings']) > 1 else ''} noticed about itself",
                            "; ".join(f["text"] for f in report["findings"][:3]))

    # ----- the check ---------------------------------------------------------------------
    def check(self, days: float = DAYS) -> dict[str, Any]:
        rt = self._rt()
        if rt is None:
            return {"findings": [], "numbers": {}}
        items = rt.timeline.since(hours=days * 24, limit=2000)
        said = [i for i in items if i["kind"] == "said"]
        held = [i for i in items if i["kind"] == "held"]
        findings: list[dict[str, Any]] = []

        # 1. which kinds of remark were said, and how they went down
        by_kind = Counter(_kind(i.get("source")) for i in said)
        hushes = [i for i in items if i["kind"] == "mode" and str(i.get("text", "")).startswith("Hushed")]
        hushed_after: Counter[str] = Counter()
        for h in hushes:
            ht = dt_util.parse_datetime(h["time"])
            before = [s for s in said if ht and timedelta(0) <= ht - dt_util.parse_datetime(s["time"]) <= timedelta(minutes=3)]
            if before:
                hushed_after[_kind(before[0].get("source"))] += 1
        thumbs_down: Counter[str] = Counter()
        fb = getattr(rt, "feedback", None)
        if fb is not None:
            for row in fb.listing():
                if "thumbs down" in " ".join(row.get("why") or []):
                    thumbs_down[row["name"]] += 1
        for kind, n in by_kind.most_common():
            if n < CHATTY_MIN or any(kind.startswith(p.rstrip(":")) for p in NEVER_QUIETEN):
                continue
            pushback = hushed_after[kind]
            if pushback >= 2 or (pushback and n >= 8):
                findings.append({
                    "kind": "chatty", "about": kind,
                    "text": f"I may be saying too many {kind}: {n} in {int(days)} days, and the house was hushed straight after {pushback} of them",
                    "evidence": {"said": n, "hushed_right_after": pushback, "days": days},
                })
        repeats = Counter(_key(i["text"]) for i in said if i.get("source") == "notice")
        same_day = Counter((_key(i["text"]), str(i.get("time") or "")[:10]) for i in said if i.get("source") == "notice")
        for text_key, n in repeats.items():
            # once a day (the kettle finishing each morning) is just the day happening; flag it only when it comes
            # round more than daily on average, or three times or more in one day
            busiest = max((c for (k, _d), c in same_day.items() if k == text_key), default=0)
            if text_key and n >= 3 and (n > max(days, 1) * 1.5 or busiest >= 3):
                example = next(i["text"] for i in said if _key(i["text"]) == text_key)
                findings.append({
                    "kind": "repeat", "about": "remarks",
                    "text": f"I said “{example}” {n} times in {int(days)} days — that may be more often than it's worth",
                    "evidence": {"times": n, "days": days},
                })

        # 2. speakers that keep failing
        tries: dict[str, list[int]] = defaultdict(lambda: [0, 0])
        for i in said:
            for d in i.get("deliveries") or []:
                tries[d["entity_id"]][0] += 1
                if d.get("status") not in ("accepted", "delivered", "spoken", "ok"):
                    tries[d["entity_id"]][1] += 1
        speakers = {}
        for eid, (n, bad) in tries.items():
            st = self.hass.states.get(eid)
            name = st.name if st else eid
            speakers[name] = {"tries": n, "failed": bad}
            if n >= SPEAKER_MIN_TRIES and bad / n >= SPEAKER_BAD:
                findings.append({
                    "kind": "speaker", "about": name,
                    "text": f"{name} isn't reliable for announcements: {bad} of the last {n} failed",
                    "evidence": {"tries": n, "failed": bad},
                })

        # 3. things held back a lot (quiet hours, hush…), and why
        held_why = Counter(str(i.get("reason") or "").split(":")[0] for i in held)
        if held and len(held) >= max(5, len(said)):
            top, n = held_why.most_common(1)[0]
            findings.append({
                "kind": "held", "about": top,
                "text": f"More was held back than said: {len(held)} held, mostly for {top.replace('_', ' ')} ({n}), against {len(said)} said",
                "evidence": {"held": len(held), "said": len(said), "top_reason": top},
            })

        # 4. the same question asked again and again — maybe it should be easier to find
        asked: Counter[str] = Counter()
        examples: dict[str, str] = {}
        conv = getattr(rt, "conversations", None)
        for ep in (conv.recent(hours=days * 24, limit=500) if conv else []):
            first = next((t["text"] for t in ep.get("turns", []) if t["who"] == "them"), "")
            if TRIVIAL.search(first):
                continue  # the time or the date: of course people ask the speaker that
            if (k := _key(first)) and len(k.split()) >= 2:
                asked[k] += 1
                examples.setdefault(k, first)
        for k, n in asked.most_common(3):
            if n >= REPEATED_QUESTION:
                findings.append({
                    "kind": "question", "about": examples[k],
                    "text": f"I've been asked “{examples[k]}” {n} times — it might be worth putting in the morning briefing or on a screen",
                    "evidence": {"times": n, "days": days},
                })

        # 5. the AI
        ai = rt.ai.describe()
        week = list((ai.get("last_7_days") or {}).values())
        if ai.get("failed_today", 0) >= 3:
            findings.append({"kind": "ai", "about": "ai", "text": f"The AI has failed {ai['failed_today']} times today, so I've used plain words more",
                             "evidence": {"failed_today": ai["failed_today"]}})
        if ai.get("limit") and week and max(week) >= ai["limit"]:
            findings.append({"kind": "ai", "about": "limit", "text": f"I ran out of AI calls on at least one day this week (limit {ai['limit']}) — the limit could be raised in Settings → Mind",
                             "evidence": {"busiest_day": max(week), "limit": ai["limit"]}})

        return {
            "time": dt_util.utcnow().isoformat(), "days": days, "findings": findings,
            "numbers": {
                "said": len(said), "held": len(held), "held_why": dict(held_why.most_common(5)),
                "said_by_kind": dict(by_kind.most_common(8)), "hushed_right_after": dict(hushed_after),
                "thumbs_down": sum(thumbs_down.values()), "speakers": speakers,
                "ai_today": ai.get("today"), "ai_failed_today": ai.get("failed_today"), "ai_usually": ai.get("average_per_day"),
            },
        }

    def _adjust(self, report: dict[str, Any]) -> list[str]:
        """Quieten remark kinds the house keeps hushing — the same way three thumbs down would. Fades by itself."""
        rt = self._rt()
        fb = getattr(rt, "feedback", None) if rt else None
        done = []
        if fb is None:
            return done
        for f in report["findings"]:
            if f["kind"] != "chatty":
                continue
            source = f["about"].replace(": ", ":", 1)
            source = {"remarks": "notice", "left-open reminders": "left_open"}.get(source, source)
            if not fb.quietenable(source):
                continue
            fb.record(f"source:{source}", 1.0, "self-check: hushed straight after too often")
            done.append(f"Quietened {f['about']} a little")
        if done:
            fb._save()
            rt.timeline.add("mind", "Adjusted itself", "; ".join(done))
        return done

    def summary(self) -> dict[str, Any]:
        """For the Mind page: status now and the self-check (kept for five minutes)."""
        now = dt_util.utcnow()
        if self._cache and now - self._cache[0] < timedelta(minutes=5):
            return self._cache[1]
        out: dict[str, Any] = {"status": self.status(), "enabled": self.enabled}
        if self.enabled:
            report = self.check()
            out["findings"] = [{"text": f["text"], "kind": f["kind"]} for f in report["findings"]]
            out["numbers"] = {k: report["numbers"][k] for k in ("said", "held", "thumbs_down")}
            out["adjusted"] = (self.last or {}).get("adjusted") or []
        self._cache = (now, out)
        return out

    # ----- what it can do right now --------------------------------------------------------
    def status(self) -> list[dict[str, str]]:
        rt = self._rt()
        if rt is None:
            return []
        out: list[dict[str, str]] = []
        add = lambda name, state, why="": out.append({"name": name, "status": state, "why": why})  # noqa: E731
        opts = rt.options
        # talking
        if rt.speech.hushed():
            add("Speaking", "limited", "hushed for now")
        elif rt.speech.quiet_now():
            add("Speaking", "limited", "quiet hours: only important things are said")
        elif rt.speech.guest_mode:
            add("Speaking", "limited", "guest mode: nothing personal")
        else:
            add("Speaking", "ok")
        bad = [n for n, s in (self.check(days=1)["numbers"]["speakers"]).items() if s["tries"] >= 2 and s["failed"] >= s["tries"] / 2]
        speakers = opts.get("speakers") or []
        gone = [st.name for e in speakers if (st := self.hass.states.get(e)) is not None and _down(st)]
        if gone or bad:
            add("Speakers", "limited", "; ".join(filter(None, [
                f"offline: {', '.join(gone)}" if gone else "", f"failing today: {', '.join(bad)}" if bad else ""])))
        else:
            add("Speakers", "ok", f"{len(speakers)} to speak on")
        # thinking
        ai = rt.ai
        if ai.failing:
            add("AI", "limited", f"not answering ({(ai.last_error or '')[:80]}) — plain words for now")
        elif ai.capped:
            add("AI", "limited", f"today's {ai.limit} calls are used up — plain words until midnight")
        else:
            add("AI", "ok", f"{ai.used} of {ai.limit} calls used today" if ai.limit else f"{ai.used} calls today")
        over = [k for k, v in ai.budgets().items() if v["used"] >= v["cap"]]
        if over:
            add("AI budgets", "limited", "used up today: " + ", ".join(over))
        # looking
        ignored = set(opts.get("health_ignore") or [])
        cams = [st for st in self.hass.states.async_all("camera") if st.entity_id not in ignored]
        dark = [st.name for st in cams if _down(st)]
        if not cams:
            add("Cameras", "off", "no cameras")
        elif ai.failing or ai.capped:
            add("Cameras", "limited", "I can see detections but can't describe pictures while the AI is limited")
        elif dark:
            add("Cameras", "limited", f"unavailable: {', '.join(dark)}")
        else:
            add("Cameras", "ok", f"{len(cams)} cameras")
        # remembering, acting
        add("Memory", "ok", f"{len(rt.memory.items)} things remembered")
        never = opts.get("never_entities") or []
        add("Acting", "ok" if not opts.get("guarded_control") else "limited",
            ("guarded: actions are checked first" if opts.get("guarded_control") else "can switch things when asked")
            + (f"; {len(never)} on the never-touch list" if never else ""))
        health = rt.health.issues
        if health:
            add("House health", "limited", "; ".join(rt.health.describe()[:4]))
        else:
            add("House health", "ok", "everything online")
        return out
