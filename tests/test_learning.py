"""Learning routines from history."""

from datetime import datetime, timedelta
import random
from unittest.mock import patch

from homeassistant.core import HomeAssistant, State
from homeassistant.helpers import llm
from homeassistant.util import dt as dt_util
from pytest_homeassistant_custom_component.common import (
    MockConfigEntry,
    async_fire_time_changed,
    async_mock_service,
)

from custom_components.sentient.const import DOMAIN
from custom_components.sentient.learning import find_routines

TODAY = datetime(2026, 10, 21, 12, 0)  # a Wednesday


def _local(day: datetime, hour: int, minute: int) -> datetime:
    return day.replace(hour=hour, minute=minute, tzinfo=dt_util.get_default_time_zone())


def _history(entity_id: str, on_times: dict[int, tuple[int, int]], off=(23, 30)) -> list[State]:
    """on_times: days-ago -> (hour, minute) the light went on that day."""
    states: list[State] = []
    for ago in sorted(range(1, 15), reverse=True):
        day = TODAY - timedelta(days=ago)
        if ago in on_times:
            h, m = on_times[ago]
            states.append(State(entity_id, "on", last_changed=_local(day, h, m)))
        states.append(State(entity_id, "off", last_changed=_local(day, *off)))
    return states


def _weekdays_ago() -> list[int]:
    return [a for a in range(1, 15) if (TODAY - timedelta(days=a)).weekday() < 5]


def test_finds_a_weekday_habit() -> None:
    rnd = random.Random(4)
    times = {a: (17, 30 + rnd.randint(-10, 10)) for a in _weekdays_ago()}
    found = find_routines("light.hallway", "hallway light", _history("light.hallway", times), TODAY)
    on = [r for r in found if r.state == "on"]
    assert len(on) == 1
    r = on[0]
    assert r.days == "on weekdays" and abs(r.minute - (17 * 60 + 30)) <= 10
    assert r.describe().startswith("The hallway light usually goes on around 5:")
    assert r.describe().endswith("on weekdays.")


def test_ignores_what_an_automation_already_does() -> None:
    times = {a: (7, 0) for a in range(1, 15)}  # to the minute, every single day
    found = find_routines("light.porch", "porch light", _history("light.porch", times, off=(7, 45)), TODAY)
    assert not [r for r in found if r.state == "on"]


def test_changes_made_by_automations_are_left_out() -> None:
    rnd = random.Random(9)
    times = {a: (18, 30 + rnd.randint(-8, 8)) for a in range(1, 15)}
    states = _history("light.bedroom", times)
    automated = {round(s.last_changed.timestamp()) for s in states if s.state == "on"}
    assert not [r for r in find_routines("light.bedroom", "bedroom light", states, TODAY, automated) if r.state == "on"]
    # without the logbook's help it would look like a habit
    assert [r for r in find_routines("light.bedroom", "bedroom light", states, TODAY) if r.state == "on"]


def test_random_use_is_not_a_routine() -> None:
    rnd = random.Random(1)
    times = {a: (rnd.randint(8, 22), rnd.randint(0, 59)) for a in range(1, 15)}
    found = find_routines("light.lamp", "lamp", _history("light.lamp", times, off=(23, 59)), TODAY)
    assert not [r for r in found if r.state == "on"]


async def test_learn_share_adopt_and_do(hass: HomeAssistant, freezer) -> None:
    freezer.move_to(dt_util.as_utc(_local(TODAY, 12, 0)))
    rnd = random.Random(7)
    history = {"light.hallway": _history("light.hallway", {a: (17, 30 + rnd.randint(-5, 5)) for a in _weekdays_ago()})}
    hass.states.async_set("light.hallway", "off", {"friendly_name": "Hallway light"})
    hass.states.async_set("assist_satellite.kitchen", "idle", {"supported_features": 3})
    entry = MockConfigEntry(domain=DOMAIN, title="Nova", unique_id=DOMAIN, data={
        "persona": "Nova", "speakers": ["assist_satellite.kitchen"], "default_speaker": "assist_satellite.kitchen",
        "quiet_start": "00:00:00", "quiet_end": "00:00:00", "learn_entities": ["light.hallway"],
        "suggest_routines": False,
    })
    entry.add_to_hass(hass)
    with patch("custom_components.sentient.learning.Learner._async_history", return_value=history):
        assert await hass.config_entries.async_setup(entry.entry_id)
        await hass.async_block_till_done()
        res = await hass.services.async_call(DOMAIN, "learn", {}, blocking=True, return_response=True)
    assert len(res["routines"]) >= 1
    sensor = hass.states.get("sensor.nova_routines")
    assert int(sensor.state) >= 1 and sensor.attributes["routines"][0]["adopted"] is False

    ctx = llm.LLMContext(platform="test", context=None, language="en", assistant="conversation", device_id=None)
    api = await llm.async_get_api(hass, "sentient", ctx)
    assert "hallway light usually goes on around 5:" in api.api_prompt
    out = await api.async_call_tool(llm.ToolInput(tool_name="sentient_adopt_routine", tool_args={"routine": "hallway light on"}))
    assert out["house_does_it"] is True

    res = await hass.services.async_call(DOMAIN, "adopt_routine", {"routine": "hallway light on"}, blocking=True, return_response=True)
    assert res["house_does_it"] is False  # toggled off
    res = await hass.services.async_call(DOMAIN, "adopt_routine", {"routine": "hallway light on", "adopt": True}, blocking=True, return_response=True)
    assert res["house_does_it"] is True
    turned_on = async_mock_service(hass, "light", "turn_on")
    routine = next(r for r in entry.runtime_data.learner.routines.values() if r.state == "on")
    hour, minute = divmod(routine.minute, 60)
    target = _local(TODAY, hour, minute)
    while not routine_day_ok(routine, target):
        target += timedelta(days=1)
    freezer.move_to(dt_util.as_utc(target))
    async_fire_time_changed(hass, dt_util.as_utc(target))
    await hass.async_block_till_done()
    assert turned_on and turned_on[-1].data["entity_id"] in ("light.hallway", ["light.hallway"])


def routine_day_ok(routine, when: datetime) -> bool:
    from custom_components.sentient.learning import _DAYTYPES

    return _DAYTYPES[routine.days](when.date())


async def test_offers_once_when_someone_is_home(hass: HomeAssistant, freezer) -> None:
    freezer.move_to(dt_util.as_utc(_local(TODAY, 10, 14)))
    hass.states.async_set("light.hallway", "off", {"friendly_name": "Hallway light"})
    hass.states.async_set("assist_satellite.kitchen", "idle", {"supported_features": 3})
    entry = MockConfigEntry(domain=DOMAIN, title="Nova", unique_id=DOMAIN, data={
        "persona": "Nova", "speakers": ["assist_satellite.kitchen"], "default_speaker": "assist_satellite.kitchen",
        "quiet_start": "00:00:00", "quiet_end": "00:00:00", "learn_entities": ["light.hallway"],
    })
    entry.add_to_hass(hass)
    rnd = random.Random(2)
    history = {"light.hallway": _history("light.hallway", {a: (17, 30 + rnd.randint(-5, 5)) for a in range(1, 15)})}
    with patch("custom_components.sentient.learning.Learner._async_history", return_value=history):
        assert await hass.config_entries.async_setup(entry.entry_id)
        await hass.async_block_till_done()
        await entry.runtime_data.learner.async_learn()
    convo = async_mock_service(hass, "assist_satellite", "start_conversation")
    with patch.object(entry.runtime_data.tracker, "best_area", return_value="kitchen"):
        for minute in (15, 30):
            when = dt_util.as_utc(_local(TODAY, 10, minute))
            freezer.move_to(when)
            async_fire_time_changed(hass, when)
            await hass.async_block_till_done()
    assert len(convo) == 1
    msg = convo[0].data["start_message"]
    assert msg.startswith("I've noticed the hallway light usually goes on around 5:") and "from now on?" in msg
    assert "sentient_adopt_routine" in convo[0].data["extra_system_prompt"]


def _door_and_light(days: int = 10, lit_days: int = 8):
    door: list[State] = [State("binary_sensor.back_door", "off", last_changed=_local(TODAY - timedelta(days=15), 0, 0))]
    light: list[State] = [State("light.kitchen", "off", last_changed=_local(TODAY - timedelta(days=15), 0, 0))]
    for ago in range(days, 0, -1):
        day = TODAY - timedelta(days=ago)
        for h in (12, 21):
            door.append(State("binary_sensor.back_door", "on", last_changed=_local(day, h, 0)))
            door.append(State("binary_sensor.back_door", "off", last_changed=_local(day, h, 2)))
        if ago <= lit_days:
            light.append(State("light.kitchen", "on", last_changed=_local(day, 21, 0) + timedelta(seconds=30)))
            light.append(State("light.kitchen", "off", last_changed=_local(day, 23, 30)))
    return door, light


def test_finds_a_link_after_dark() -> None:
    from custom_components.sentient.learning import find_links

    door, light = _door_and_light()
    dark = lambda t: dt_util.as_local(t).hour >= 18 or dt_util.as_local(t).hour < 7  # noqa: E731
    found = find_links(
        {"binary_sensor.back_door": ("back door", "door", door), "light.lamp": ("lamp", None, [])},
        {"light.kitchen": ("kitchen light", light)},
        dark,
        today=_local(TODAY, 12, 0),
    )
    assert len(found) == 1
    link = found[0]
    assert link.dark and link.cause_state == "on" and link.state == "on" and link.seen == 8
    assert link.describe() == "When the back door opens after dark, the kitchen light usually goes on within a minute."
    # without darkness it isn't reliable enough (daytime openings don't light it)
    assert not find_links({"binary_sensor.back_door": ("back door", "door", door)},
                          {"light.kitchen": ("kitchen light", light)}, lambda t: False, today=_local(TODAY, 12, 0))


def test_rare_coincidence_is_not_a_link() -> None:
    from custom_components.sentient.learning import find_links

    door, light = _door_and_light(lit_days=3)
    found = find_links({"binary_sensor.back_door": ("back door", "door", door)},
                       {"light.kitchen": ("kitchen light", light)}, lambda t: True, today=_local(TODAY, 12, 0))
    assert not found


async def test_link_is_learned_offered_and_done(hass: HomeAssistant, freezer) -> None:
    freezer.move_to(dt_util.as_utc(_local(TODAY, 12, 0)))
    door, light = _door_and_light()
    hass.states.async_set("binary_sensor.back_door", "off", {"device_class": "door", "friendly_name": "Back door"})
    hass.states.async_set("light.kitchen", "off", {"friendly_name": "Kitchen light"})
    hass.states.async_set("assist_satellite.kitchen", "idle", {"supported_features": 3})
    entry = MockConfigEntry(domain=DOMAIN, title="Nova", unique_id=DOMAIN, data={
        "persona": "Nova", "speakers": ["assist_satellite.kitchen"], "default_speaker": "assist_satellite.kitchen",
        "quiet_start": "00:00:00", "quiet_end": "00:00:00", "learn_entities": ["light.kitchen"],
        "suggest_routines": False,
    })
    entry.add_to_hass(hass)
    history = {"binary_sensor.back_door": door, "light.kitchen": light}
    with patch("custom_components.sentient.learning.Learner._async_history", return_value=history), \
         patch("custom_components.sentient.learning.Learner.is_dark", lambda self, t: dt_util.as_local(t).hour >= 18):
        assert await hass.config_entries.async_setup(entry.entry_id)
        await hass.async_block_till_done()
        learner = entry.runtime_data.learner
        await learner.async_learn()
        assert len(learner.links) == 1
        link = next(iter(learner.links.values()))
        assert learner.next_to_offer() is link
        sensor = hass.states.get("sensor.nova_routines")
        assert any(r.get("kind") == "link" for r in sensor.attributes["routines"])
        ctx = llm.LLMContext(platform="test", context=None, language="en", assistant="conversation", device_id=None)
        api = await llm.async_get_api(hass, "sentient", ctx)
        assert "When the back door opens after dark" in api.api_prompt
        out = await api.async_call_tool(llm.ToolInput(tool_name="sentient_adopt_routine", tool_args={"routine": link.id}))
        assert out["house_does_it"] is True
        turned_on = async_mock_service(hass, "light", "turn_on")
        freezer.move_to(dt_util.as_utc(_local(TODAY, 13, 0)))
        hass.states.async_set("binary_sensor.back_door", "on", {"device_class": "door"})
        await hass.async_block_till_done()
        assert not turned_on  # daytime: left alone
        hass.states.async_set("binary_sensor.back_door", "off", {"device_class": "door"})
        freezer.move_to(dt_util.as_utc(_local(TODAY, 21, 0)))
        hass.states.async_set("binary_sensor.back_door", "on", {"device_class": "door"})
        await hass.async_block_till_done()
        assert len(turned_on) == 1
