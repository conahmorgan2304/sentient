"""A readable copy of what Sentient knows and does, with secrets left out."""

from homeassistant.core import HomeAssistant
from pytest_homeassistant_custom_component.common import MockConfigEntry

from custom_components.sentient.const import DOMAIN

OPTIONS = {"persona": "Nova", "quiet_start": "21:00:00", "quiet_end": "07:00:00",
           "tandoor_token": "SECRET-TOKEN", "searxng_url": "http://10.0.0.5:8080"}


async def test_readable_copy(hass: HomeAssistant, hass_ws_client) -> None:
    entry = MockConfigEntry(domain=DOMAIN, title="Nova", unique_id=DOMAIN, data=OPTIONS)
    entry.add_to_hass(hass)
    assert await hass.config_entries.async_setup(entry.entry_id)
    await hass.async_block_till_done()
    rt = entry.runtime_data
    m = rt.memory.add("The bins go out on Tuesday nights.")
    rt.memory.pin(m.id, True)
    rt.lists["maintenance"].upsert({"name": "Purifier filter", "every_hours": 500})
    client = await hass_ws_client(hass)
    await client.send_json_auto_id({"type": "sentient/export/readable"})
    text = (await client.receive_json())["result"]["text"]
    assert text.startswith("# Nova — what it knows and does")
    assert "- The bins go out on Tuesday nights. *(pinned)*" in text
    assert "**Purifier filter** — 0 of 500 h" in text
    assert "SECRET-TOKEN" not in text and "10.0.0.5" not in text
    assert "21:00:00" in text
