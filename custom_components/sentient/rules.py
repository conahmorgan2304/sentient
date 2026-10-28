"""Rules: "when this happens, do that" — made on the page or by saying it.

A rule is a trigger (something changing to a state), optional conditions (only after dark, only between
times, only if something else is in a state), and actions — plus, optionally, something to say. They run
inside Sentient (no automation is created) and each can be switched off or deleted on the Rules page.

By voice ("when the back door opens after dark, put the kitchen light on dim") the voice fills in the
details, Sentient reads the rule back in plain words, and it's only made after a clear yes. Rules can never
arm, disarm or unlock anything. The whole feature is off by default.
"""

from __future__ import annotations

from datetime import timedelta
import logging
from typing import Any, Callable

from homeassistant.const import EVENT_STATE_CHANGED
from homeassistant.core import Context, Event, HomeAssistant, callback
from homeassistant.util import dt as dt_util

from .const import CONF_RULES
from .items import Items
from .kinds import in_windows

_LOGGER = logging.getLogger(__name__)

ALLOWED = {"light", "switch", "fan", "scene", "input_boolean", "media_player", "climate", "cover", "script", "vacuum",
           "humidifier", "button", "input_select", "select", "number", "input_number", "notify", "tts", "sentient"}
NEVER = {"alarm_control_panel", "lock", "homeassistant", "hassio", "shell_command", "rest_command", "python_script"}
GAP = timedelta(seconds=30)  # the same rule doesn't run twice within this
STATE_WORDS = {"opens": "on", "open": "on", "opened": "on", "on": "on", "closes": "off", "closed": "off", "off": "off",
               "shut": "off", "arrives": "home", "home": "home", "leaves": "not_home", "away": "not_home"}


def state_word(text: str | None) -> str:
    t = (text or "").strip().lower()
    return STATE_WORDS.get(t, t)


def safe_actions(actions: list[dict[str, Any]]) -> str | None:
    """Why these actions can't be in a rule, or None if they're fine."""
    for a in actions:
        service = str(a.get("action") or a.get("service") or "")
        domain = service.split(".", 1)[0]
        if domain in NEVER or (domain and domain not in ALLOWED):
            return f"rules can't use {service or 'that'}"
    return None


def describe(hass: HomeAssistant, item: dict[str, Any]) -> str:
    """The rule in plain words — what's read back before a voice-made rule is saved."""
    def name(entity_id: str | None) -> str:
        st = hass.states.get(entity_id) if entity_id else None
        return st.name if st else (entity_id or "something")

    to = item.get("when_to") or ""
    when = f"When {name(item.get('when_entity'))} " + ({"on": "turns on or opens", "off": "turns off or closes",
                                                          "home": "comes home", "not_home": "leaves"}.get(to, f"changes to {to}") if to else "changes")
    bits = [when]
    if item.get("after_dark"):
        bits.append("after dark")
    if item.get("times"):
        bits.append(f"between {item['times']}")
    if item.get("only_if"):
        bits.append(f"if {name(item['only_if'])} is {item.get('only_if_state') or 'on'}")
    does = []
    for a in item.get("actions") or []:
        service = str(a.get("action") or "")
        verb = service.split(".", 1)[-1].replace("_", " ").replace("turn ", "turn ")
        targets = (a.get("target") or {}).get("entity_id") or (a.get("data") or {}).get("entity_id") or []
        targets = [targets] if isinstance(targets, str) else targets
        extra = ""
        data = a.get("data") or {}
        if "brightness_pct" in data:
            extra = f" at {data['brightness_pct']}%"
        elif "temperature" in data:
            extra = f" to {data['temperature']}°"
        does.append(f"{verb} {', '.join(name(t) for t in targets)}{extra}".strip())
    if item.get("say"):
        does.append(f"say “{item['say']}”")
    return ", ".join(bits) + ": " + ("; ".join(does) or "nothing yet") + "."


class Rules:
    def __init__(self, hass: HomeAssistant, items: Items, runtime: Callable[[], Any]) -> None:
        self.hass = hass
        self.items = items
        self._rt = runtime
        self._unsub = None
        self.context = Context()

    @callback
    def async_start(self) -> None:
        self._unsub = self.hass.bus.async_listen(EVENT_STATE_CHANGED, self._changed)

    @callback
    def async_stop(self) -> None:
        if self._unsub:
            self._unsub()
            self._unsub = None

    def on(self) -> bool:
        rt = self._rt()
        return rt is not None and bool(rt.options.get(CONF_RULES, False))

    @callback
    def _changed(self, event: Event) -> None:
        if not self.on():
            return
        new, old = event.data.get("new_state"), event.data.get("old_state")
        if new is None or old is None or new.state == old.state or old.state in ("unavailable", "unknown"):
            return
        for rule in self.items.enabled():
            if rule.get("when_entity") != new.entity_id:
                continue
            want = state_word(rule.get("when_to"))
            if want and new.state != want:
                continue
            if self.allowed(rule):
                self.hass.async_create_task(self.async_run(rule), eager_start=False)

    def allowed(self, rule: dict[str, Any]) -> bool:
        now = dt_util.utcnow()
        last = dt_util.parse_datetime(rule.get("last") or "")
        if last and now - last < GAP:
            return False
        if rule.get("after_dark"):
            sun = self.hass.states.get("sun.sun")
            if sun is None or sun.state != "below_horizon":
                return False
        if rule.get("times") and not in_windows(rule["times"], dt_util.now()):
            return False
        if rule.get("only_if"):
            st = self.hass.states.get(rule["only_if"])
            if st is None or st.state != (state_word(rule.get("only_if_state")) or "on"):
                return False
        return True

    async def async_run(self, rule: dict[str, Any]) -> None:
        rt = self._rt()
        self.items.update(rule["id"], last=dt_util.utcnow().isoformat())
        if why := safe_actions(rule.get("actions") or []):
            _LOGGER.warning("Rule %s not run: %s", rule.get("name"), why)
            return
        try:
            if rule.get("actions"):
                from .shortcuts import async_run_actions

                await async_run_actions(self.hass, rule["actions"], f"rule: {rule.get('name')}",
                                        Context(parent_id=self.context.id))  # the house did it, not a person
            if rule.get("say") and rt is not None:
                await rt.speech.async_say(rule["say"], "normal", None, source=f"rule:{rule.get('name')}")
        except Exception as err:  # noqa: BLE001
            _LOGGER.warning("Rule %s failed: %s", rule.get("name"), err)
            return
        if rt is not None:
            rt.timeline.add("mode", f"Rule: {rule.get('name')}", describe(self.hass, rule))
