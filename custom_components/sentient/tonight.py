"""'Make sure everything's ready for tonight': one short-lived household check, done with what Sentient already has.

It brings together the security check (doors, windows, covers and locks Assist can see), the lights, and the
heating, then:

* switches off only the lights chosen in Settings as safe to switch off at night, and checks each one reports off;
* changes nothing else: locks, the alarm and the heating are reported, never touched, and the alarm is never
  offered (house rules);
* says plainly what's still open, unlocked, left on, or can't be confirmed (a sensor with no current report);
* optionally keeps an eye on what was outstanding for up to three hours and says once, on the speaker that asked,
  when it's all sorted ("That's everything ready for tonight now: the back door's shut.").

It isn't a goals system: one check per speaker, replaced by the next one, gone after three hours (kept over a restart).
No AI. Off until switched on (Settings › Voice › Ready for tonight).
"""

from __future__ import annotations

from datetime import datetime, timedelta
import logging
from typing import Any, Callable

from homeassistant.const import EVENT_STATE_CHANGED
from homeassistant.core import Context, Event, HomeAssistant, callback
from homeassistant.util import dt as dt_util

_LOGGER = logging.getLogger(__name__)

CONF_TONIGHT = "ready_tonight"
CONF_TONIGHT_LIGHTS = "ready_tonight_lights"
CONF_TONIGHT_FOLLOW = "ready_tonight_follow"
FOLLOW_FOR = timedelta(hours=3)
MAX_RUNS = 10


def _join(names: list[str]) -> str:
    names = list(dict.fromkeys(names))
    return names[0] if len(names) == 1 else ", ".join(names[:-1]) + " and " + names[-1]


def _num(value: Any) -> float | None:
    from .numeric import finite_float

    return finite_float(value)


class Tonight:
    def __init__(self, hass: HomeAssistant, options: dict[str, Any], rt_getter: Callable[[], Any],
                 entry_id: str | None = None) -> None:
        self.hass = hass
        self.options = options
        self._rt = rt_getter
        self.runs: dict[str, dict[str, Any]] = {}  # owner -> {"outstanding": {eid: name}, "names", "speaker", "until"}
        self._unsub: Callable[[], None] | None = None
        self._store = None
        if entry_id:
            from homeassistant.helpers.storage import Store

            from .const import DOMAIN

            self._store = Store(hass, 1, f"{DOMAIN}.{entry_id}.tonight")

    async def async_load(self) -> None:
        """A check still being followed carries on after a restart, until its three hours are up."""
        if self._store is None:
            return
        try:
            saved = (await self._store.async_load() or {}).get("runs") or {}
        except Exception:  # noqa: BLE001 - a damaged file: nothing being followed
            saved = {}
        now = dt_util.utcnow()
        for owner, run in saved.items():
            until = dt_util.parse_datetime(str(run.get("until") or ""))
            if until is None or until <= now or not run.get("outstanding"):
                continue
            self.runs[owner] = {"outstanding": dict(run["outstanding"]), "names": dict(run.get("names") or run["outstanding"]),
                                "speaker": run.get("speaker"), "until": until}
        if self.runs and self.enabled:
            self._listen()
            self.hass.async_create_task(self.async_follow(), eager_start=False)  # anything sorted while it was down
        else:
            self.runs.clear()

    def _save(self) -> None:
        if self._store is not None:
            self._store.async_delay_save(lambda: {"runs": {o: {**r, "until": r["until"].isoformat()}
                                                           for o, r in self.runs.items()}}, 1)

    @property
    def enabled(self) -> bool:
        return bool(self.options.get(CONF_TONIGHT))

    @callback
    def async_stop(self) -> None:
        """Unloading: stop listening, but keep what's being followed so it carries on after the restart."""
        if self._unsub is not None:
            self._unsub()
            self._unsub = None

    def cancel(self, owner: str) -> bool:
        gone = self.runs.pop(owner, None) is not None
        self._listen()
        return gone

    async def async_check(self, owner: str, context: Context | None, speaker: str | None,
                          act: bool = True) -> dict[str, Any]:
        """Check the house (and, with act, switch off the permitted lights). Returns what to say and the details."""
        from .security import check_for_voice

        sec = await check_for_voice(self.hass, context)
        visible = await self._visible(context)
        out: dict[str, Any] = {}

        switched, failed = await self._lights(context, visible) if act else ([], [])
        permitted = set(self.options.get(CONF_TONIGHT_LIGHTS) or [])
        left_on = [s.name for s in self.hass.states.async_all("light")
                   if s.state == "on" and visible(s.entity_id) and "entity_id" not in s.attributes
                   and s.entity_id not in permitted]
        still_on = [st.name for e in permitted if visible(e) and (st := self.hass.states.get(e)) is not None
                    and st.state == "on" and st.name not in failed]

        open_items = [(i["entity_id"], i["name"]) for i in sec["open"]]
        unlocked = [(i["entity_id"], i["name"]) for i in sec["unlocked"]]
        unknown = [u.get("name", u["entity_id"]) for u in sec["unknown"]
                   if not str(u.get("entity_id", "")).startswith("alarm_control_panel.")]
        heating = self._heating(visible, {e for e, _ in open_items})

        parts: list[str] = []
        if switched:
            parts.append(f"I've switched off {_join(switched)}")
        if open_items:
            names = [n for _, n in open_items]
            parts.append(f"{_join(names)} {'is' if len(names) == 1 else 'are'} still open")
        if unlocked:
            names = [n for _, n in unlocked]
            parts.append(f"{_join(names)} {'is' if len(names) == 1 else 'are'} unlocked")
        if failed or still_on:
            parts.append(f"I couldn't confirm {_join(failed + still_on)} switched off")
        if left_on:
            parts.append(f"{_join(left_on[:6])} {'is' if len(left_on) == 1 else 'are'} still on")
        parts.extend(heating["notes"])
        if unknown:
            parts.append(f"I can't confirm {_join(unknown[:5])}: no current report")
        if not sec["open"] and not sec["unlocked"] and not unknown and not (failed or still_on):
            parts.insert(1 if switched else 0, "the doors and windows I can see are shut and locked" if sec["evidence"]
                         else "there are no door, window or lock sensors I can see")
        text = ". ".join(p[0].upper() + p[1:] for p in parts) + "." if parts else "Everything's ready for tonight."

        outstanding = dict(open_items + unlocked)
        if self.options.get(CONF_TONIGHT_FOLLOW, True) and outstanding and act:
            self.runs[owner] = {"outstanding": outstanding, "names": dict(outstanding), "speaker": speaker,
                                "until": dt_util.utcnow() + FOLLOW_FOR}
            while len(self.runs) > MAX_RUNS:
                self.runs.pop(next(iter(self.runs)))
            rt = self._rt()
            speech = getattr(rt, "speech", None) if rt is not None else None
            hushed = speech is not None and (speech.quiet_now() or speech.hushed())
            # quiet hours keep the follow-up quiet too (it goes on the timeline): don't promise a word that won't come
            text += (" I'll note when that's sorted." if hushed or not speaker else " I'll tell you when that's sorted.")
            out["following"] = list(outstanding.values())
        elif act:
            self.runs.pop(owner, None)
        self._listen()
        out.update({
            "summary": text,
            "switched_off": switched, "could_not_confirm_off": failed + still_on, "left_on": left_on[:10],
            "open": [n for _, n in open_items], "unlocked": [n for _, n in unlocked], "no_current_report": unknown,
            "heating": heating["zones"],
            "say": "Say the summary as given. Don't offer to arm or disarm the alarm. Only what's in switched_off was "
                   "changed: nothing else was touched.",
        })
        return out

    async def _visible(self, context: Context | None) -> Callable[[str], bool]:
        from homeassistant.auth.permissions.const import POLICY_READ
        from homeassistant.components.homeassistant.exposed_entities import async_should_expose

        user_id = context.user_id if context else None
        user = await self.hass.auth.async_get_user(user_id) if user_id else None

        def visible(eid: str) -> bool:
            if user_id and (not user or not user.is_active or not user.permissions.check_entity(eid, POLICY_READ)):
                return False
            try:
                return async_should_expose(self.hass, "conversation", eid)
            except KeyError:
                return False

        return visible

    async def _lights(self, context: Context | None, visible: Callable[[str], bool]) -> tuple[list[str], list[str]]:
        """Switch off the permitted lights that are on; report which really went off and which didn't."""
        rt = self._rt()
        lights = [e for e in dict.fromkeys(self.options.get(CONF_TONIGHT_LIGHTS) or []) if e.startswith("light.")][:20]
        on = [e for e in lights if visible(e) and (st := self.hass.states.get(e)) is not None and st.state == "on"
              and "entity_id" not in st.attributes]
        if not on or rt is None or getattr(rt, "household_tasks", None) is None:
            return [], []
        failed: set[str] = set()
        import asyncio

        await asyncio.gather(*(rt.household_tasks._turn_off(e, context, visible, lambda: self.enabled, failed)
                               for e in on))
        switched, not_off = [], []
        for e in on:
            st = self.hass.states.get(e)
            name = st.name if st else e
            (switched if st is not None and st.state == "off" and e not in failed else not_off).append(name)
        return switched, not_off

    def _heating(self, visible: Callable[[str], bool], open_ids: set[str]) -> dict[str, Any]:
        """What the heating is doing, and anything about it worth a word. Read only."""
        from .const import CONF_HEATING_ENTITIES
        from .heating_schedule import night_temp
        from .presence import area_of

        rt = self._rt()
        zones, notes = [], []
        open_areas = {area_of(self.hass, e) for e in open_ids} - {None}
        for heater in [h for h in self.options.get(CONF_HEATING_ENTITIES) or [] if h.startswith("climate.")]:
            st = self.hass.states.get(heater)
            if st is None or not visible(heater):
                continue
            if st.state in ("unavailable", "unknown"):
                notes.append(f"I can't reach {st.name}")
                continue
            target, now = _num(st.attributes.get("temperature")), _num(st.attributes.get("current_temperature"))
            zone: dict[str, Any] = {"name": st.name, "mode": st.state, "target": target, "now": now}
            prog = getattr(rt, "heat_prog", None) if rt is not None else None
            if prog is not None and prog.covers(heater):
                zone["overnight"] = night_temp(self.options)
            zones.append(zone)
            if st.state != "off" and area_of(self.hass, heater) in open_areas and st.attributes.get("hvac_action") == "heating":
                notes.append(f"{st.name} is heating with a window open in that room")
        return {"zones": zones, "notes": notes}

    # following what was outstanding
    def _listen(self) -> None:
        self._save()
        if self.runs and self._unsub is None:
            self._unsub = self.hass.bus.async_listen(EVENT_STATE_CHANGED, self._changed)
        elif not self.runs and self._unsub is not None:
            self._unsub()
            self._unsub = None

    @callback
    def _changed(self, event: Event) -> None:
        entity_id = event.data.get("entity_id")
        if not self.enabled:
            self.runs.clear()
            self._listen()
            return
        if not any(entity_id in run["outstanding"] for run in self.runs.values()):
            return
        self.hass.async_create_task(self.async_follow(), eager_start=False)

    @staticmethod
    def _sorted(st: Any) -> bool:
        if st is None or st.attributes.get("restored"):
            return False
        if st.domain == "lock":
            return st.state == "locked"
        if st.domain == "cover":
            return st.state == "closed"
        return st.state == "off"

    async def async_follow(self, now: datetime | None = None) -> None:
        now = now or dt_util.utcnow()
        rt = self._rt()
        for owner, run in list(self.runs.items()):
            if now >= run["until"]:
                self.runs.pop(owner, None)
                continue
            for eid in [e for e in run["outstanding"] if self._sorted(self.hass.states.get(e))]:
                run["outstanding"].pop(eid)
            if run["outstanding"]:
                continue
            self.runs.pop(owner, None)  # all sorted: said once, then forgotten
            done = [f"{name}'s {'locked' if eid.startswith('lock.') else 'shut'}" for eid, name in run["names"].items()]
            text = f"That's everything ready for tonight now: {_join(done)}."
            if rt is not None and run.get("speaker"):
                try:
                    await rt.speech.async_say(text, "normal", [run["speaker"]], source="ready for tonight")
                except Exception as err:  # noqa: BLE001
                    _LOGGER.debug("Couldn't say tonight's check is done: %s", err)
            if rt is not None:
                rt.timeline.add("house", "Ready for tonight", text)
        self._listen()
