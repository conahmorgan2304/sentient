"""Weather warnings by country, read-only email, calendar clashes, the security check, automations, incident stages."""
from datetime import timedelta
from unittest.mock import AsyncMock, patch

from homeassistant.core import HomeAssistant
from homeassistant.util import dt as dt_util
from pytest_homeassistant_custom_component.common import MockConfigEntry, async_mock_service

from custom_components.sentient.const import DOMAIN
from custom_components.sentient.incidents import stage
from custom_components.sentient.skills import (
    async_check_automation,
    async_save_automation,
    async_security_audit,
    describe_automation,
    find_clashes,
)
from custom_components.sentient.warnings_feed import (
    countries,
    is_severe,
    parse_floods,
    parse_meteoalarm,
    parse_metoffice,
    parse_nws,
    source_for,
)

METOFFICE = """<?xml version="1.0"?><rss><channel>
<item><title>Yellow warning of rain affecting North West England</title><guid>a1</guid><description>Rain</description></item>
<item><title>Amber warning of wind affecting Scotland</title><guid>a2</guid><description>Gales</description></item>
</channel></rss>"""
ATOM = """<?xml version="1.0"?><feed xmlns="http://www.w3.org/2005/Atom" xmlns:cap="urn:oasis:names:tc:emergency:cap:1.2">
<entry><id>x1</id><title>t</title><cap:event>Severe thunderstorm</cap:event><cap:severity>Severe</cap:severity><cap:areaDesc>Bavaria</cap:areaDesc></entry>
<entry><id>x2</id><title>t</title><cap:event>Fog</cap:event><cap:severity>Minor</cap:severity><cap:areaDesc>Hesse</cap:areaDesc></entry>
</feed>"""


async def _setup(hass, **options):
    entry = MockConfigEntry(domain=DOMAIN, title="Nova", unique_id=DOMAIN, data={"persona": "Nova"}, options=options)
    entry.add_to_hass(hass)
    assert await hass.config_entries.async_setup(entry.entry_id)
    await hass.async_block_till_done(wait_background_tasks=True)
    return entry


# ----- warnings --------------------------------------------------------------------------------
def test_each_country_reads_its_own_service():
    met = parse_metoffice(METOFFICE)
    assert [w["level"] for w in met] == ["yellow", "amber"] and is_severe(met[1]) and not is_severe(met[0])
    eu = parse_meteoalarm(ATOM)
    assert len(eu) == 1 and eu[0]["title"] == "Severe thunderstorm — Bavaria" and is_severe(eu[0])
    floods = parse_floods({"items": [{"@id": "f1", "severityLevel": 2, "severity": "Flood warning", "description": "River Douglas"},
                                     {"@id": "f2", "severityLevel": 4, "description": "no longer in force"}]})
    assert [f["id"] for f in floods] == ["f1"] and is_severe(floods[0])
    us = parse_nws({"features": [{"properties": {"id": "u1", "headline": "Winter Storm Warning", "severity": "Severe"}}]})
    assert us[0]["title"] == "Winter Storm Warning"
    assert source_for("gb").startswith("Met Office") and source_for("DE") == "MeteoAlarm" and source_for("JP") is None
    assert {"value": "GB", "label": "United Kingdom"} in countries()


async def test_new_severe_warnings_are_said_once_and_regions_filter(hass: HomeAssistant):
    hass.config.country = "GB"
    entry = await _setup(hass, weather_warnings=True, warnings_region="Scotland")
    w = entry.runtime_data.warnings

    async def fetch(url, as_json):
        return {"items": []} if as_json else METOFFICE

    with patch.object(w, "_fetch", side_effect=fetch), patch.object(entry.runtime_data.speech, "async_say", AsyncMock()) as say:
        found = await w.async_check()
        await w.async_check()
    assert [x["title"] for x in found] == ["Amber warning of wind affecting Scotland"]
    say.assert_called_once()
    assert w.listing()["source"] == "Met Office and Environment Agency"


async def test_no_feed_for_a_country_says_so_and_off_forgets(hass: HomeAssistant):
    entry = await _setup(hass, weather_warnings=True, warnings_country="JP")
    w = entry.runtime_data.warnings
    assert await w.async_check() == [] and "No official warnings feed" in w.error
    hass.config_entries.async_update_entry(entry, options={**entry.options, "weather_warnings": False})
    await hass.async_block_till_done()
    assert w.current == [] and w.error is None and w._unsub is None


# ----- email -----------------------------------------------------------------------------------
async def test_email_is_kept_only_while_switched_on(hass: HomeAssistant):
    entry = await _setup(hass, email_read=True)
    hass.bus.async_fire("imap_content", {"sender": "school@example.com", "subject": "Trip on Friday", "text": "Bring a coat"})
    await hass.async_block_till_done()
    inbox = entry.runtime_data.inbox
    assert inbox.listing()["messages"][0]["subject"] == "Trip on Friday"
    hass.config_entries.async_update_entry(entry, options={**entry.options, "email_read": False})
    await hass.async_block_till_done()
    assert not inbox.messages
    hass.bus.async_fire("imap_content", {"sender": "x", "subject": "y"})
    await hass.async_block_till_done()
    assert not inbox.messages


# ----- calendar --------------------------------------------------------------------------------
def test_overlaps_and_tight_gaps_are_found():
    base = dt_util.now().replace(hour=9, minute=0, second=0, microsecond=0)
    ev = lambda s, e, n: {"start": (base + timedelta(minutes=s)).isoformat(), "end": (base + timedelta(minutes=e)).isoformat(), "summary": n}  # noqa: E731
    clashes = find_clashes([ev(0, 60, "Dentist"), ev(30, 90, "School run"), ev(100, 120, "Call"), ev(300, 360, "Gym")])
    assert [(c["kind"], c["first"], c["second"]) for c in clashes] == [("overlap", "Dentist", "School run"), ("tight", "School run", "Call")]


# ----- security check --------------------------------------------------------------------------
async def test_security_check_flags_reachable_locks_and_http(hass: HomeAssistant):
    await _setup(hass)
    hass.states.async_set("lock.front_door", "locked", {"friendly_name": "Front door"})
    await hass.config.async_update(external_url="http://example.duckdns.org")
    with patch("homeassistant.components.homeassistant.exposed_entities.async_should_expose", return_value=True):
        out = await async_security_audit(hass, {"guarded_control": False})
    texts = [f["finding"] for f in out["findings"]]
    assert out["findings"][0]["level"] == "serious"
    assert any("plain http" in t for t in texts) and any("Front door" in t and "guarded device control is off" in t for t in texts)


# ----- automations from plain English ------------------------------------------------------------
async def test_automations_are_checked_before_they_are_saved(hass: HomeAssistant, tmp_path):
    await _setup(hass)
    hass.states.async_set("light.porch", "off")
    async_mock_service(hass, "light", "turn_on")
    bad = {"alias": "Porch", "triggers": [{"trigger": "sun", "event": "sunset"}],
           "actions": [{"action": "light.turn_on", "target": {"entity_id": "light.porhc"}}]}
    out = await async_check_automation(hass, bad)
    assert not out["ok"] and "did you mean light.porch" in out["problems"][0]
    assert not (await async_check_automation(hass, {**bad, "actions": [{"action": "light.explode", "target": {"entity_id": "light.porch"}}]}))["ok"]
    hass.states.async_set("lock.front", "locked")
    unlock = await async_check_automation(hass, {**bad, "actions": [{"action": "lock.unlock", "target": {"entity_id": "lock.front"}}]})
    assert not unlock["ok"] and "automation editor" in unlock["problems"][0]
    good = {**bad, "actions": [{"action": "light.turn_on", "target": {"entity_id": "light.porch"}}]}
    assert "Name: Porch" in describe_automation(good)
    path = tmp_path / "automations.yaml"
    path.write_text("[]\n")
    reload = async_mock_service(hass, "automation", "reload")
    with patch.object(hass.config, "path", return_value=str(path)):
        saved = await async_save_automation(hass, good)
    assert saved["saved"] and "light.porch" in path.read_text() and len(reload) == 1


async def test_an_automation_needs_a_signed_in_admin_and_a_yes_in_a_later_turn(hass: HomeAssistant):
    from homeassistant.core import Context
    from homeassistant.helpers import llm

    from custom_components.sentient.focus import current_request
    from custom_components.sentient.llm_api import CreateAutomationTool

    entry = await _setup(hass)
    tool = CreateAutomationTool()
    admin = await hass.auth.async_create_user("Admin", group_ids=["system-admin"])
    args = {"alias": "Porch", "triggers": [{"trigger": "sun", "event": "sunset"}],
            "actions": [{"action": "light.turn_on", "target": {"entity_id": "light.porch"}}]}

    async def call(context, **extra):
        ctx = llm.LLMContext(platform="test", context=context, language="en", assistant="conversation", device_id=None)
        out = await tool.async_call(hass, llm.ToolInput(tool_name=tool.name, tool_args={**args, **extra}), ctx)
        return out.data if hasattr(out, "data") else out

    assert "switched off" in (await call(Context(user_id=admin.id)))["error"]
    hass.config_entries.async_update_entry(entry, options={**entry.options, "automation_builder": True})
    await hass.async_block_till_done()
    hass.states.async_set("light.porch", "off")
    async_mock_service(hass, "light", "turn_on")
    assert "signed in" in (await call(Context()))["error"]  # a voice satellite has no user: never enough
    with patch("custom_components.sentient.skills.async_save_automation", AsyncMock(return_value={"saved": True})) as save:
        turn = Context(user_id=admin.id)
        assert "read_back" in await call(turn)
        assert "next turn" in (await call(turn, confirm=True))["error"]  # the model can't say yes for them
        changed = {"actions": [{"action": "light.turn_on", "target": {"entity_id": "light.porch"}, "data": {"brightness": 255}}]}
        token = current_request.set("yes")
        try:
            assert "isn't the automation that was read back" in (await call(Context(user_id=admin.id), confirm=True, **changed))["error"]
        finally:
            current_request.reset(token)
        for words in ("what's the weather like?", "yes but make it ten minutes later", ""):
            token = current_request.set(words)
            try:
                assert "plainly said yes" in (await call(Context(user_id=admin.id), confirm=True))["error"], words
            finally:
                current_request.reset(token)
        assert not save.called
        token = current_request.set("Yes, save it please.")
        try:
            assert (await call(Context(user_id=admin.id), confirm=True)) == {"saved": True}
            assert save.call_count == 1
            assert "Nothing has been read back" in (await call(Context(user_id=admin.id), confirm=True))["error"]  # used once
        finally:
            current_request.reset(token)


async def test_indirect_and_templated_actions_are_refused(hass: HomeAssistant):
    await _setup(hass)
    hass.states.async_set("scene.night", "scening", {"entity_id": ["lock.front", "light.hall"]})
    hass.states.async_set("script.unlock_all", "off")
    async_mock_service(hass, "scene", "turn_on")
    async_mock_service(hass, "script", "turn_on")
    base = {"alias": "x", "triggers": [{"trigger": "sun", "event": "sunset"}]}
    nested = {**base, "actions": [{"choose": [{"conditions": [], "sequence": [{"action": "script.turn_on", "target": {"entity_id": "script.unlock_all"}}]}]}]}
    assert not (await async_check_automation(hass, nested))["ok"]
    scene = {**base, "actions": [{"action": "scene.turn_on", "target": {"entity_id": "scene.night"}}]}
    assert "sets a lock or alarm" in (await async_check_automation(hass, scene))["problems"][0]
    hass.states.async_set("light.hall", "off")
    async_mock_service(hass, "light", "turn_on")
    templ = {**base, "actions": [{"action": "light.turn_on", "target": {"entity_id": "light.hall"}, "data": {"brightness": "{{ 255 }}"}}]}
    assert "Templates" in (await async_check_automation(hass, templ))["problems"][0]


# ----- incident stages -------------------------------------------------------------------------
def test_incident_stages_follow_the_evidence():
    now = dt_util.utcnow()
    base = {"sources": ["camera.gate"], "evidence": [{"text": "person"}], "alert_level": 0, "end": now.isoformat()}
    assert stage(base)[0] == "observed"
    assert stage({**base, "sources": ["camera.gate", "camera.garden"]})[0] == "investigating"
    assert stage({**base, "alert_level": 2})[0] == "concerning"
    assert stage({**base, "evidence": [{"text": "kept quiet: probably someone from the house going out"}]})[0] == "likely household"
    assert stage({**base, "end": (now - timedelta(hours=1)).isoformat()})[0] == "resolved"


async def test_every_new_setting_has_a_page_and_words(hass: HomeAssistant):
    import json
    from pathlib import Path

    from custom_components.sentient.websocket import GROUPS

    strings = json.loads((Path(__file__).parents[1] / "custom_components/sentient/strings.json").read_text())
    data = strings["options"]["step"]["settings"]
    keys = [k for g in GROUPS if g[0] in ("memory", "warnings", "skills") for k in g[4]]
    assert len(keys) == 20
    for key in keys:
        assert key in data["data"] and key in data["data_description"], key


def test_a_learned_routine_becomes_an_automation():
    from custom_components.sentient.learning import Routine
    from custom_components.sentient.skills import routine_automation

    clock = Routine("light.hallway", "Hallway light", "on", 17 * 60 + 30, "on weekdays", 0.8, 10, 5.0)
    auto = routine_automation(clock)
    assert auto["triggers"] == [{"trigger": "time", "at": "17:30:00"}]
    assert auto["conditions"] == [{"condition": "time", "weekday": ["mon", "tue", "wed", "thu", "fri"]}]
    assert auto["actions"] == [{"action": "light.turn_on", "target": {"entity_id": "light.hallway"}}]
    dusk = Routine("light.porch", "Porch", "on", 0, "every day", 0.8, 10, 5.0, anchor="sunset", offset=-15)
    assert routine_automation(dusk)["triggers"] == [{"trigger": "sun", "event": "sunset", "offset": "-00:15:00"}]
    assert routine_automation(Routine("light.x", "X", "on", 0, "every day", 0.8, 10, 5.0, anchor="getting up")) is None


async def test_garage_doors_and_gates_are_refused(hass: HomeAssistant):
    await _setup(hass)
    hass.states.async_set("cover.garage", "closed", {"device_class": "garage"})
    async_mock_service(hass, "cover", "open_cover")
    out = await async_check_automation(hass, {"alias": "x", "triggers": [{"trigger": "sun", "event": "sunset"}],
                                              "actions": [{"action": "cover.open_cover", "target": {"entity_id": "cover.garage"}}]})
    assert not out["ok"] and "garage door" in out["problems"][0]


async def test_reload_receipt_is_not_proof_new_automation_is_active(hass: HomeAssistant):
    from custom_components.sentient.skills import async_activate

    async_mock_service(hass, "automation", "reload")
    missing = await async_activate(hass, "generated-123")
    assert missing["reload_accepted"] is True
    assert missing["active"] is False and missing["loaded"] is False

    hass.states.async_set("automation.generated", "off", {"id": "generated-123"})
    disabled = await async_activate(hass, "generated-123")
    assert disabled["loaded"] is True and disabled["active"] is False

    hass.states.async_set("automation.generated", "on", {"id": "generated-123"})
    active = await async_activate(hass, "generated-123")
    assert active["loaded"] is True and active["active"] is True


def test_production_code_never_waits_for_every_task():
    from pathlib import Path

    for path in (Path(__file__).parents[1] / "custom_components/sentient").glob("*.py"):
        code = "\n".join(line for line in path.read_text().splitlines() if not line.lstrip().startswith("#"))
        assert "await hass.async_block_till_done(" not in code and "await self.hass.async_block_till_done(" not in code, path.name
