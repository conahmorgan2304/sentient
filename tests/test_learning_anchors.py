"""Habits that follow the day: learned against sunset or getting up when that fits better than the clock."""

from datetime import datetime, timedelta

from homeassistant.core import State
from homeassistant.util import dt as dt_util

from custom_components.sentient.learning import Routine, find_routines

TODAY = datetime(2026, 10, 21, 12, 0)
JITTER = [-3, 2, 0, 4, -2, 1, -4, 3, 0, -1, 2, -3, 1, 4]


def _local(day: datetime, minute: int) -> datetime:
    return day.replace(hour=minute // 60, minute=minute % 60, tzinfo=dt_util.get_default_time_zone())


def _sunset(d) -> int:  # 6pm on TODAY, three minutes earlier each day that passes
    return 18 * 60 - 3 * (d - TODAY.date()).days


def _history(minute_on) -> list[State]:
    states = []
    for ago in range(14, 0, -1):
        day = TODAY - timedelta(days=ago)
        states.append(State("light.lamp", "on", last_changed=_local(day, minute_on(day.date(), ago))))
        states.append(State("light.lamp", "off", last_changed=_local(day, 23 * 60 + 30)))
    return states


def test_lamp_at_dusk_follows_sunset() -> None:
    states = _history(lambda d, ago: _sunset(d) + 10 + JITTER[ago - 1])
    [r] = [r for r in find_routines("light.lamp", "lamp", states, TODAY, anchors={"sunset": _sunset}) if r.state == "on"]
    assert r.anchor == "sunset" and 5 <= r.offset <= 15
    assert r.minute == _sunset(TODAY.date()) + r.offset
    assert "minutes after sunset" in r.describe() or "around sunset" in r.describe()
    # without anchors it's still a clock habit, just a looser one
    [c] = [r for r in find_routines("light.lamp", "lamp", states, TODAY) if r.state == "on"]
    assert c.anchor == "clock" and c.spread > r.spread


def test_clock_habit_stays_on_the_clock() -> None:
    states = _history(lambda d, ago: 18 * 60 + 30 + JITTER[ago - 1])
    [r] = [r for r in find_routines("light.lamp", "lamp", states, TODAY, anchors={"sunset": _sunset}) if r.state == "on"]
    assert r.anchor == "clock" and r.when == "6:30pm"


def test_kettle_after_getting_up() -> None:
    woke = {(TODAY - timedelta(days=a)).date(): 6 * 60 + 30 + (a * 37) % 90 for a in range(1, 15)}
    states = _history(lambda d, ago: woke[d] + 15 + JITTER[ago - 1] // 2)
    [r] = [r for r in find_routines("light.lamp", "lamp", states, TODAY, anchors={"getting up": woke.get}) if r.state == "on"]
    assert r.anchor == "getting up" and 10 <= r.offset <= 20
    assert r.describe().startswith("The lamp usually goes on about 15 minutes after getting up") or "after getting up" in r.describe()


def test_old_saved_routines_still_load() -> None:
    r = Routine(**{"entity_id": "light.lamp", "name": "lamp", "state": "on", "minute": 1110, "days": "every day",
                   "support": 0.8, "seen": 10, "spread": 5.0})
    assert r.anchor == "clock" and r.timing == "around 6:30pm"


async def test_adopted_routine_runs_at_todays_sunset(hass) -> None:
    from pytest_homeassistant_custom_component.common import MockConfigEntry

    from custom_components.sentient.const import DOMAIN

    entry = MockConfigEntry(domain=DOMAIN, title="Nova", unique_id=DOMAIN,
                            data={"persona": "Nova", "quiet_start": "00:00:00", "quiet_end": "00:00:00"})
    entry.add_to_hass(hass)
    assert await hass.config_entries.async_setup(entry.entry_id)
    await hass.async_block_till_done()
    learner = entry.runtime_data.learner
    day = dt_util.now().date()
    dusk = Routine("light.lamp", "lamp", "on", 0, "every day", 0.9, 12, 3.0, anchor="sunset", offset=10, adopted=True)
    sunset = learner._sun_minute("sunset", day)
    assert sunset is not None and learner.minute_today(dusk, day) == (sunset + 10) % 1440
    up = Routine("light.kettle", "kettle", "on", 420, "every day", 0.9, 12, 3.0, anchor="getting up", offset=15)
    assert learner.minute_today(up, day) is None  # nobody's got up yet today: not due
    learner.woke_on = lambda d: 400
    assert learner.minute_today(up, day) == 415
    hass.config_entries.async_update_entry(entry, options={"learn_anchors": False})
    await hass.async_block_till_done()
    assert learner.anchors() == {}
    assert learner.minute_today(up, day) == 420  # back to its last clock time


async def test_habits_done_together_offered_as_one(hass) -> None:
    from pytest_homeassistant_custom_component.common import MockConfigEntry, async_mock_service

    from custom_components.sentient.const import DOMAIN

    hass.states.async_set("assist_satellite.kitchen", "idle", {"supported_features": 0})
    entry = MockConfigEntry(domain=DOMAIN, title="Nova", unique_id=DOMAIN,
                            data={"persona": "Nova", "quiet_start": "00:00:00", "quiet_end": "00:00:00",
                                  "speakers": ["assist_satellite.kitchen"], "default_speaker": "assist_satellite.kitchen"})
    entry.add_to_hass(hass)
    assert await hass.config_entries.async_setup(entry.entry_id)
    await hass.async_block_till_done()
    learner = entry.runtime_data.learner
    learner.tracker = None
    days = [f"2026-10-{d:02d}" for d in range(7, 21)]
    lamp = Routine("light.lamp", "lamp", "on", 1200, "every day", 0.9, 12, 4.0, dates=days[:12])
    blinds = Routine("cover.blinds", "blinds", "closed", 1205, "every day", 0.85, 11, 4.0, dates=days[1:12])
    tv = Routine("switch.tv", "tv", "on", 1260, "every day", 0.8, 11, 4.0, dates=days[1:12])  # an hour later
    porch = Routine("light.porch", "porch light", "on", 1202, "every day", 0.7, 5, 4.0, dates=days[9:14])  # other days
    learner.routines = {r.id: r for r in (lamp, blinds, tv, porch)}
    assert [r.entity_id for r in learner.together(lamp)] == ["light.lamp", "cover.blinds"]
    said = async_mock_service(hass, "assist_satellite", "announce")
    await learner._async_offer(dt_util.now())
    await hass.async_block_till_done()
    text = said[0].data["message"]
    assert "around 8pm every day the lamp goes on and the blinds close — all of them on 11 days" in text
    assert "all of that together" in text and lamp.suggested and blinds.suggested and not tv.suggested
    hass.config_entries.async_update_entry(entry, options={**entry.data, "offer_together": False})
    await hass.async_block_till_done()
    assert learner.together(tv) == [tv]
