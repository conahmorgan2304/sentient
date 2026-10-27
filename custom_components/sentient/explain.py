"""Why did that happen? Who or what last changed something, and what the house logged around then.

Home Assistant records a context with every change: a user (someone in the app or on a dashboard),
a parent (an automation, a script or Sentient itself doing it), or neither (the device itself — a
wall switch, a remote, its own timer). Sentient adds its own timeline, which says why it acted
(auto-off after 20 minutes with no movement, a light show, an alarm…).
"""

from __future__ import annotations

from datetime import timedelta
import re
from typing import Any

from homeassistant.core import HomeAssistant
from homeassistant.util import dt as dt_util

_WORD = re.compile(r"[a-z0-9']+")
_SKIP = {"the", "a", "an", "why", "did", "go", "off", "on", "who", "turned", "turn", "what", "is", "was", "it", "you", "say",
         "that", "didn't", "didnt", "not", "tell", "told", "me", "about", "when", "announce", "mention", "had", "has", "been"}


async def async_explain(hass: HomeAssistant, rt: Any, what: str, hours: float = 12) -> dict[str, Any]:
    from .watches import resolve_entity

    out: dict[str, Any] = {}
    entity_id = what if hass.states.get(what) else resolve_entity(hass, what)
    st = hass.states.get(entity_id) if entity_id else None
    if st is not None:
        when = dt_util.as_local(st.last_changed)
        out["device"] = st.name
        out["now"] = st.state
        out["changed"] = f"{when:%H:%M}" + ("" if when.date() == dt_util.now().date() else f" on {when:%A}")
        ctx = st.context
        if ctx.user_id:
            user = await hass.auth.async_get_user(ctx.user_id)
            out["by"] = f"a person in the Home Assistant app or a dashboard ({user.name if user else 'a user'})"
        elif ctx.parent_id:
            out["by"] = "an automation, a script or the house itself"
            triggered = []
            for auto in hass.states.async_all("automation"):
                last = auto.attributes.get("last_triggered")
                last = dt_util.parse_datetime(last) if isinstance(last, str) else last
                if last and timedelta(seconds=-2) <= st.last_changed - last <= timedelta(minutes=10):
                    triggered.append(auto.name)
            if triggered:
                out["automations_that_ran_just_before"] = triggered[:3]
        else:
            out["by"] = "the device itself or its own app (a wall switch, a remote, its own timer), or a voice assistant outside Home Assistant"
    words = {w for w in _WORD.findall(((st.name if st else "") + " " + what).lower()) if w not in _SKIP and len(w) > 2}
    notes = []
    for item in rt.timeline.since(hours, None, 400):
        text = f"{item.get('text', '')} {item.get('detail', '')}".lower()
        if words and sum(1 for w in words if w in text) >= max(1, len(words) // 2):
            t = dt_util.as_local(dt_util.parse_datetime(item["time"]))
            notes.append(f"{t:%H:%M} {item['text']}" + (f" ({item['detail']})" if item.get("detail") else ""))
        if len(notes) >= 6:
            break
    if notes:
        out["house_log"] = notes
    # things it noticed and decided not to say, and why (quiet hours, the hourly limit, not worth it…)
    quiet = []
    for d in list(getattr(rt.noticer, "decisions", []))[:60]:
        text = " ".join(d.get("changes") or []).lower()
        if words and any(w in text for w in words) and not str(d.get("result", "")).startswith("spoke"):
            quiet.append(f"{str(d.get('time', ''))[11:16]} noticed {'; '.join(d.get('changes') or [])} — {d.get('result')}")
        if len(quiet) >= 4:
            break
    if quiet:
        out["noticed_but_not_said"] = quiet
    if not out:
        out["error"] = f"I can't find anything about {what}"
    return out
