"""What's new after an update: only releases since the one last seen; nothing on a fresh install."""

from homeassistant.core import HomeAssistant
from pytest_homeassistant_custom_component.common import MockConfigEntry

from custom_components.sentient.const import DOMAIN

OPTIONS = {"persona": "Nova", "quiet_start": "00:00:00", "quiet_end": "00:00:00"}


async def test_whats_new(hass: HomeAssistant, hass_ws_client) -> None:
    entry = MockConfigEntry(domain=DOMAIN, title="Nova", unique_id=DOMAIN, data=OPTIONS)
    entry.add_to_hass(hass)
    assert await hass.config_entries.async_setup(entry.entry_id)
    await hass.async_block_till_done()
    wn = entry.runtime_data.whats_new
    assert wn.listing()["releases"] == []  # a fresh install has nothing to catch up on
    wn.seen, wn.version = "0.84.0", "0.86.0"  # updated from 0.84 to 0.86
    client = await hass_ws_client(hass)
    await client.send_json_auto_id({"type": "sentient/whats_new"})
    out = (await client.receive_json())["result"]
    assert [r["version"] for r in out["releases"]] == ["0.86.0", "0.85.0"]
    await client.send_json_auto_id({"type": "sentient/whats_new/seen"})
    assert (await client.receive_json())["success"]
    assert wn.listing()["releases"] == []
    wn.seen = "0.84.0"
    hass.config_entries.async_update_entry(entry, options={**OPTIONS, "whats_new": False})
    await hass.async_block_till_done()
    assert wn.listing()["releases"] == []
