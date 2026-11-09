"""A light that movement flicks on and off all day is not a routine."""
from datetime import datetime, timedelta

from homeassistant.core import State
from homeassistant.util import dt as dt_util

from custom_components.sentient.learning import find_routines, is_flappy


def _hist(per_day, gap_min, days=14, start_hour=17, jitter=False):
    now = dt_util.now().replace(hour=23, minute=0, second=0, microsecond=0)
    out = []
    for d in range(days, 0, -1):
        day = now - timedelta(days=d)
        for i in range(per_day):
            on = day.replace(hour=start_hour) + timedelta(minutes=i * 47 + (d * 7 % 13 if jitter else 0))
            out.append(State("light.hall", "on", last_changed=on, last_updated=on))
            off = on + timedelta(minutes=gap_min)
            out.append(State("light.hall", "off", last_changed=off, last_updated=off))
    return out


def test_motion_driven_light_is_flappy_and_not_a_routine():
    states = _hist(8, 2)
    assert is_flappy(states, "on")
    assert find_routines("light.hall", "hall light", states) == []


def test_short_cycles_a_few_times_a_day_are_flappy():
    assert is_flappy(_hist(4, 4), "on")


def test_one_deliberate_switch_a_day_is_still_a_routine():
    states = _hist(1, 240, jitter=True)
    assert not is_flappy(states, "on")
    assert find_routines("light.hall", "hall light", states)
