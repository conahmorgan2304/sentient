"""Record what the real integration tells the sidebar page, for the page checks (tests/panel_pages.test.cjs).

Every read-only command the panel sends is answered by a real Sentient set up in a test Home Assistant, twice:
once with the defaults and once with every on/off setting switched on, so the most cards and Settings sections
are drawn. The answers, the entity states and the Sentient entity registry go to build/panel/<name>.json; the
page checks then open every tab and Settings page against them in a browser.

A command that errors fails here, before the browser is involved.
"""
from __future__ import annotations

import json
from pathlib import Path
from typing import Any

from homeassistant.core import HomeAssistant
from homeassistant.helpers import area_registry as ar, entity_registry as er
from pytest_homeassistant_custom_component.common import MockConfigEntry

from custom_components.sentient.const import DOMAIN
from custom_components.sentient.kinds import KINDS

OUT = Path(__file__).parents[1] / "build" / "panel"

# what the panel asks for, with the arguments it sends (from sentient-panel.js)
READS: list[dict[str, Any]] = [
    {"type": "sentient/settings", "language": "en"},
    {"type": "sentient/layout"},
    {"type": "sentient/timeline", "hours": 1, "limit": 30},
    {"type": "sentient/timeline", "hours": 72, "limit": 400},
    {"type": "sentient/wizard", "language": "en"},
    {"type": "sentient/whats_new"},
    {"type": "sentient/weekly"},
    {"type": "sentient/voice_id/details"},
    {"type": "sentient/voice_check"},
    {"type": "sentient/usage"},
    {"type": "sentient/today"},
    {"type": "sentient/suggestions"},
    {"type": "sentient/spots"},
    {"type": "sentient/shortcuts"},
    {"type": "sentient/security/frigate_health"},
    {"type": "sentient/privacy"},
    {"type": "sentient/mutes"},
    {"type": "sentient/motion_lights"},
    {"type": "sentient/memories"},
    {"type": "sentient/insights"},
    {"type": "sentient/inner"},
    {"type": "sentient/heating"},
    {"type": "sentient/glance"},
    {"type": "sentient/games"},
    {"type": "sentient/energy"},
    {"type": "sentient/doctor"},
    {"type": "sentient/doctor/speakers"},
    {"type": "sentient/digest", "hours": 24},
    {"type": "sentient/day"},
    {"type": "sentient/clips"},
    {"type": "sentient/cameras"},
    {"type": "sentient/brain"},
    {"type": "sentient/backups"},
    *({"type": "sentient/items", "kind": kind} for kind in KINDS),
]


def _house(hass: HomeAssistant) -> dict[str, Any]:
    """A small made-up house: a kitchen and a lounge with a speaker, a light, motion, a window and heating."""
    areas = ar.async_get(hass)
    kitchen, lounge = areas.async_create("Kitchen"), areas.async_create("Lounge")
    reg = er.async_get(hass)
    for domain, uid, obj, area, state, attrs in (
        ("assist_satellite", "k_sat", "kitchen", kitchen, "idle", {"friendly_name": "Kitchen speaker", "supported_features": 3}),
        ("light", "l_light", "lounge", lounge, "on", {"friendly_name": "Lounge light", "brightness": 200}),
        ("binary_sensor", "l_motion", "lounge_motion", lounge, "off", {"friendly_name": "Lounge motion", "device_class": "motion"}),
        ("binary_sensor", "k_window", "kitchen_window", kitchen, "on", {"friendly_name": "Kitchen window", "device_class": "window"}),
        ("climate", "heat", "heating", lounge, "heat", {"friendly_name": "Heating", "current_temperature": 19.5, "temperature": 20,
                                                         "hvac_modes": ["off", "heat"]}),
        ("sensor", "k_temp", "kitchen_temperature", kitchen, "21.2", {"friendly_name": "Kitchen temperature",
                                                                       "device_class": "temperature", "unit_of_measurement": "°C"}),
    ):
        reg.async_get_or_create(domain, "test", uid, suggested_object_id=obj)
        reg.async_update_entity(f"{domain}.{obj}", area_id=area.id)
        hass.states.async_set(f"{domain}.{obj}", state, attrs)
    return {"persona": "Nova", "speakers": ["assist_satellite.kitchen"], "default_speaker": "assist_satellite.kitchen",
            "heating_entities": ["climate.heating"]}


async def _record(hass: HomeAssistant, ws, name: str) -> None:
    answers: list[dict[str, Any]] = []
    for i, msg in enumerate(READS, 100):
        await ws.send_json({"id": i, **msg})
        reply = await ws.receive_json()
        if reply["success"]:
            answers.append({"request": msg, "result": reply["result"]})
            continue
        # a handled refusal (the AI isn't there in a test, a feature is off) is something the page must cope with;
        # an unhandled exception in the command is a bug
        assert reply["error"]["code"] != "unknown_error", (msg, reply["error"])
        answers.append({"request": msg, "error": reply["error"]})
    reg = er.async_get(hass)
    entities = {e.entity_id: {"entity_id": e.entity_id, "platform": e.platform, "translation_key": e.translation_key}
                for e in reg.entities.values()}
    states = {s.entity_id: {"entity_id": s.entity_id, "state": s.state, "attributes": dict(s.attributes),
                            "last_changed": s.last_changed.isoformat(), "last_updated": s.last_updated.isoformat()}
              for s in hass.states.async_all()}
    OUT.mkdir(parents=True, exist_ok=True)
    (OUT / f"{name}.json").write_text(json.dumps({"answers": answers, "entities": entities, "states": states},
                                                 default=str), encoding="utf-8")


async def test_record_what_the_page_is_told(hass: HomeAssistant, hass_ws_client, tmp_path) -> None:
    hass.config.media_dirs = {"local": str(tmp_path)}
    entry = MockConfigEntry(domain=DOMAIN, title="Nova", unique_id=DOMAIN, data=_house(hass))
    entry.add_to_hass(hass)
    assert await hass.config_entries.async_setup(entry.entry_id)
    await hass.async_block_till_done()
    rt = entry.runtime_data
    rt.memory.add("The bins go out on Thursday", "routine")
    rt.timeline.add("said", "The kitchen window is still open.", "Kitchen", source="left_open")
    rt.timeline.add("noticed", "Lounge light changed from off to on", "noticed, decided it wasn't worth saying")
    ws = await hass_ws_client(hass)
    await _record(hass, ws, "defaults")

    # every on/off setting on, so every optional card and Settings section is drawn
    await ws.send_json({"id": 500, "type": "sentient/settings", "language": "en"})
    reply = await ws.receive_json()
    assert reply["success"], reply
    settings = reply["result"]
    for n, group in enumerate(settings["groups"], 501):
        flags = {f["name"]: True for f in group["schema"] if "boolean" in (f.get("selector") or {})}
        if not flags:
            continue
        await ws.send_json({"id": n, "type": "sentient/settings/save", "group": group["id"], "data": flags})
        assert (await ws.receive_json())["success"], group["id"]
        await hass.async_block_till_done()
    ws = await hass_ws_client(hass)  # the entry reloaded; a fresh connection sees the new runtime
    from custom_components.sentient.conversation_quality import of

    quality = of(entry.runtime_data)  # some measured turns, so those Mind rows are drawn too
    quality.record(text="when do the bins go out?", reply="The bins go out on Thursday.", carried=False,
                   asked_back=False, error=False, ms=900, memories=["The bins go out on Thursday"])
    quality.record(text="what about the recycling?", reply="Which bin?", carried=True, asked_back=True, error=False)
    rt = entry.runtime_data
    item = rt.incidents.add("camera.gate", "Person reported at Gate", "detection")
    if item is not None:
        rt.incidents.review(item["id"], "visitor")
    # camera visits on the Security page: one to review, one reviewed, and a zone suggested for hiding
    from homeassistant.util import dt as dt_util

    events = rt.security_events
    events.cameras = lambda: {"gate"}
    now = dt_util.utcnow().timestamp()
    for n, (zone, label) in enumerate((("path", "person"), ("path", "person"), ("path", "person"), ("drive", "cat"))):
        events.ingest({"id": f"visit-{n}", "camera": "gate", "label": label, "start_time": now - 4000 + n * 900,
                       "end_time": now - 3990 + n * 900, "entered_zones": [zone], "has_snapshot": True, "has_clip": True})
    for n in range(3):
        events.feedback(f"visit-{n}", "false_positive")
    from homeassistant.helpers.entity_component import DATA_INSTANCES

    for entity in hass.data[DATA_INSTANCES]["sensor"].entities:  # the Mind sensor shows them now, not in a minute
        if entity.platform.platform_name == DOMAIN:
            entity.async_write_ha_state()
    await hass.async_block_till_done()
    await _record(hass, ws, "all_on")
    assert (OUT / "defaults.json").exists() and (OUT / "all_on.json").exists()
