"""Phone notifications go through the app with a tap link to that moment on the timeline; others as before."""

from homeassistant.core import HomeAssistant
from homeassistant.helpers import device_registry as dr, entity_registry as er
from pytest_homeassistant_custom_component.common import MockConfigEntry, async_mock_service

from custom_components.sentient.const import DOMAIN


async def test_tap_opens_the_timeline_and_it_is_marked(hass: HomeAssistant) -> None:
    app = MockConfigEntry(domain="mobile_app")
    app.add_to_hass(hass)
    dev = dr.async_get(hass).async_get_or_create(config_entry_id=app.entry_id, identifiers={("mobile_app", "x")},
                                                 name="Sam's iPhone")
    er.async_get(hass).async_get_or_create("notify", "mobile_app", "x", suggested_object_id="sams_iphone",
                                           device_id=dev.id, config_entry=app)
    hass.states.async_set("notify.sams_iphone", "unknown")
    hass.states.async_set("notify.kitchen_display", "unknown")
    pushed = async_mock_service(hass, "notify", "mobile_app_sams_iphone")
    plain = async_mock_service(hass, "notify", "send_message")
    entry = MockConfigEntry(domain=DOMAIN, title="Nova", unique_id=DOMAIN, data={
        "persona": "Nova", "quiet_start": "00:00:00", "quiet_end": "00:00:00",
        "notify_targets": ["notify.sams_iphone", "notify.kitchen_display"], "notify_when": ["health"]})
    entry.add_to_hass(hass)
    assert await hass.config_entries.async_setup(entry.entry_id)
    await hass.async_block_till_done()
    rt = entry.runtime_data
    rt.timeline.add("health", "The back door sensor's battery is low.")
    await hass.async_block_till_done()
    item = rt.timeline.items[0]
    assert pushed[0].data["title"] == "Nova · House health"
    assert pushed[0].data["message"] == "The back door sensor's battery is low."
    link = pushed[0].data["data"]["url"]
    assert link.startswith("/sentient/timeline?item=") and pushed[0].data["data"]["clickAction"] == link
    assert plain[0].data["entity_id"] == ["notify.kitchen_display"]  # not the app: as before
    assert item["phoned"] == "House health"
