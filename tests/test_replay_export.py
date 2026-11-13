"""A recorded day for the replay benchmark: the ledger's changes with their causes, the devices' states and rooms,
the settings without secrets, and Sentient's decisions in one shape."""
from homeassistant.core import Context, HomeAssistant
from homeassistant.helpers import area_registry as ar, entity_registry as er
from pytest_homeassistant_custom_component.common import MockConfigEntry

from custom_components.sentient.const import DOMAIN
from custom_components.sentient.replay import decision_record


async def test_a_day_is_exported_with_causes_rooms_and_decisions(hass: HomeAssistant, hass_ws_client) -> None:
    kitchen = ar.async_get(hass).async_create("Kitchen")
    reg = er.async_get(hass)
    reg.async_get_or_create("light", "test", "k_light", suggested_object_id="kitchen")
    reg.async_update_entity("light.kitchen", area_id=kitchen.id)
    hass.states.async_set("light.kitchen", "off", {"friendly_name": "Kitchen light"})
    entry = MockConfigEntry(domain=DOMAIN, title="Nova", unique_id=DOMAIN, data={
        "persona": "Nova", "speakers": [], "house_ledger": True, "tandoor_token": "s3cret", "tandoor_url": "http://x"})
    entry.add_to_hass(hass)
    assert await hass.config_entries.async_setup(entry.entry_id)
    await hass.async_block_till_done()
    hass.states.async_set("light.kitchen", "on", {"friendly_name": "Kitchen light"}, context=Context())
    await hass.async_block_till_done()
    entry.runtime_data.timeline.add("held", "The kitchen light is on.", "held back: quiet hours", reason="quiet_hours",
                                    source="notice")
    ws = await hass_ws_client(hass)
    await ws.send_json({"id": 1, "type": "sentient/replay/export", "hours": 2})
    day = (await ws.receive_json())["result"]
    assert day["format"] == 1
    assert [c["entity"] for c in day["changes"]] == ["light.kitchen"] and day["changes"][0]["cause"] == "device"
    assert day["states"]["light.kitchen"]["state"] == "off"  # as it was when the period began
    assert day["areas"][day["entities"]["light.kitchen"]["area"]]["name"] == "Kitchen"
    assert "tandoor_token" not in day["options"] and "tandoor_url" not in day["options"]
    assert day["decisions"][-1]["action"] == "held" and day["decisions"][-1]["reason"] == "quiet_hours"


def test_every_timeline_decision_reads_the_same_way() -> None:
    said = decision_record({"time": "t", "kind": "said", "text": "Hi", "source": "briefing:up", "priority": "normal",
                            "why": "the briefing was due", "phoned": "Announcement"})
    assert said["action"] == "spoke" and said["phoned"] and said["source"] == "briefing:up"
    health = decision_record({"time": "t", "kind": "health", "text": "x", "decision": {
        "kind": "unexplained_change", "evidence": ["a"], "surprise": 0.7, "ai_used": False}})
    assert health["action"] == "noted" and health["source"] == "unexplained_change" and health["surprise"] == 0.7
