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


def test_nobody_at_the_door_is_not_sent_to_phones() -> None:
    """Live 13 Nov: "empty street view" and "nobody at the door" were sent as At the door."""
    from custom_components.sentient.notifier import wanted

    assert wanted({"kind": "visitor", "detail": "nobody", "text": "empty street view"}, {"visitors"}) is None
    assert wanted({"kind": "visitor", "detail": "delivery", "text": "a driver"}, {"visitors"}) == "At the door"
    assert wanted({"kind": "visitor", "detail": "nobody", "parcel": True}, {"visitors"}) == "Parcel left"


def test_good_news_stays_on_the_timeline_not_the_phone() -> None:
    """Live 13 Nov 12:44: four "fine again" House health notifications in one second after a restart."""
    from custom_components.sentient.notifier import wanted

    assert wanted({"kind": "health", "text": "Upstairs Assist satellite is fine again", "recovered": True}, {"health"}) is None
    assert wanted({"kind": "health", "text": "gate Battery is at 5%"}, {"health"}) == "House health"


async def test_a_security_alert_is_updated_in_place_after_the_picture_check(hass: HomeAssistant) -> None:
    """Live 13 Nov 18:18: "person in the garden" went to the phone; the picture check found nobody, and the phone never heard."""
    entry = MockConfigEntry(domain=DOMAIN, title="Nova", unique_id=DOMAIN,
                            data={"persona": "Nova", "speakers": [], "notify_targets": ["notify.mobile_app_phone"],
                                  "phone_apps": ["notify.mobile_app_phone"], "notify_when": ["security"]})
    entry.add_to_hass(hass)
    hass.states.async_set("notify.mobile_app_phone", "unknown")
    sent = async_mock_service(hass, "notify", "mobile_app_phone")
    assert await hass.config_entries.async_setup(entry.entry_id)
    await hass.async_block_till_done()
    rt = entry.runtime_data
    item = rt.timeline.add("security", "A person was reported in the garden (18:18).", "camera security", alert=True)
    await hass.async_block_till_done()
    rt.timeline.mark(item, phoned="Night watch")  # what the runtime records once it's sent
    rt.notifier.follow_up(item, "Checked the picture: no one visible, so possibly a false alarm (a picture can miss someone).")
    await hass.async_block_till_done()
    calls = sent
    first, update = calls[0].data, calls[-1].data
    assert first["data"]["tag"] == update["data"]["tag"]  # the same notification, replaced
    assert update["data"]["alert_once"] is True and update["message"].endswith("(a picture can miss someone).")
    assert update["message"].startswith("A person was reported in the garden")
    rt.notifier.follow_up({"text": "never sent"}, "Checked the picture.")  # not phoned: nothing to update
    await hass.async_block_till_done()
    assert len(sent) == len(calls)
