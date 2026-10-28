"""Self-test, rain warnings, the dusk hutch check and the night cat watch — each working when on and
doing nothing when off."""

from datetime import timedelta
from types import SimpleNamespace
from unittest.mock import patch

from homeassistant.core import HomeAssistant, ServiceCall, SupportsResponse
from homeassistant.util import dt as dt_util
from pytest_homeassistant_custom_component.common import MockConfigEntry, async_mock_service

from custom_components.sentient.const import DOMAIN
from custom_components.sentient.selftest import numbers

OPTIONS = {"persona": "Nova", "speakers": ["assist_satellite.kitchen"], "default_speaker": "assist_satellite.kitchen",
           "quiet_start": "00:00:00", "quiet_end": "00:00:00", "voice_agent": "conversation.ai"}


async def _setup(hass: HomeAssistant, **extra) -> MockConfigEntry:
    hass.states.async_set("assist_satellite.kitchen", "idle", {"supported_features": 3})
    entry = MockConfigEntry(domain=DOMAIN, title="Nova", unique_id=DOMAIN, data={**OPTIONS, **extra})
    entry.add_to_hass(hass)
    assert await hass.config_entries.async_setup(entry.entry_id)
    await hass.async_block_till_done()
    return entry


def _ai(hass: HomeAssistant, reply):
    calls: list[ServiceCall] = []

    async def generate(call: ServiceCall):
        calls.append(call)
        return {"conversation_id": "x", "data": reply}

    hass.services.async_register("ai_task", "generate_data", generate, supports_response=SupportsResponse.ONLY)
    return calls


def test_numbers_in_words_and_digits() -> None:
    assert numbers("It's twenty one degrees") == [21.0]
    assert numbers("about 12.6°, feels like eleven") == [12.6, 11.0]


async def test_self_test_finds_a_wrong_answer(hass: HomeAssistant) -> None:
    hass.states.async_set("weather.home", "rainy", {"temperature": 12.4})
    entry = await _setup(hass, self_test="nightly")
    replies = {
        "What's the temperature outside right now?": "It's about nineteen degrees outside.",
        "Is anything open?": "No, everything's shut.",
        "What reminders have I got?": "You haven't got any reminders.",
        "What time is it?": f"It's {dt_util.now():%H:%M}.",
    }

    async def converse(hass_, text, cid, context, **kw):
        return SimpleNamespace(response=SimpleNamespace(speech={"plain": {"speech": replies[text]}}), conversation_id=cid)

    with patch("homeassistant.components.conversation.async_converse", converse):
        out = await entry.runtime_data.selftest.async_run()
    wrong = [r for r in out["results"] if not r["right"]]
    assert [r["key"] for r in wrong] == ["outside"] and len(out["results"]) == 4
    issues = entry.runtime_data.health.async_check()
    assert any(i["kind"] == "selftest" and "nineteen" in i["text"] for i in issues)
    assert not entry.runtime_data.conversations.recent()  # not remembered as a conversation


async def test_self_test_off(hass: HomeAssistant) -> None:
    entry = await _setup(hass)
    assert "skipped" in await entry.runtime_data.selftest.async_run()
    assert entry.runtime_data.selftest.issues() == []


def _forecast(hass: HomeAssistant, condition: str):
    async def get(call: ServiceCall):
        soon = dt_util.utcnow().replace(minute=0, second=0, microsecond=0) + timedelta(hours=1)
        return {"weather.home": {"forecast": [{"datetime": soon.isoformat(), "condition": condition,
                                                "precipitation_probability": 80}]}}

    hass.services.async_register("weather", "get_forecasts", get, supports_response=SupportsResponse.ONLY)


async def test_rain_on_the_way_with_a_window_open(hass: HomeAssistant) -> None:
    hass.states.async_set("weather.home", "cloudy", {"temperature": 12})
    hass.states.async_set("binary_sensor.bedroom_window", "on", {"device_class": "window", "friendly_name": "Bedroom window"})
    entry = await _setup(hass, rain_warnings=True)
    said = async_mock_service(hass, "assist_satellite", "announce")
    _forecast(hass, "rainy")
    out = await entry.runtime_data.rain.async_check()
    assert out["rain"] and said[-1].data["message"] == "Rain's on the way in the next hour, and the bedroom window is open."
    assert "skipped" in await entry.runtime_data.rain.async_check()  # once per spell of rain


async def test_rain_off_or_dry_or_shut(hass: HomeAssistant) -> None:
    hass.states.async_set("weather.home", "cloudy", {"temperature": 12})
    hass.states.async_set("binary_sensor.bedroom_window", "on", {"device_class": "window", "friendly_name": "Bedroom window"})
    entry = await _setup(hass)
    said = async_mock_service(hass, "assist_satellite", "announce")
    _forecast(hass, "rainy")
    assert "skipped" in await entry.runtime_data.rain.async_check()  # off
    entry.runtime_data.rain.options = {**entry.runtime_data.rain.options, "rain_warnings": True}
    hass.states.async_set("binary_sensor.bedroom_window", "off", {"device_class": "window"})
    assert (await entry.runtime_data.rain.async_check())["open"] == []  # nothing open
    assert not said


async def test_hutch_check_and_cat_watch(hass: HomeAssistant) -> None:
    hass.states.async_set("camera.garden", "idle", {"friendly_name": "Garden"})
    hass.states.async_set("sun.sun", "below_horizon")
    hass.states.async_set("binary_sensor.garden_cat_occupancy", "off")
    entry = await _setup(hass, hutch_check=True, cat_watch=True, cat_sensors=["binary_sensor.garden_cat_occupancy"],
                         camera_prefilter=False)
    rt = entry.runtime_data
    rt.spots.set("camera.garden", "rabbit pen", [0.1, 0.4, 0.6, 0.9])
    said = async_mock_service(hass, "assist_satellite", "announce")
    _ai(hass, {"ok": False, "problem": "The hutch door looks open."})
    out = await rt.pets.async_hutch()
    assert out["ok"] is False and said[-1].data["message"] == "The hutch door looks open."
    _ai(hass, {"cat": True})
    hass.states.async_set("binary_sensor.garden_cat_occupancy", "on")
    await hass.async_block_till_done()
    await hass.async_block_till_done()
    assert said[-1].data["message"] == "There's a cat by the hutch."
    assert any("A cat was by the hutch" in i["text"] for i in rt.timeline.items)


async def test_hutch_and_cats_off(hass: HomeAssistant) -> None:
    hass.states.async_set("camera.garden", "idle")
    hass.states.async_set("sun.sun", "below_horizon")
    hass.states.async_set("binary_sensor.garden_cat_occupancy", "off")
    entry = await _setup(hass, cat_sensors=["binary_sensor.garden_cat_occupancy"])
    rt = entry.runtime_data
    rt.spots.set("camera.garden", "rabbit pen", [0.1, 0.4, 0.6, 0.9])
    ai = _ai(hass, {"cat": True})
    assert "skipped" in await rt.pets.async_hutch()
    hass.states.async_set("binary_sensor.garden_cat_occupancy", "on")
    await hass.async_block_till_done()
    assert not ai


def test_spoken_times() -> None:
    from custom_components.sentient.selftest import spoken_times

    assert spoken_times("It's twenty to twelve.") == [11 * 60 + 40]
    assert spoken_times("It's ten to twelve") == [11 * 60 + 50]
    assert spoken_times("half past three") == [3 * 60 + 30]
    assert spoken_times("It's 11:49") == [11 * 60 + 49]
    assert spoken_times("eleven forty-nine") == [11 * 60 + 49]
    assert spoken_times("three o'clock") == [180]
