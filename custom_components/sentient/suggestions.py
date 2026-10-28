"""Suggestions: the set-up wizard, all the time.

The house changes — a new speaker, a power sensor on the washing machine, a thermometer in the hutch.
The same look the wizard does finds what Sentient could use but isn't, and the Sentient page offers it
one line at a time: "The Washing machine power sensor could say when the washing's done — use it?"
Yes adds it to the settings; no means it's never offered again. Plain rules, no AI. Can be turned off.
"""

from __future__ import annotations

from typing import Any

import voluptuous as vol

from homeassistant.components import websocket_api
from homeassistant.core import HomeAssistant, callback
from homeassistant.helpers.storage import Store

from .const import (
    CONF_AI_TASK_ENTITY,
    CONF_ANIMAL_SENSORS,
    CONF_APPLIANCES,
    CONF_DOOR_CAMERA,
    CONF_SPEAKERS,
    CONF_SUGGESTIONS,
    DOMAIN,
)

# (setting, is a list, how to say it)
OFFERS: list[tuple[str, bool, str]] = [
    (CONF_SPEAKERS, True, "{name} could be one of the house's speakers"),
    (CONF_APPLIANCES, True, "{name} could say when it's finished, or seems stuck"),
    (CONF_ANIMAL_SENSORS, True, "{name} could warn when it's too cold or too hot for the animals"),
    (CONF_DOOR_CAMERA, False, "{name} could be the doorbell camera — who's there, parcels left"),
    (CONF_AI_TASK_ENTITY, False, "{name} could do the camera looks and background thinking"),
]


class Suggestions:
    def __init__(self, hass: HomeAssistant, entry_id: str) -> None:
        self.hass = hass
        self._store: Store[dict[str, Any]] = Store(hass, 1, f"{DOMAIN}.{entry_id}.suggestions")
        self.dismissed: set[str] = set()

    async def async_load(self) -> None:
        self.dismissed = set((await self._store.async_load() or {}).get("dismissed", []))

    def listing(self, options: dict[str, Any], own_agent: str | None = None) -> list[dict[str, Any]]:
        if not options.get(CONF_SUGGESTIONS, True):
            return []
        from .wizard import suggest

        found = suggest(self.hass, options, own_agent)
        out = []
        for key, many, text in OFFERS:
            wanted = found.get(key)
            if not wanted:
                continue
            have = options.get(key)
            if many:
                new = [e for e in wanted if e not in (have or [])]
            else:
                new = [wanted] if not have else []
            for entity_id in new:
                sid = f"{key}|{entity_id}"
                if sid in self.dismissed or (st := self.hass.states.get(entity_id)) is None:
                    continue
                out.append({"id": sid, "key": key, "entity_id": entity_id, "text": text.format(name=st.name)})
        return out[:6]

    @callback
    def dismiss(self, sid: str) -> None:
        self.dismissed.add(sid)
        self._store.async_delay_save(lambda: {"dismissed": sorted(self.dismissed)}, 1)


def async_register(hass: HomeAssistant) -> None:
    websocket_api.async_register_command(hass, ws_suggestions)
    websocket_api.async_register_command(hass, ws_suggestion_act)


def _entry(hass: HomeAssistant):
    entries = hass.config_entries.async_loaded_entries(DOMAIN)
    return entries[0] if entries else None


def _own_agent(hass: HomeAssistant, entry) -> str | None:
    from homeassistant.helpers import entity_registry as er

    return er.async_get(hass).async_get_entity_id("conversation", DOMAIN, f"{entry.entry_id}_voice")


@websocket_api.websocket_command({vol.Required("type"): "sentient/suggestions"})
@callback
def ws_suggestions(hass: HomeAssistant, connection, msg: dict[str, Any]) -> None:
    entry = _entry(hass)
    if entry is None:
        connection.send_result(msg["id"], {"items": []})
        return
    rt = entry.runtime_data
    connection.send_result(msg["id"], {"items": rt.suggestions.listing(rt.options, _own_agent(hass, entry))})


@websocket_api.websocket_command({
    vol.Required("type"): "sentient/suggestions/act", vol.Required("suggestion"): str, vol.Required("accept"): bool,
})
@websocket_api.require_admin
@callback
def ws_suggestion_act(hass: HomeAssistant, connection, msg: dict[str, Any]) -> None:
    entry = _entry(hass)
    if entry is None:
        connection.send_error(msg["id"], "not_loaded", "Sentient is not set up")
        return
    rt = entry.runtime_data
    item = next((i for i in rt.suggestions.listing(rt.options, _own_agent(hass, entry)) if i["id"] == msg["suggestion"]), None)
    if item is None:
        connection.send_error(msg["id"], "not_found", "That suggestion has gone")
        return
    rt.suggestions.dismiss(item["id"])  # either way, never asked again
    if msg["accept"]:
        opts = {**entry.data, **entry.options}
        many = next(m for k, m, _ in OFFERS if k == item["key"])
        value = [*(opts.get(item["key"]) or []), item["entity_id"]] if many else item["entity_id"]
        hass.config_entries.async_update_entry(entry, options={**opts, item["key"]: value})
        rt.timeline.add("mode", f"Now using {hass.states.get(item['entity_id']).name}", "from a suggestion")
    connection.send_result(msg["id"], {"done": True})
