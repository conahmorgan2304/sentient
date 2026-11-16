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
    freezer.move_to("2026-06-15 13:00:00+00:00")
    hass.states.async_set("binary_sensor.back_door", "off", {"device_class": "door", "friendly_name": "Back door"})
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
        return SimpleNamespace(response=SimpleNamespace(speech={"plain": {"speech": replies.get(text, "It's 14:00.")}}),
                               conversation_id=cid)

    def tools(self, cid):  # the voice switched something while only being asked about the bins
        return ["HassTurnOn"] if list(asked)[-1] == qs["impossible"].ask else []

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
