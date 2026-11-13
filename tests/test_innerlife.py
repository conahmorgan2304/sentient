"""Corrections, an unusual day, and next-room guesses."""
from datetime import datetime, timedelta
from types import SimpleNamespace
from unittest.mock import patch

from homeassistant.core import Context, State
from homeassistant.util import dt as dt_util

from custom_components.sentient.innerlife import InnerLife, bucket


class FakeOwn:
    def __init__(self):
        self.ids = {}

    def own_ctx(self, why):
        ctx = Context()
        self.ids[ctx.id] = why
        return ctx

    def is_own(self, ctx):
        return ctx is not None and ctx.id in self.ids

    def why(self, ctx):
        return self.ids.get(ctx.id)


class FakeTimeline:
    def __init__(self):
        self.items = []

    def add(self, kind, text, detail=None, **kw):
        self.items.append((kind, text))


def _event(state):
    return SimpleNamespace(data={"new_state": state})


def _inner(hass, rt=None, **opts):
    return InnerLife(hass, "t", opts, lambda: rt)


def _state(entity, value, ctx, when):
    return State(entity, value, {"friendly_name": "Landing light"}, context=ctx, last_changed=when, last_updated=when)


async def test_undone_routine_is_handed_back_after_three_corrections(hass):
    own, now = FakeOwn(), dt_util.utcnow()
    adopted = []
    routine = SimpleNamespace(id="r1", entity_id="light.landing", adopted=True, describe=lambda: "the landing light goes on around 18:00.")
    learner = SimpleNamespace(all=lambda: [routine], adopt=lambda rid, flag: adopted.append((rid, flag)))
    rt = SimpleNamespace(learner=learner, timeline=FakeTimeline())
    inner = _inner(hass, rt)
    with patch("custom_components.sentient.innerlife.own_actions", return_value=own):
        for i in range(3):
            t = now - timedelta(hours=i + 1)
            inner._on_state(_event(_state("light.landing", "on", own.own_ctx("learned routine: landing"), t)))
            inner._on_state(_event(_state("light.landing", "off", Context(), t + timedelta(minutes=2))))
    assert len(inner.corrections) == 3
    assert inner.evaluate_lessons()
    assert adopted == [("r1", False)]
    assert any("stopped doing" in t["text"] for t in inner.thoughts)
    assert rt.timeline.items and rt.timeline.items[0][0] == "mind"


async def test_motion_lights_and_automations_are_never_corrections(hass):
    own, now = FakeOwn(), dt_util.utcnow()
    inner = _inner(hass, SimpleNamespace(timeline=FakeTimeline()))
    with patch("custom_components.sentient.innerlife.own_actions", return_value=own):
        for _ in range(5):  # motion lights: the house puts the light on, a person switches it off
            inner._on_state(_event(_state("light.hall", "on", own.own_ctx("motion lights"), now)))
            inner._on_state(_event(_state("light.hall", "off", Context(), now + timedelta(seconds=30))))
        # an automation (has a parent) switching a routine's light back is not a person either
        inner._on_state(_event(_state("light.x", "on", own.own_ctx("alarm: x"), now)))
        inner._on_state(_event(_state("light.x", "off", Context(parent_id="abc"), now + timedelta(seconds=30))))
    assert inner.corrections == []


async def test_switched_off_learning_does_nothing(hass):
    own, now = FakeOwn(), dt_util.utcnow()
    inner = _inner(hass, None, learn_corrections=False)
    with patch("custom_components.sentient.innerlife.own_actions", return_value=own):
        inner._on_state(_event(_state("light.x", "on", own.own_ctx("learned routine: x"), now)))
        inner._on_state(_event(_state("light.x", "off", Context(), now + timedelta(seconds=30))))
    assert inner.corrections == []


async def test_nobody_up_yet_is_noted_once(hass):
    rt = SimpleNamespace(
        sleep=SimpleNamespace(enabled=True, asleep=True), heating=SimpleNamespace(wake_minute=lambda d: 6 * 60 + 30),
        away=SimpleNamespace(everyone_away=lambda: False), holiday=SimpleNamespace(active=False), timeline=FakeTimeline())
    inner = _inner(hass, rt)
    local = dt_util.now().replace(hour=9, minute=0)
    assert inner.check_day(local)
    assert inner.check_day(local)  # still true, but only one thought and one timeline line
    assert len(inner.thoughts) == 1 and len(rt.timeline.items) == 1
    rt.sleep.asleep = False
    assert not inner.check_day(local)
    rt.sleep.asleep, rt.away.everyone_away = True, (lambda: True)
    assert not inner.check_day(local)


async def test_next_room_is_guessed_then_scored(hass):
    inner = _inner(hass)
    inner._inside = {"binary_sensor.a", "binary_sensor.b"}
    inner._inside_at = 1e12
    base = dt_util.now().replace(hour=7, minute=0, second=0, microsecond=0)
    areas = {"binary_sensor.a": "kitchen", "binary_sensor.b": "lounge"}
    with patch("custom_components.sentient.presence.area_of", side_effect=lambda h, e: areas[e]):
        t = base.timestamp()
        for i in range(20):  # kitchen then lounge, again and again
            inner._watch_rooms("binary_sensor.a", t)
            inner._watch_rooms("binary_sensor.b", t + 60)
            t += 360
    p = inner.prediction()
    assert p["tried"] >= 5 and p["rate"] == 1.0
    assert inner.transitions[f"{bucket(base)}|kitchen"]["lounge"] >= 5


async def test_a_flickering_sensor_is_not_a_journey(hass):
    inner = _inner(hass)
    inner._inside = {"binary_sensor.a", "binary_sensor.b"}
    inner._inside_at = 1e12
    areas = {"binary_sensor.a": "kitchen", "binary_sensor.b": "lounge"}
    with patch("custom_components.sentient.presence.area_of", side_effect=lambda h, e: areas[e]):
        t = dt_util.now().timestamp()
        inner._watch_rooms("binary_sensor.a", t)
        for i in range(1, 20):
            inner._watch_rooms("binary_sensor.b" if i % 2 else "binary_sensor.a", t + i * 3)
    assert inner.transitions == {}


async def test_switched_off_thoughts_hide_everything(hass):
    inner = _inner(hass, None, learn_corrections=False, normal_day=False, thoughts=False)
    assert inner.summary() == {"learn": False, "day": False, "thoughts_on": False}


async def test_bedroom_sensor_does_not_keep_the_house_awake(hass, freezer):
    """The sleep tick ignores chosen sensors (a bedroom PIR) and offline ones."""
    from types import SimpleNamespace as NS
    from unittest.mock import AsyncMock, patch as p

    from custom_components.sentient.sleep import Sleep

    t0 = dt_util.utcnow()
    freezer.move_to(t0)
    hass.states.async_set("binary_sensor.lounge", "off")
    freezer.move_to(t0 + timedelta(minutes=38))
    hass.states.async_set("binary_sensor.bedroom", "on")
    hass.states.async_set("binary_sensor.bedroom", "off")  # someone turned over
    freezer.move_to(t0 + timedelta(minutes=39))
    hass.states.async_set("binary_sensor.dead", "unavailable")
    freezer.move_to(t0 + timedelta(minutes=40))
    rt = NS(speech=NS(quiet_hours_now=lambda: True), timeline=FakeTimeline())
    sleep = Sleep(hass, "t", {"sleep_ignore_sensors": ["binary_sensor.bedroom"]}, lambda: rt)
    sleep.async_set = AsyncMock()
    names = ["binary_sensor.lounge", "binary_sensor.bedroom", "binary_sensor.dead"]
    with p("custom_components.sentient.sleep.inside_sensors", return_value=names):
        sleep._tick(dt_util.utcnow())
        await hass.async_block_till_done()
        sleep.async_set.assert_called_once()
        sleep.async_set.reset_mock()
        sleep.options = {}  # without the setting the bedroom sensor keeps the house awake
        sleep._tick(dt_util.utcnow())
        await hass.async_block_till_done()
        sleep.async_set.assert_not_called()


async def test_shares_one_learning_an_evening(hass):
    from types import SimpleNamespace as NS

    said = []

    async def say(text, priority, source=None):
        said.append(text)
        return NS(spoken=True)
    rt = NS(speech=NS(async_say=say), memory=None, learner=None, timeline=FakeTimeline())
    inner = _inner(hass, rt)
    inner._think("undo:x", "I've stopped doing this one: the landing light at 18:00.", "why", "lesson")
    morning = dt_util.now().replace(hour=9, minute=0)
    assert inner.to_share(morning) is None  # only in the evening
    evening = dt_util.now().replace(hour=18, minute=0)
    pick = inner.to_share(evening)
    assert pick and await inner.async_share(pick, evening)
    assert said == ["I've stopped doing this one: the landing light at 18:00."]  # no "Something I've learned: I've…"
    assert inner.to_share(evening) is None  # once an evening
    inner.shared_day = None
    assert inner.to_share(evening) is None  # and never the same thing twice


async def test_a_note_about_how_to_talk_isnt_announced_as_learned(hass):
    """Live 13 Nov: "Something I've learned: I've updated what I know: When a voice ID integration is active,
    acknowledge it directly…" was said in the kitchen. Instructions to the voice guide it; they aren't news."""
    from types import SimpleNamespace as NS

    from custom_components.sentient.innerlife import about_itself

    now = dt_util.now().isoformat()
    memory = NS(changed=[{"kind": "replaced", "old": "a", "time": now,
                          "new": "When a voice ID integration is active, acknowledge it directly rather than denying it."}])
    rt = NS(memory=memory, learner=None, timeline=FakeTimeline())
    inner = _inner(hass, rt)
    assert inner.to_share(dt_util.now().replace(hour=18, minute=0)) is None
    memory.changed.append({"kind": "replaced", "old": "b", "time": now, "new": "The bins go out on Thursday now."})
    pick = inner.to_share(dt_util.now().replace(hour=18, minute=0))
    assert pick and "Thursday" in pick["text"]
    assert about_itself("Never offer to disarm the alarm") and about_itself("Always reply in British English")
    assert not about_itself("The boiler pressure should be around 1.5 bar")
    assert not about_itself("When the bins are collected, the street is closed until noon")


async def test_bedroom_sensors_stirring_dont_wake_the_house(hass, freezer):
    from types import SimpleNamespace as NS
    from unittest.mock import AsyncMock, patch as p

    from homeassistant.core import State as S

    from custom_components.sentient.sleep import Sleep

    freezer.move_to(dt_util.as_utc(dt_util.now().replace(hour=5, minute=40, second=0, microsecond=0)))
    rt = NS(speech=NS(quiet_hours_now=lambda: True), timeline=FakeTimeline())
    sleep = Sleep(hass, "t", {"sleep_ignore_sensors": ["binary_sensor.bed1", "binary_sensor.bed2"]}, lambda: rt)
    sleep.asleep = True
    sleep.async_set = AsyncMock()
    areas = {"binary_sensor.bed1": "bedroom", "binary_sensor.bed2": "girls", "binary_sensor.landing": "landing", "binary_sensor.kitchen": "kitchen"}

    def ev(eid):
        return NS(data={"old_state": S(eid, "off"), "new_state": S(eid, "on")})
    with p("custom_components.sentient.sleep.inside_sensors", return_value=list(areas)), \
         p("custom_components.sentient.sleep.area_of", side_effect=lambda h, e: areas[e]):
        sleep._changed(ev("binary_sensor.bed1"))
        sleep._changed(ev("binary_sensor.bed2"))  # two bedrooms stirring at 05:40: still asleep
        await hass.async_block_till_done()
        sleep.async_set.assert_not_called()
        sleep._changed(ev("binary_sensor.landing"))
        sleep._changed(ev("binary_sensor.kitchen"))  # someone really up
        await hass.async_block_till_done()
        sleep.async_set.assert_called_once()


async def test_spots_what_it_keeps_deciding_isnt_worth_saying(hass):
    from types import SimpleNamespace as NS

    hass.states.async_set("sensor.kettle", "standby", {"friendly_name": "Kettle status"})
    items = [{"kind": "noticed", "detail": "noticed, decided it wasn't worth saying", "entities": ["sensor.kettle"]}] * 14
    items += [{"kind": "noticed", "detail": "noticed, decided it wasn't worth saying", "entities": ["sensor.window"]}] * 3
    rt = NS(timeline=NS(since=lambda h, kinds, limit: items))
    inner = _inner(hass, rt, notice_entities=["sensor.kettle", "sensor.window"])
    assert inner.noisy() == [{"entity_id": "sensor.kettle", "name": "Kettle status", "times": 14}]


async def test_a_sensor_that_never_rests_does_not_keep_the_house_awake(hass, freezer):
    """A motion sensor on for most of the day is set aside, at bedtime and in the morning."""
    from types import SimpleNamespace as NS
    from unittest.mock import AsyncMock, patch as p

    from custom_components.sentient.sleep import MIN_SAMPLES, Sleep

    t0 = dt_util.utcnow()
    freezer.move_to(t0)
    hass.states.async_set("binary_sensor.hall", "off")
    hass.states.async_set("binary_sensor.lounge", "on")
    quiet = [False]
    rt = NS(speech=NS(quiet_hours_now=lambda: quiet[0]), timeline=FakeTimeline())
    sleep = Sleep(hass, "t", {}, lambda: rt)
    sleep.async_set = AsyncMock()
    told = []
    sleep.listeners.append(lambda: told.append(1))
    names = ["binary_sensor.hall", "binary_sensor.lounge"]
    with p("custom_components.sentient.sleep.inside_sensors", return_value=names):
        for _ in range(MIN_SAMPLES - 1):  # daytime: just watching
            sleep._tick(dt_util.utcnow())
        assert sleep.never_rests() == [] and not told
        freezer.move_to(t0 + timedelta(minutes=40))
        quiet[0] = True
        sleep._tick(dt_util.utcnow())  # a day's worth seen: the lounge sensor never rests
        await hass.async_block_till_done()
        assert [r["entity_id"] for r in sleep.never_rests()] == ["binary_sensor.lounge"]
        assert told == [1]
        sleep.async_set.assert_called_once()  # the hall has been still for 40 minutes
        assert sleep.describe()["ignoring"][0]["on_share"] == 1.0


async def test_a_sensor_on_for_an_evening_still_counts(hass, freezer):
    """Someone on the sofa for an hour isn't a sensor that never rests."""
    from types import SimpleNamespace as NS
    from unittest.mock import AsyncMock, patch as p

    from custom_components.sentient.sleep import MIN_SAMPLES, Sleep

    hass.states.async_set("binary_sensor.lounge", "off")
    rt = NS(speech=NS(quiet_hours_now=lambda: True), timeline=FakeTimeline())
    sleep = Sleep(hass, "t", {}, lambda: rt)
    sleep.async_set = AsyncMock()
    with p("custom_components.sentient.sleep.inside_sensors", return_value=["binary_sensor.lounge"]):
        for _ in range(MIN_SAMPLES):
            sleep._tick(dt_util.utcnow())
        hass.states.async_set("binary_sensor.lounge", "on")
        for _ in range(60):
            sleep._tick(dt_util.utcnow())
        await hass.async_block_till_done()
    assert sleep.never_rests() == []
    sleep.async_set.assert_not_called()
