"""Phone notifications driven by the timeline."""

from homeassistant.core import HomeAssistant
from pytest_homeassistant_custom_component.common import MockConfigEntry, async_mock_service

from custom_components.sentient.const import DOMAIN, EVENT_HELD, EVENT_SPOKE, EVENT_WATCH_FIRED


async def test_chosen_kinds_go_to_phones(hass: HomeAssistant) -> None:
    entry = MockConfigEntry(
        domain=DOMAIN, title="Nova", unique_id=DOMAIN,
        data={"persona": "Nova", "speakers": [], "notify_targets": ["notify.phone"], "notify_when": ["urgent", "unheard"]},
    )
    entry.add_to_hass(hass)
    hass.states.async_set("notify.phone", "unknown")
    assert await hass.config_entries.async_setup(entry.entry_id)
    await hass.async_block_till_done()
    sent = async_mock_service(hass, "notify", "send_message")
    bus = hass.bus
    bus.async_fire(EVENT_SPOKE, {"message": "Normal chat.", "speakers": [], "priority": "normal"})
    bus.async_fire(EVENT_SPOKE, {"message": "Smoke alarm!", "speakers": [], "priority": "urgent"})
    bus.async_fire(EVENT_HELD, {"message": "Gate left open.", "reason": "no_speaker"})
    bus.async_fire(EVENT_HELD, {"message": "Gate left open.", "reason": "no_speaker"})  # repeat: once only
    bus.async_fire(EVENT_HELD, {"message": "Rain.", "reason": "quiet_hours"})  # not chosen
    bus.async_fire(EVENT_WATCH_FIRED, {"what": "washer", "message": "Washer done."})  # not chosen
    await hass.async_block_till_done()
    assert [(c.data["title"], c.data["message"]) for c in sent] == [
        ("Nova · Announcement", "Smoke alarm!"),
        ("Nova · Nobody heard this", "Gate left open."),
    ]
    assert sent[0].data["entity_id"] == ["notify.phone"]
