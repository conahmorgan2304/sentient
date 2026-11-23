"""Ongoing situations have a start, evidence, how sure, an ending — and are said as over once they are."""
from datetime import timedelta
from types import SimpleNamespace

from homeassistant.util import dt as dt_util

from custom_components.sentient.situations import SituationBook

HEAT = "climate.heating"


def _book(hass, **opts):
    rt = SimpleNamespace(brain=None, incidents=None, watchdog=None, health=None, sleep=None)
    options = {"heating_entities": [HEAT], **opts}
    return SituationBook(hass, lambda: options, lambda: rt), rt


def _heater(hass, current, target=20, action="heating"):
    hass.states.async_set(HEAT, "heat", {"friendly_name": "Heating", "temperature": target,
                                         "current_temperature": current, "hvac_action": action})


async def test_heating_struggling_starts_after_an_hour_and_ends_when_warm(hass):
    book, _ = _book(hass)
    t0 = dt_util.utcnow()
    _heater(hass, 17.0)
    book.update(t0)
    assert not book.items  # calling for heat, but not for long
    _heater(hass, 17.4)
    lines = book.lines(t0 + timedelta(minutes=70))
    assert len(lines) == 1 and lines[0].startswith("Going on since") and "struggling to reach 20°" in lines[0]
    assert "up 0.4°" in lines[0] and "Ends when" in lines[0]
    started = book.items[f"heating:{HEAT}"]["started"]
    _heater(hass, 17.5)
    book.update(t0 + timedelta(minutes=80))
    assert book.items[f"heating:{HEAT}"]["started"] == started  # still the same situation, not a new one
    _heater(hass, 19.8)
    lines = book.lines(t0 + timedelta(minutes=95))
    assert lines[0].startswith("Over since") and "struggling" in lines[0]
    assert not book.lines(t0 + timedelta(minutes=95 + 61))  # forgotten an hour after it ended


async def test_heating_warming_up_properly_isnt_struggling(hass):
    book, _ = _book(hass)
    t0 = dt_util.utcnow()
    _heater(hass, 16.0)
    book.update(t0)
    _heater(hass, 18.5)
    assert not book.lines(t0 + timedelta(minutes=70))


async def test_arriving_home_then_over(hass):
    book, _ = _book(hass)
    hass.states.async_set("person.alex", "home", {"friendly_name": "Alex"})
    now = dt_util.utcnow()
    assert "Alex arriving home" in book.lines(now)[0]
    assert book.lines(now + timedelta(minutes=25))[0].startswith("Over since")


async def test_busy_garden_from_the_ledger(hass):
    rows = []
    ledger = SimpleNamespace(recent_rows=lambda since: [r for r in rows if r["ts"] >= since])
    book, rt = _book(hass, camera_security_inside=["binary_sensor.garden_person"])
    rt.brain = SimpleNamespace(ledger=ledger)
    now = dt_util.utcnow()
    for minutes in (20, 12, 4):
        rows.append({"entity": "binary_sensor.garden_person", "new": "on", "name": "Garden person",
                     "ts": (now - timedelta(minutes=minutes)).timestamp()})
    line = book.lines(now)[0]
    assert "garden's busy" in line and "3 detections" in line and "shadow" in line
    rows.append({"entity": "binary_sensor.other", "new": "on", "ts": now.timestamp()})
    assert book.lines(now + timedelta(minutes=31))[0].startswith("Over since")


async def test_device_that_keeps_failing_and_comes_back(hass):
    book, rt = _book(hass)
    times = [(dt_util.utcnow() - timedelta(days=d)).isoformat() for d in (5, 3, 0)]
    rt.watchdog = SimpleNamespace(drops={"Hallway tablet": times})
    rt.health = SimpleNamespace(all_issues=[{"kind": "offline", "name": "Hallway tablet"}])
    assert "Hallway tablet keeps dropping offline" in book.lines()[0]
    rt.health.all_issues = []
    assert book.lines()[0].startswith("Over since")


async def test_one_broken_detector_doesnt_stop_the_rest(hass):
    book, rt = _book(hass)
    rt.watchdog = SimpleNamespace(drops=None)  # broken
    rt.health = SimpleNamespace(all_issues=[{"kind": "offline", "name": "x"}])
    rt.sleep = SimpleNamespace(asleep=True, since=(dt_util.utcnow() - timedelta(hours=1)).isoformat())
    assert any("The house is asleep" in line for line in book.lines())


async def test_voice_is_told_whats_over(hass):
    """End to end: the voice prompt carries an ended situation as over, not as news."""
    from homeassistant.core import Context
    from homeassistant.helpers import llm

    from tests.test_heating_programmes import _setup

    entry = await _setup(hass, house_ledger=True, situations=True)
    rt = entry.runtime_data
    assert rt.brain is not None
    now = dt_util.utcnow()
    rt.brain.book.items["heating:climate.lounge"] = {
        "kind": "heating struggling", "title": "Lounge struggling to reach 20°", "started": now - timedelta(hours=2),
        "evidence": ["x"], "sure": "y", "ends_when": "z", "ended": now - timedelta(minutes=10)}
    api = await llm.async_get_api(hass, "sentient", llm.LLMContext(
        platform="test", context=Context(), language="en", assistant="conversation", device_id=None))
    prompt = api.api_prompt
    assert "Over since" in prompt and "Lounge struggling to reach 20°" in prompt and "something over is over" in prompt
