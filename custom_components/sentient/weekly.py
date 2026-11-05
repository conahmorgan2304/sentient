"""Weekly report: what the house learned and noticed this week.

Put together from what Sentient already keeps — no AI. Kept on the timeline (and
so sent to phones if digests are), asked for by voice ("what have you learned
this week?"), and made on Sunday evenings if the weekly report is on.
"""

from __future__ import annotations

from datetime import timedelta
from typing import Any

from homeassistant.util import dt as dt_util


def _since(stamp: str | None, cutoff) -> bool:
    when = dt_util.parse_datetime(stamp or "")
    return bool(when and when >= cutoff)


def build(rt: Any, days: int = 7) -> dict[str, Any]:
    cutoff = dt_util.utcnow() - timedelta(days=days)
    out: dict[str, Any] = {}

    learned = [r for r in rt.learner.all() if _since(r.learned, cutoff)]
    if learned:
        out["new_habits"] = [r.describe() for r in learned][:6]
    adopted = [r.describe() for r in rt.learner.all() if r.adopted]
    if adopted:
        out["house_does_for_you"] = adopted[:6]

    quiet = [f for f in rt.feedback.listing() if f["score"] >= 1]
    if quiet:
        out["learned_from_feedback"] = [f"{f['name']}: {f['effect']}" for f in quiet][:6]

    if lines := rt.heating.describe():
        out["heating"] = lines

    unusual = [e for e in rt.night_watch.events if _since(e.get("time"), cutoff)]
    if unusual:
        alerts = sum(1 for e in unusual if e.get("alert"))
        out["night_watch"] = (
            f"{len(unusual)} unusual opening{'s' if len(unusual) != 1 else ''}"
            + (f", {alerts} with nobody up" if alerts else ", all with someone up inside")
        )

    if rt.options.get("battery_forecast", True) and (batteries := rt.watchdog.battery_forecasts()):
        out["batteries_to_buy"] = [b["text"] for b in batteries[:6]]

    memories = [m.text for m in rt.memory.items if _since(m.created, cutoff)]
    if memories:
        out["new_memories"] = memories[:6]

    items = [i for i in rt.timeline.items if _since(i.get("time"), cutoff)]
    said = sum(1 for i in items if i.get("kind") == "said")
    held = sum(1 for i in items if i.get("kind") == "held")
    visitors = sum(1 for i in items if i.get("kind") == "visitor")
    out["activity"] = f"spoke {said} times, held back {held}, {visitors} visitor{'s' if visitors != 1 else ''} at the door"

    # what it got wrong, plainly — and what it would change (each waits for a yes on the Mind page)
    wrong: list[str] = []
    corrections = [m.text for m in rt.memory.items if m.category == "correction" and _since(m.created, cutoff)]
    if corrections:
        wrong.append(f"corrected {len(corrections)} time{'s' if len(corrections) != 1 else ''} (e.g. {corrections[0]})")
    missed = [i for i in items if i.get("kind") == "health" and str(i.get("text", "")).startswith("Didn't happen:")]
    if missed:
        wrong.append(f"{len(missed)} thing{'s' if len(missed) != 1 else ''} asked for by voice didn't happen")
    fell_back = [i for i in items if i.get("kind") == "health" and str(i.get("text", "")).startswith("Voice answered by the backup agent")]
    if fell_back:
        wrong.append(f"the AI didn't answer {len(fell_back)} time{'s' if len(fell_back) != 1 else ''}")
    test = getattr(getattr(rt, "selftest", None), "last", None) or {}
    if _since(test.get("time"), cutoff) and (bad := [r for r in test.get("results", []) if not r.get("right")]):
        wrong.append(f"the self-test got {len(bad)} answer{'s' if len(bad) != 1 else ''} wrong")
    if wrong:
        out["got_wrong"] = wrong
    if (proposals := getattr(rt, "proposals", None)) is not None and (waiting := proposals.listing()):
        out["would_change"] = [p["change"] for p in waiting]
    return out


def summary(report: dict[str, Any]) -> str:
    """A few plain sentences for the timeline and phones."""
    parts = []
    if habits := report.get("new_habits"):
        parts.append(f"New habits spotted: {len(habits)} (e.g. {habits[0][0].lower()}{habits[0][1:-1]}).")
    if quiet := report.get("learned_from_feedback"):
        parts.append("Keeping quieter about " + ", ".join(q.split(":")[0] for q in quiet[:3]) + ".")
    if heat := report.get("heating"):
        parts.append(heat[0])
    if nw := report.get("night_watch"):
        parts.append(f"Night watch: {nw}.")
    if bats := report.get("batteries_to_buy"):
        parts.append("Batteries running down: " + "; ".join(bats[:3]) + ".")
    if mem := report.get("new_memories"):
        parts.append(f"{len(mem)} new thing{'s' if len(mem) != 1 else ''} remembered.")
    if wrong := report.get("got_wrong"):
        parts.append("Got wrong: " + "; ".join(wrong[:3]) + ".")
    if change := report.get("would_change"):
        parts.append("I'd change: " + "; ".join(c[0].lower() + c[1:] for c in change) + " — yes or no on the Mind page.")
    parts.append(f"This week it {report.get('activity', '')}.")
    return " ".join(parts)
