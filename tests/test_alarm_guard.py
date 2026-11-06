"""Alarms made by voice get the same guards as rules: allowed actions only, exposed entities only, read back first,
and checked again when they go off."""

from homeassistant.components.conversation.chat_log import UserContent, async_get_chat_log
from homeassistant.helpers import chat_session
from homeassistant.components.homeassistant.exposed_entities import async_expose_entity
from homeassistant.core import HomeAssistant
from homeassistant.helpers import llm
from homeassistant.setup import async_setup_component
from pytest_homeassistant_custom_component.common import MockConfigEntry, async_mock_service

from custom_components.sentient.const import DOMAIN

OPTIONS = {"persona": "Nova", "quiet_start": "00:00:00", "quiet_end": "00:00:00"}


async def _setup(hass: HomeAssistant):
    assert await async_setup_component(hass, "homeassistant", {})
    for eid, name in (("script.open_everything", "Open everything"), ("automation.burglar_alarm", "Burglar alarm"),
                      ("switch.heated_blanket", "Heated blanket"), ("switch.garage_heater", "Garage heater")):
        hass.states.async_set(eid, "off", {"friendly_name": name})
    for eid in ("script.open_everything", "automation.burglar_alarm", "switch.heated_blanket"):
        async_expose_entity(hass, "conversation", eid, True)
    async_expose_entity(hass, "conversation", "switch.garage_heater", False)
    entry = MockConfigEntry(domain=DOMAIN, title="Nova", unique_id=DOMAIN, data=OPTIONS)
    entry.add_to_hass(hass)
    assert await hass.config_entries.async_setup(entry.entry_id)
    await hass.async_block_till_done()
    api = await llm.async_get_api(hass, DOMAIN, llm.LLMContext(platform="t", context=None, language="en",
                                                               assistant=None, device_id=None))
    return entry, api


async def _alarm(api, **args):
    out = await api.async_call_tool(llm.ToolInput(tool_name="sentient_alarm", tool_args={
        "action": "set", "name": "Test", "time": "21:00", "days": "every day", "silent": True, **args}))
    return getattr(out, "data", out)


async def test_scripts_and_automations_refused(hass: HomeAssistant) -> None:
    entry, api = await _setup(hass)
    out = await _alarm(api, turn_on="open everything", confirmed=True)
    assert "can't use script.turn_on" in out["error"]
    out = await _alarm(api, turn_off="burglar alarm", confirmed=True)
    assert "can't use automation.turn_off" in out["error"]
    assert entry.runtime_data.lists["alarms"].find("Test") is None


async def test_not_exposed_refused(hass: HomeAssistant) -> None:
    entry, api = await _setup(hass)
    out = await _alarm(api, turn_on="garage heater", confirmed=True)
    assert "isn't exposed to voice assistants" in out["error"]
    assert entry.runtime_data.lists["alarms"].find("Test") is None


async def test_read_back_first_then_saved(hass: HomeAssistant) -> None:
    entry, api = await _setup(hass)
    with chat_session.async_get_chat_session(hass) as session, async_get_chat_log(hass, session) as chat:
        chat.async_add_user_content(UserContent("Schedule the heated blanket"))
        out = await _alarm(api, turn_on="heated blanket")
    assert out["read_back"].startswith("'Test': at 21:00 every day, switch on the Heated blanket")
    assert entry.runtime_data.lists["alarms"].find("Test") is None
    with chat_session.async_get_chat_session(hass, session.conversation_id) as session, async_get_chat_log(hass, session) as chat:
        chat.async_add_user_content(UserContent("Yes please"))
        out = await _alarm(api, turn_on="heated blanket", confirmed=True)
    made = entry.runtime_data.lists["alarms"].find("Test")
    assert out["set"] == "Test" and made["made_by"] == "voice"


async def test_checked_again_when_it_goes_off(hass: HomeAssistant) -> None:
    entry, api = await _setup(hass)
    with chat_session.async_get_chat_session(hass) as session, async_get_chat_log(hass, session) as chat:
        chat.async_add_user_content(UserContent("Schedule the heated blanket"))
        await _alarm(api, turn_on="heated blanket")
        chat.async_add_user_content(UserContent("Yes"))
        await _alarm(api, turn_on="heated blanket", confirmed=True)
    rt = entry.runtime_data
    alarm = rt.lists["alarms"].find("Test")
    on = async_mock_service(hass, "switch", "turn_on")
    async_expose_entity(hass, "conversation", "switch.heated_blanket", False)  # no longer exposed
    await rt.alarms.async_ring(alarm)
    await hass.async_block_till_done()
    assert not on and rt.timeline.since(1, ["health"], 1)[0]["text"] == "Didn't run: Test"
    async_expose_entity(hass, "conversation", "switch.heated_blanket", True)
    await rt.alarms.async_ring(rt.lists["alarms"].find("Test"))
    await hass.async_block_till_done()
    assert on  # made on the page or still allowed: runs as set


async def test_plain_spoken_alarm_needs_no_read_back(hass: HomeAssistant) -> None:
    entry, api = await _setup(hass)
    out = await api.async_call_tool(llm.ToolInput(tool_name="sentient_alarm", tool_args={
        "action": "set", "name": "Wake up", "time": "07:00", "days": "weekdays"}))
    out = getattr(out, "data", out)
    assert out["set"] == "Wake up" and out["kind"] == "alarm"


async def test_older_alarms_checked_until_saved_on_the_page(hass: HomeAssistant, hass_ws_client) -> None:
    """Saved before 0.88 (no made_by): checked like voice ones; saving it on the page keeps it as set."""
    entry, _api = await _setup(hass)
    rt = entry.runtime_data
    old = rt.lists["alarms"].upsert({"name": "Old", "time": "21:00:00", "days": ["mon"], "silent": True,
                                     "actions": [{"action": "script.turn_on", "target": {"entity_id": "script.open_everything"}}]})
    old.pop("made_by", None)
    scripts = async_mock_service(hass, "script", "turn_on")
    await rt.alarms.async_ring(old)
    await hass.async_block_till_done()
    assert not scripts
    assert "save it once" in rt.timeline.since(1, ["health"], 1)[0]["detail"]
    ws = await hass_ws_client(hass)
    await ws.send_json_auto_id({"type": "sentient/items/save", "kind": "schedules", "item": {**old}})
    assert (await ws.receive_json())["success"]
    saved = rt.lists["alarms"].find("Old")
    assert saved["made_by"] == "page"
    await rt.alarms.async_ring(saved)
    await hass.async_block_till_done()
    assert scripts  # an admin set it on the page: runs as set
