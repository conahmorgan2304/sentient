"""Run a recorded day through Sentient's real decision code, on a simulated clock.

Used by the replay harness (``scripts/replay/test_day.py``, driven by ``scripts/replay.py``) and by the
regression days in the tests. It must keep working against older versions of the integration too, so it only
uses what has been stable for a long time: a config entry, ``runtime_data.timeline`` and ``AIGate.async_call``.

What's real and what isn't:

* The recorded changes are applied to Home Assistant at their recorded times, with a context that matches
  their recorded cause (nothing in Home Assistant, a person, an automation, a service call), so the ledger and
  everything listening hear them as they happened.
* Nothing is spoken and no device is touched: every speaker, phone and device service is a recorder. A device
  Sentient switches doesn't change state in the replay (only the recording changes states).
* AI isn't called: every AI request is counted and answered "unavailable", so both versions run on their
  plain rules. The count still shows how often each version would have asked.
* Sentient's own changes in the recording are applied as service calls by Home Assistant, not as Sentient:
  the replayed Sentient makes its own.
"""
from __future__ import annotations

from datetime import datetime, timedelta, timezone
from typing import Any
from unittest.mock import patch

from homeassistant.core import Context, HomeAssistant, ServiceCall
from homeassistant.helpers import area_registry as ar, entity_registry as er
from homeassistant.util import dt as dt_util

SPEECH = {("assist_satellite", "announce"), ("assist_satellite", "start_conversation"), ("tts", "speak"),
          ("media_player", "play_media")}
DEVICE_DOMAINS = ("light", "switch", "fan", "climate", "cover", "lock", "alarm_control_panel", "media_player",
                  "water_heater", "vacuum", "humidifier", "input_boolean", "scene", "script", "button", "select",
                  "number", "siren", "valve", "camera", "todo", "homeassistant")
DEVICE_SERVICES = ("turn_on", "turn_off", "toggle", "set_temperature", "set_hvac_mode", "set_preset_mode",
                   "open_cover", "close_cover", "lock", "unlock", "press", "select_option", "set_value",
                   "media_pause", "media_play", "media_stop", "media_seek", "volume_set", "volume_mute",
                   "start", "stop", "return_to_base", "add_item", "snapshot", "set_percentage", "set_operation_mode",
                   "alarm_arm_away", "alarm_arm_home", "alarm_disarm", "reload")
STEP = timedelta(minutes=1)
_ACTION = {"said": "spoke", "held": "held", "critical": "spoke"}


def record(item: dict[str, Any]) -> dict[str, Any]:
    """The same shape as replay.decision_record in the integration (kept here so older versions replay too)."""
    decision = item.get("decision") if isinstance(item.get("decision"), dict) else {}
    action = _ACTION.get(item.get("kind"), "noted")
    return {
        "time": item.get("time"), "kind": item.get("kind"), "action": action, "phoned": bool(item.get("phoned")),
        "source": item.get("source") or decision.get("kind") or item.get("kind"),
        "reason": item.get("reason") or (item.get("detail") if action == "held" else None),
        "why": item.get("why"), "priority": item.get("priority"), "text": item.get("text"),
        "evidence": list(decision.get("evidence") or [])[:6], "surprise": decision.get("surprise"),
        "ai_used": decision.get("ai_used"),
    }


def _when(value: Any) -> datetime:
    if isinstance(value, (int, float)):
        return datetime.fromtimestamp(value, tz=timezone.utc)
    parsed = dt_util.parse_datetime(str(value))
    if parsed is None:
        raise ValueError(f"Not a time: {value!r}")
    return dt_util.as_utc(parsed)


async def async_replay(hass: HomeAssistant, day: dict[str, Any], freezer: Any) -> dict[str, Any]:
    """Replay the day; returns what Sentient decided, said, sent and switched, and how often it asked for AI."""
    from pytest_homeassistant_custom_component.common import MockConfigEntry, async_fire_time_changed

    from custom_components.sentient import ai as ai_module
    from custom_components.sentient.const import DOMAIN

    start, end = _when(day["from"]), _when(day["to"])
    freezer.move_to(start)
    await hass.config.async_set_time_zone(day.get("time_zone") or "Europe/London")

    # the house: rooms, devices in them, and how they were when the period began
    from homeassistant.helpers import floor_registry as fr

    floors = fr.async_get(hass)
    floor_ids = {old: floors.async_create(f["name"], level=f.get("level")).floor_id
                 for old, f in (day.get("floors") or {}).items()}
    areas = ar.async_get(hass)
    area_ids = {}
    for old, info in (day.get("areas") or {}).items():
        info = info if isinstance(info, dict) else {"name": info}
        area_ids[old] = areas.async_create(info["name"], floor_id=floor_ids.get(info.get("floor"))).id
    reg = er.async_get(hass)
    for entity_id, info in (day.get("entities") or {}).items():
        domain, obj = entity_id.split(".", 1)
        reg.async_get_or_create(domain, "replay", entity_id, suggested_object_id=obj)
        if info.get("area") in area_ids:
            reg.async_update_entity(entity_id, area_id=area_ids[info["area"]])
    states = {e: dict(s) for e, s in (day.get("states") or {}).items()}
    for entity_id, s in states.items():
        hass.states.async_set(entity_id, s.get("state"), s.get("attributes") or {})

    # nothing spoken, nothing switched: every speaker, phone and device service records instead
    calls: list[dict[str, Any]] = []

    async def _recorder(call: ServiceCall) -> None:
        calls.append({"time": dt_util.utcnow().isoformat(), "domain": call.domain, "service": call.service,
                      "data": {k: v for k, v in call.data.items() if isinstance(v, (str, int, float, bool, list))}})

    pairs = set(SPEECH) | {(d, s) for d in DEVICE_DOMAINS for s in DEVICE_SERVICES} | {("notify", "send_message")}
    options = day.get("options") or {}
    for value in [*(options.get("notify_targets") or []), *(options.get("phone_apps") or [])]:
        if isinstance(value, str):
            pairs.add(("notify", value.split(".", 1)[-1]))
    for domain, service in pairs:
        if not hass.services.has_service(domain, service):
            hass.services.async_register(domain, service, _recorder)

    asked = {"n": 0}

    async def _no_ai(self, data, *args, **kwargs):  # noqa: ANN001 - same signature as AIGate.async_call
        asked["n"] += 1
        raise ai_module.AIUnavailable("replay: no AI")

    # the settings name rooms by their ids, which the replayed house gives new ones
    def _remap(value: Any) -> Any:
        if isinstance(value, str):
            return area_ids.get(value, value)
        if isinstance(value, list):
            return [_remap(v) for v in value]
        if isinstance(value, dict):
            return {k: _remap(v) for k, v in value.items()}
        return value

    options = _remap(options)
    user = await hass.auth.async_create_user("Replay person")
    entry = MockConfigEntry(domain=DOMAIN, title=options.get("persona") or "Replay", unique_id=DOMAIN,
                            data={k: v for k, v in options.items()})
    entry.add_to_hass(hass)
    with patch.object(ai_module.AIGate, "async_call", _no_ai):
        assert await hass.config_entries.async_setup(entry.entry_id), "Sentient didn't start in the replay"
        await hass.async_block_till_done()

        async def _advance(to: datetime) -> None:
            # minute by minute, so a timer fires when it's due, not at the next recorded change
            now = dt_util.utcnow()
            while now < to:
                now = min(to, now + STEP)
                freezer.move_to(now)
                async_fire_time_changed(hass, now)
                await hass.async_block_till_done(wait_background_tasks=True)

        for step in sorted(day.get("changes") or [], key=lambda c: c["ts"]):
            await _advance(_when(step["ts"]))
            if step.get("reload"):  # Sentient restarting (an update, a settings change)
                assert await hass.config_entries.async_reload(entry.entry_id)
                await hass.async_block_till_done(wait_background_tasks=True)
                continue
            entity_id = step["entity"]
            context = _context(hass, step, user.id)
            attrs = (states.get(entity_id) or {}).get("attributes") or {}
            hass.states.async_set(entity_id, step.get("new"), attrs, context=context)
            await hass.async_block_till_done(wait_background_tasks=True)

        await _advance(end)
        timeline = list(entry.runtime_data.timeline.items)
        await hass.config_entries.async_unload(entry.entry_id)
        await hass.async_block_till_done()

    decisions = [record(i) for i in reversed(timeline)
                 if (when := dt_util.parse_datetime(i.get("time") or "")) and start <= when <= end]
    return {
        "from": start.isoformat(), "to": end.isoformat(), "changes": len(day.get("changes") or []),
        "decisions": decisions,
        "speech": [c for c in calls if (c["domain"], c["service"]) in SPEECH],
        "phones": [c for c in calls if c["domain"] == "notify"],
        "device_actions": [c for c in calls if (c["domain"], c["service"]) not in SPEECH and c["domain"] != "notify"],
        "ai_requests": asked["n"],
    }


def _context(hass: HomeAssistant, step: dict[str, Any], user_id: str) -> Context:
    """A context matching the recorded cause, announced the way Home Assistant would announce it."""
    cause = step.get("cause") or "device"
    if cause == "device":
        return Context()
    if cause == "person":
        return Context(user_id=user_id)
    context = Context()
    if cause == "automation":
        hass.bus.async_fire("automation_triggered", {"name": step.get("who") or "an automation"}, context=context)
    elif cause == "script":
        hass.bus.async_fire("script_started", {"name": step.get("who") or "a script"}, context=context)
    else:  # Home Assistant, a voice request or Sentient in the recording: a service call made it
        domain = step["entity"].split(".", 1)[0]
        hass.bus.async_fire("call_service", {"domain": domain, "service": "turn_on",
                                             "service_data": {"entity_id": step["entity"]}}, context=context)
    return context
