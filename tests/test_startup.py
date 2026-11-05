"""Lighter start-up: optional parts that are off don't start, start when switched on, and start-up is timed."""

from homeassistant.core import HomeAssistant
from pytest_homeassistant_custom_component.common import MockConfigEntry

from custom_components.sentient.const import DOMAIN
from custom_components.sentient.diagnostics import async_get_config_entry_diagnostics

OPTIONS = {"persona": "Nova", "quiet_start": "00:00:00", "quiet_end": "00:00:00"}


async def test_dormant_until_switched_on(hass: HomeAssistant) -> None:
    hass.states.async_set("sensor.price", "0.3")
    entry = MockConfigEntry(domain=DOMAIN, title="Nova", unique_id=DOMAIN, data=OPTIONS)
    entry.add_to_hass(hass)
    assert await hass.config_entries.async_setup(entry.entry_id)
    await hass.async_block_till_done()
    rt = entry.runtime_data
    assert {"books", "cheap_power", "parcels", "rain"} <= rt.dormant
    assert rt.cheap_power._unsubs == []  # not listening
    hass.config_entries.async_update_entry(entry, options={**OPTIONS, "price_entity": "sensor.price"})
    await hass.async_block_till_done()
    assert "cheap_power" not in rt.dormant and rt.cheap_power._unsubs  # started without a restart
    diag = await async_get_config_entry_diagnostics(hass, entry)
    assert diag["startup"]["seconds"] >= 0 and "books" in diag["startup"]["not_started_while_off"]
