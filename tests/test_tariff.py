"""Cheap-electricity suggestions: asked once per appliance, then a nudge on evenings it hasn't run."""

from datetime import timedelta

from homeassistant.core import HomeAssistant
from homeassistant.util import dt as dt_util
from pytest_homeassistant_custom_component.common import MockConfigEntry, async_mock_service

from custom_components.sentient.const import DOMAIN
from custom_components.sentient.tariff import cheapest

OPTIONS = {"persona": "Nova", "speakers": ["assist_satellite.kitchen"], "default_speaker": "assist_satellite.kitchen",
           "quiet_start": "00:00:00", "quiet_end": "00:00:00", "price_entity": "sensor.price",
           "appliances": ["sensor.dishwasher_power"]}


def _rates(evening):
    """Half-hourly: 30p, but 7.5p from 1am to 5am."""
    out = []
    for i in range(0, 36):
        t = evening + timedelta(minutes=30 * i)
        cheap = 1 <= dt_util.as_local(t).hour < 5
        out.append({"start": t.isoformat(), "end": (t + timedelta(minutes=30)).isoformat(), "value_inc_vat": 0.075 if cheap else 0.30})
    return out


def test_cheapest_window() -> None:
    evening = dt_util.start_of_local_day() + timedelta(hours=19, minutes=30)
    rates = [(dt_util.parse_datetime(r["start"]), r["value_inc_vat"]) for r in _rates(evening)]
    start, price = cheapest(rates, evening)
    assert dt_util.as_local(start).hour == 1 and price == 0.075


async def test_ask_once_then_nudge(hass: HomeAssistant, freezer) -> None:
    evening = dt_util.start_of_local_day() + timedelta(hours=19, minutes=30)
    freezer.move_to(evening)
    hass.states.async_set("assist_satellite.kitchen", "idle", {"friendly_name": "Kitchen", "supported_features": 3})
    hass.states.async_set("sensor.price", "0.30", {"friendly_name": "Price", "rates": _rates(evening)})
    hass.states.async_set("sensor.dishwasher_power", "0", {"friendly_name": "Dishwasher power", "unit_of_measurement": "W"})
    entry = MockConfigEntry(domain=DOMAIN, title="Nova", unique_id=DOMAIN, data=OPTIONS)
    entry.add_to_hass(hass)
    assert await hass.config_entries.async_setup(entry.entry_id)
    await hass.async_block_till_done()
    rt = entry.runtime_data
    rt.tracker.best_area = lambda: "kitchen"
    asked = async_mock_service(hass, "assist_satellite", "start_conversation")
    said = async_mock_service(hass, "assist_satellite", "announce")
    out = await rt.cheap_power.async_ask()
    assert out["asked"].startswith("Electricity's about 75% cheaper from 1am")
    assert "dishwasher" in asked[0].data["start_message"]
    assert await rt.speech.async_take_answer("assist_satellite.kitchen", "yes") == "Okay, I'll mention it in the evenings."
    assert await rt.cheap_power.async_ask() is None  # once per appliance
    freezer.tick(timedelta(hours=1))
    nudged = await rt.cheap_power.async_nudge()
    assert nudged and "from 1am tonight" in said[-1].data["message"]
    # without a time-of-use tariff (one flat price) it never asks
    hass.states.async_set("sensor.price", "0.25", {"friendly_name": "Price"})
    assert rt.cheap_power.tonight() is None
