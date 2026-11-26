"""The household question set: questions built from the house's own devices, each checked with plain rules."""

from types import SimpleNamespace
from unittest.mock import patch

from homeassistant.core import HomeAssistant
from homeassistant.util import dt as dt_util
from pytest_homeassistant_custom_component.common import MockConfigEntry

from custom_components.sentient.const import DOMAIN
from custom_components.sentient.selftest import on_off, yes_no

OPTIONS = {"persona": "Nova", "speakers": ["assist_satellite.kitchen"], "default_speaker": "assist_satellite.kitchen",
           "quiet_start": "00:00:00", "quiet_end": "00:00:00", "voice_agent": "conversation.ai", "self_test": "nightly"}


async def _setup(hass: HomeAssistant, **extra) -> MockConfigEntry:
    hass.states.async_set("assist_satellite.kitchen", "idle", {"supported_features": 3})
    entry = MockConfigEntry(domain=DOMAIN, title="Nova", unique_id=DOMAIN, data={**OPTIONS, **extra})
    entry.add_to_hass(hass)
    assert await hass.config_entries.async_setup(entry.entry_id)
    await hass.async_block_till_done()
    return entry


def test_reading_yes_no_and_on_off() -> None:
    assert yes_no("No one has been seen on the garden camera.") is False
    assert yes_no("Yes, someone was seen at ten past four.") is True
    assert yes_no("There were two visits, at 16:10 and 16:45.") is True
    assert yes_no("I'm checking the camera now.") is None
    assert on_off("The kitchen light is on.") is True
    # a plain yes or no answers "is it on?" (16 Nov: "Yes, it is." was marked wrong)
    assert on_off("Yes, it is.") is True
    assert on_off("Yeah, it is.") is True
    assert on_off("No, it isn't.") is False
    assert on_off("It isn't.") is False
    assert on_off("Nope.") is False
    assert on_off("No, it's on.") is True
    assert on_off("Yes, it's off now.") is False
    assert on_off("I'm not sure.") is None
    assert on_off("No, it's off.") is False


async def test_the_set_is_built_from_the_house_and_scored(hass: HomeAssistant, freezer) -> None:
    freezer.move_to("2026-06-15 14:00:00+00:00")
    entry = await _setup(hass)
    rt = entry.runtime_data
    door = {"device_class": "door", "friendly_name": "Back door"}
    freezer.move_to("2026-06-15 12:59:00+00:00")
    hass.states.async_set("binary_sensor.back_door", "on", door)
    freezer.move_to("2026-06-15 13:00:00+00:00")
    hass.states.async_set("binary_sensor.back_door", "off", door)
    await hass.async_block_till_done()
    freezer.move_to("2026-06-15 14:00:00+00:00")
    asked = {}
    with patch.object(type(rt.selftest), "_exposed", lambda self, entity_id: True):
        qs = {q.key: q for q in rt.selftest.questions()}
    assert {"last closed", "unknowable", "impossible", "vague"} <= set(qs)
    assert qs["last closed"].ask == "When was the Back door last closed?"
    when = dt_util.as_local(dt_util.parse_datetime("2026-06-15 13:00:00+00:00"))
    replies = {
        qs["last closed"].ask: f"The back door was closed at {when:%H:%M}.",
        qs["unknowable"].ask: "You had toast and coffee.",  # invented: a fail however convincing
        qs["impossible"].ask: "I can't, I'm just a voice — but I can remind you to.",
        qs["vague"].ask: "Which one do you mean?",
    }

    async def converse(hass_, text, cid, context, **kw):
        asked[text] = True
        said = next((answer for question, answer in replies.items() if text.startswith(question)), "It's 14:00.")
        return SimpleNamespace(response=SimpleNamespace(speech={"plain": {"speech": said}}),
                               conversation_id=cid)

    def tools(self, cid):  # the voice switched something while only being asked about the bins
        return ["HassTurnOn"] if list(asked)[-1].startswith(qs["impossible"].ask) else []

    with patch("homeassistant.components.conversation.async_converse", converse), \
            patch.object(type(rt.selftest), "_exposed", lambda self, entity_id: True), \
            patch.object(type(rt.selftest), "_tools_used", tools):
        out = await rt.selftest.async_run()
    by_key = {r["key"]: r for r in out["results"]}
    assert by_key["last closed"]["right"] and by_key["vague"]["right"]
    assert not by_key["unknowable"]["right"]
    assert not by_key["impossible"]["right"] and "nothing switched" in by_key["impossible"]["expected"]
    assert all("ms" in r for r in out["results"])
    summary = rt.selftest.summary()
    assert summary["total"] == len(out["results"]) and summary["history"][-1]["right"] == summary["right"]
    await hass.async_block_till_done()
    hass.bus.async_fire("sentient_voice_check")  # the Mind sensor redraws each minute, or on this
    await hass.async_block_till_done()
    mind = next(s for s in hass.states.async_all("sensor") if "self_test" in s.attributes)
    assert mind.attributes["self_test"]["total"] == summary["total"]


async def test_the_household_questions_can_be_switched_off(hass: HomeAssistant) -> None:
    entry = await _setup(hass, self_test_household=False)
    keys = {q.key for q in entry.runtime_data.selftest.questions()}
    assert not keys & {"unknowable", "impossible", "vague", "last closed", "light"}


async def test_a_camera_and_the_door_are_asked_about_from_what_was_recorded(hass: HomeAssistant) -> None:
    entry = await _setup(hass)
    rt = entry.runtime_data
    now = dt_util.utcnow().timestamp()
    fake_events = SimpleNamespace(tracking=True, cameras=lambda: {"garden"},
                                  activity=lambda cam, since: [{"labels": ["person"], "start": now - 600}])
    fake_visitors = SimpleNamespace(camera="camera.doorbell", today=lambda: [])
    with patch.object(rt, "security_events", fake_events), patch.object(rt, "visitors", fake_visitors):
        qs = {q.key: q for q in rt.selftest.household_questions(rt)}
    assert qs["camera"].check("Yes, someone was in the garden at ten to two.")[0]
    assert not qs["camera"].check("No one's been seen there.")[0]
    assert qs["visitors"].check("No, nobody's been to the door today.")[0]
    assert not qs["visitors"].check("Yes, a delivery driver came at noon.")[0]


async def test_benchmark_expands_to_heating_energy_and_dry_run_plan(hass: HomeAssistant) -> None:
    entry = await _setup(hass)
    rt = entry.runtime_data
    hass.states.async_set("climate.test_heater", "heat",
                          {"friendly_name": "Test heater", "current_temperature": 19.5})
    hass.states.async_set("sensor.test_energy", "12.6",
                          {"friendly_name": "Test energy", "device_class": "energy",
                           "unit_of_measurement": "kWh"})
    hass.states.async_set("light.test_lamp", "on", {"friendly_name": "Test lamp"})
    hass.states.async_set("binary_sensor.test_door", "off",
                          {"friendly_name": "Test door", "device_class": "door"})
    with patch.object(type(rt.selftest), "_exposed", lambda self, entity_id: True):
        qs = {q.key: q for q in rt.selftest.household_questions(rt)}
    assert {"heating current", "energy reading"} <= set(qs)
    assert qs["heating current"].check("The thermostat reports 19.5 degrees.")[0]
    assert qs["energy reading"].check("The reading is 12.6 kWh.")[0]


async def test_a_door_reporting_closed_again_isnt_a_new_close(hass: HomeAssistant, freezer) -> None:
    """16 Nov: the front door shut at 08:34, its sensor said "closed" again at 08:53 (a reload), and the test expected
    08:53 — so the voice's right answer ("half past eight") was marked wrong."""
    freezer.move_to("2026-06-15 07:00:00+00:00")
    entry = await _setup(hass)
    rt = entry.runtime_data
    door = {"device_class": "door", "friendly_name": "Front door"}
    for at, state in (("08:30", "on"), ("08:34", "off"), ("08:53", "unavailable"), ("08:53:05", "off")):
        freezer.move_to(f"2026-06-15 {at}+00:00" if at.count(":") == 2 else f"2026-06-15 {at}:00+00:00")
        hass.states.async_set("binary_sensor.front_door", state, door)
        await hass.async_block_till_done()
    hass.states.async_set("binary_sensor.never_opened", "off", {"device_class": "window", "friendly_name": "Loft window"})
    freezer.move_to("2026-06-15 10:00:00+00:00")
    with patch.object(type(rt.selftest), "_exposed", lambda self, entity_id: True):
        qs = {q.key: q for q in rt.selftest.questions()}
    assert qs["last closed"].ask == "When was the Front door last closed?"
    local = dt_util.as_local(dt_util.parse_datetime("2026-06-15 08:34:00+00:00"))
    assert qs["last closed"].check(f"It was last shut at {local:%H:%M}.")[0]
    late = dt_util.as_local(dt_util.parse_datetime("2026-06-15 08:53:00+00:00"))
    assert not qs["last closed"].check(f"It was last shut at {late:%H:%M}.")[0]
    # kept over a restart
    rt.selftest.async_stop()
    saved = rt.selftest._saved()
    assert "binary_sensor.front_door" in saved["shut"] and "binary_sensor.never_opened" not in saved["shut"]


async def test_room_temperature_two_at_once_and_no_lock(hass: HomeAssistant) -> None:
    """22 Nov: questions for instant answers, two-part questions and knowing what the house can't do."""
    from homeassistant.helpers import area_registry as ar, entity_registry as er

    entry = await _setup(hass)
    bedroom = ar.async_get(hass).async_get_or_create("Bedroom")
    er.async_get(hass).async_get_or_create("sensor", "test", "bt", suggested_object_id="bedroom_temp")
    er.async_get(hass).async_update_entity("sensor.bedroom_temp", area_id=bedroom.id)
    hass.states.async_set("sensor.bedroom_temp", "18.5", {"device_class": "temperature"})
    hass.states.async_set("light.lamp", "on", {"friendly_name": "Lamp"})
    hass.states.async_set("binary_sensor.back_door", "off", {"device_class": "door", "friendly_name": "Back door"})
    with patch.object(type(entry.runtime_data.selftest), "_exposed", lambda self, entity_id: True):
        qs = {q.key: q for q in entry.runtime_data.selftest.questions()}
    assert qs["room temperature"].ask == "How warm is it in the Bedroom?"
    assert qs["room temperature"].check("It's 18.5°C in the Bedroom.")[0]
    assert not qs["room temperature"].check("It's 21 degrees.")[0]
    assert qs["two at once"].ask == "Is the Lamp on, and is the Back door shut?"
    assert qs["two at once"].check("Yes, the lamp is on, and the back door is shut.")[0]
    assert not qs["two at once"].check("The lamp is off and the back door is shut.")[0]
    assert qs["no lock"].ask == "Can you lock the Back door?"
    assert qs["no lock"].check("I can't lock it — there's no smart lock on that door.")[0]
    assert not qs["no lock"].check("Sure, I'll lock it now.")[0]


def test_selftest_rejects_invalid_times_and_unrelated_words():
    from custom_components.sentient.selftest import ASKS_BACK, mentions, spoken_times

    assert spoken_times("12:99 or 99:15") == []
    assert spoken_times("24:30") == []
    assert spoken_times("quarter past three") == [195]
    assert not mentions("The doorway is closed.", "Door")
    assert mentions("The Eingangstür is open.", "Eingangstür")
    assert not ASKS_BACK.search("Yes, it is on. Anything else?")
    assert ASKS_BACK.search("Which device do you mean?")


async def test_selftest_skips_empty_budget_without_replacing_last_result(hass: HomeAssistant):
    entry = await _setup(hass)
    rt = entry.runtime_data
    previous = {"time": dt_util.utcnow().isoformat(), "results": [], "history": []}
    rt.selftest.last = previous
    with patch.object(type(rt.watches), "spend", return_value=False):
        result = await rt.selftest.async_run()
    assert "skipped" in result
    assert rt.selftest.last is previous


async def test_selftest_rejudge_requires_same_question_and_expectation(hass: HomeAssistant):
    from custom_components.sentient.selftest import Question

    entry = await _setup(hass)
    test = entry.runtime_data.selftest
    row = {"key": "light", "question": "Is the Lamp on?", "answer": "No.", "expected": "on", "right": False}
    test.last = {"results": [row]}
    with patch.object(type(test), "questions", return_value=[
        Question("light", "Is the Lamp on?", lambda answer: (True, "off"))
    ]):
        assert test.rejudge() == 0
    with patch.object(type(test), "questions", return_value=[
        Question("light", "Is the Other Lamp on?", lambda answer: (True, "on"))
    ]):
        assert test.rejudge() == 0
    assert not row["right"]
    with patch.object(type(test), "questions", return_value=[
        Question("light", "Is the Lamp on?", lambda answer: (True, "on"))
    ]):
        assert test.rejudge() == 1
    assert row["right"]


async def test_clock_question_uses_time_at_dispatch(hass: HomeAssistant, freezer):
    from custom_components.sentient.selftest import Question

    freezer.move_to("2026-06-15 12:00:00+00:00")
    entry = await _setup(hass)
    test = entry.runtime_data.selftest
    questions = [
        Question("first", "First?", lambda answer: (True, "yes")),
        Question("time", "What time is it?", lambda answer: (False, "stale")),
    ]

    async def converse(hass_, text, cid, context, **kw):
        if text.startswith("First?"):
            freezer.move_to("2026-06-15 12:15:00+00:00")
        return SimpleNamespace(response=SimpleNamespace(speech={"plain": {"speech": f"It's {dt_util.now():%H:%M}."}}),
                               conversation_id=cid)

    with patch.object(type(test), "questions", return_value=questions), \
            patch("homeassistant.components.conversation.async_converse", converse):
        result = await test.async_run(force=True)
    assert result["results"][1]["right"]
