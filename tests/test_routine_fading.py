"""An adopted routine someone keeps doing first is asked about once, then forgotten only if they say so."""
from datetime import timedelta
from unittest.mock import Mock, patch

from homeassistant.util import dt as dt_util

from tests.test_learned_autonomy_reliability import habit, learner  # noqa: F401 - fixture


def _days_ago(n):
    return dt_util.now() - timedelta(days=n)


async def _due_on(obj, routine, day):
    with patch("custom_components.sentient.learning.dt_util.now", return_value=day):
        await obj._async_do(routine)


async def test_asks_once_after_five_already_done_days(learner):  # noqa: F811
    asked = []
    learner.wonder = lambda q, **kw: asked.append((q, kw)) or True
    routine = habit(learner, adopted=True)
    learner.hass.states.async_set("light.lamp", "on", {"friendly_name": "Lamp"})  # already done by someone
    for n in (9, 7, 5, 3):
        await _due_on(learner, routine, _days_ago(n))
    assert not asked and len(routine.already) == 4
    await _due_on(learner, routine, _days_ago(1))
    await _due_on(learner, routine, _days_ago(0))
    assert len(asked) == 1 and routine.asked_fading
    question, kw = asked[0]
    assert "still want me to do it" in question and routine.id in kw["extra"] and "sentient_forget_routine" in kw["extra"]


async def test_old_dates_dont_count_and_the_same_day_counts_once(learner):  # noqa: F811
    learner.wonder = Mock(return_value=True)
    routine = habit(learner, adopted=True)
    learner.hass.states.async_set("light.lamp", "on", {"friendly_name": "Lamp"})
    for n in (30, 25, 20, 18):
        await _due_on(learner, routine, _days_ago(n))
    for _ in range(3):
        await _due_on(learner, routine, _days_ago(0))
    assert len(routine.already) == 1 and not learner.wonder.called


async def test_still_does_its_job_when_not_already_done(learner):  # noqa: F811
    learner.wonder = Mock(return_value=True)
    routine = habit(learner, adopted=True)
    calls = []
    learner.hass.services.async_register("light", "turn_on", lambda c: calls.append(c))
    learner.trust = None
    await learner._async_do(routine)
    assert calls and routine.already == []


async def test_switched_off_records_nothing(learner):  # noqa: F811
    learner.options["routine_fading"] = False
    learner.wonder = Mock(return_value=True)
    routine = habit(learner, adopted=True)
    learner.hass.states.async_set("light.lamp", "on", {"friendly_name": "Lamp"})
    for n in range(6):
        await _due_on(learner, routine, _days_ago(n))
    assert routine.already == [] and not learner.wonder.called


async def test_old_saved_routines_still_load():
    from custom_components.sentient.learning import Routine

    old = {"entity_id": "light.lamp", "name": "lamp", "state": "on", "minute": 600, "days": "every day",
           "support": 0.8, "seen": 10, "spread": 4.0}
    assert Routine(**old).already == [] and not Routine(**old).asked_fading


async def test_curiosity_passes_the_extra_instruction(hass):
    from types import SimpleNamespace
    from unittest.mock import AsyncMock

    from custom_components.sentient.curiosity import Curiosity

    speech = SimpleNamespace(_privacy_generation=0, _guest_generation=0, guest_mode=False, quiet_now=lambda: False,
                             _said=[], async_ask=AsyncMock(return_value=SimpleNamespace(spoken=True, reason="")))
    rt = SimpleNamespace(speech=speech, tracker=SimpleNamespace(best_area=lambda: "kitchen"), timeline=Mock())
    cur = Curiosity(hass, "x", {"curiosity": True}, lambda: rt)
    cur._save = Mock()
    assert cur.wonder("Do you still want the lamp routine?", why="fading", extra="call sentient_forget_routine 'abc'")
    await cur.async_ask(force=True)
    assert "sentient_forget_routine 'abc'" in speech.async_ask.await_args.kwargs["extra_prompt"]
