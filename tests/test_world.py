"""Outside information in the briefing; a parcel out for delivery watched for all day."""

from homeassistant.core import HomeAssistant
from pytest_homeassistant_custom_component.common import MockConfigEntry, async_mock_service

from custom_components.sentient.const import DOMAIN
from custom_components.sentient.upkeep import gather_briefing, plain_briefing

OPTIONS = {"persona": "Nova", "speakers": ["assist_satellite.kitchen"], "default_speaker": "assist_satellite.kitchen",
           "quiet_start": "00:00:00", "quiet_end": "00:00:00",
           "outside_info": ["sensor.price", "sensor.parcels", "binary_sensor.wind_warning", "sensor.gone"],
           "parcel_trackers": ["sensor.royal_mail"]}


async def _setup(hass: HomeAssistant) -> MockConfigEntry:
    hass.states.async_set("assist_satellite.kitchen", "idle", {"friendly_name": "Kitchen"})
    hass.states.async_set("sensor.price", "0.21", {"friendly_name": "Electricity price", "unit_of_measurement": "GBP/kWh"})
    hass.states.async_set("sensor.parcels", "0", {"friendly_name": "Parcels"})
    hass.states.async_set("binary_sensor.wind_warning", "on", {"friendly_name": "Yellow wind warning"})
    hass.states.async_set("sensor.gone", "unavailable", {"friendly_name": "Gone"})
    hass.states.async_set("sensor.royal_mail", "In transit", {"friendly_name": "Royal Mail"})
    entry = MockConfigEntry(domain=DOMAIN, title="Nova", unique_id=DOMAIN, data=OPTIONS)
    entry.add_to_hass(hass)
    assert await hass.config_entries.async_setup(entry.entry_id)
    await hass.async_block_till_done()
    return entry


async def test_outside_information_in_the_briefing(hass: HomeAssistant) -> None:
    entry = await _setup(hass)
    facts = await gather_briefing(hass, entry.runtime_data)
    assert facts["outside_information"] == ["Electricity price: 0.21 GBP/kWh", "Yellow wind warning: yes"]
    assert "Electricity price: 0.21 GBP/kWh" in plain_briefing(facts)


async def test_parcel_out_for_delivery_then_delivered(hass: HomeAssistant) -> None:
    entry = await _setup(hass)
    rt = entry.runtime_data
    said = async_mock_service(hass, "assist_satellite", "announce")
    assert not rt.visitors.expecting()
    hass.states.async_set("sensor.royal_mail", "Out for delivery", {"friendly_name": "Royal Mail"})
    await hass.async_block_till_done()
    assert rt.visitors.expecting() and "out for delivery today" in said[-1].data["message"]
    hass.states.async_set("sensor.royal_mail", "Out for delivery", {"friendly_name": "Royal Mail", "x": 1})
    await hass.async_block_till_done()
    assert len(said) == 1  # once a day
    assert (await gather_briefing(hass, rt))["parcel_expected_today"]
    hass.states.async_set("sensor.royal_mail", "Delivered", {"friendly_name": "Royal Mail"})
    await hass.async_block_till_done()
    assert "been delivered" in said[-1].data["message"] and not rt.visitors.expecting()


async def test_a_list_of_old_deliveries_isnt_news(hass: HomeAssistant) -> None:
    entry = await _setup(hass)
    said = async_mock_service(hass, "assist_satellite", "announce")
    hass.states.async_set("sensor.royal_mail", "2", {"friendly_name": "Royal Mail",
                                                     "packages": [{"status": "Delivered"}, {"status": "Delivered"}]})
    await hass.async_block_till_done()
    assert not said and not entry.runtime_data.visitors.expecting()
