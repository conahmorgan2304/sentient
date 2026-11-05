"""What I'd change: suggestions from the self-check, changed only on a yes; stale readings; repeated questions."""

from datetime import timedelta

from homeassistant.core import HomeAssistant
from homeassistant.helpers import llm
from homeassistant.util import dt as dt_util
from pytest_homeassistant_custom_component.common import MockConfigEntry

from custom_components.sentient.const import DOMAIN
from custom_components.sentient.proposals import section_for
from custom_components.sentient.selfaware import is_question

OPTIONS = {"persona": "Nova", "speakers": ["assist_satellite.kitchen"], "default_speaker": "assist_satellite.kitchen",
           "quiet_start": "00:00:00", "quiet_end": "00:00:00"}


async def _setup(hass: HomeAssistant, **extra) -> MockConfigEntry:
    entry = MockConfigEntry(domain=DOMAIN, title="Nova", unique_id=DOMAIN, data={**OPTIONS, **extra})
    entry.add_to_hass(hass)
    assert await hass.config_entries.async_setup(entry.entry_id)
    await hass.async_block_till_done()
    return entry


REPEAT = {"kind": "repeat", "about": "remarks", "text": "I said “The kettle's done” 9 times in 7 days"}
ASKED = {"kind": "question", "about": "is it going to rain later", "text": "I've been asked “is it going to rain later” 4 times"}


async def test_only_changed_on_a_yes(hass: HomeAssistant) -> None:
    entry = await _setup(hass, briefing_sections=["time", "reminders"])
    props = entry.runtime_data.proposals
    added = props.refresh([REPEAT, ASKED])
    assert [p["id"] for p in added] == ["repeat", "briefing:weather"]
    assert entry.options.get("remark_repeat_hours") is None  # nothing changed yet
    assert "every 12 hours (now 3)" in props.listing()[0]["change"]
    assert props.answer("repeat", True) == {"changed": True}
    await hass.async_block_till_done()
    assert entry.options["remark_repeat_hours"] == 12
    assert props.answer("briefing:weather", False) == {"changed": False}
    assert "weather" not in entry.options.get("briefing_sections", ["time", "reminders"])
    assert not props.refresh([REPEAT, ASKED])  # a no isn't asked again straight away; a yes no longer fits
    assert props.answer("repeat", True)["error"]


async def test_never_more_than_two_and_off_clears(hass: HomeAssistant) -> None:
    entry = await _setup(hass, briefing_sections=["time"], chattiness="chatty")
    props = entry.runtime_data.proposals
    chatty = {"kind": "chatty", "about": "remarks", "text": "Too many remarks"}
    assert len(props.refresh([REPEAT, ASKED, chatty])) == 2
    hass.config_entries.async_update_entry(entry, options={**OPTIONS, "proposals": False})
    await hass.async_block_till_done()
    assert props.refresh([chatty]) == [] and props.listing() == [] and props.open == []


def test_reading_questions() -> None:
    assert section_for("is it going to rain later") == "weather"
    assert section_for("what's on the calendar today") == "calendar"
    assert section_for("tell me a joke") is None
    assert is_question("is it going to rain later") and is_question("what's on today")
    assert not is_question("turn the kitchen light on") and not is_question("can you set a timer")
    assert not is_question("is it ok to lock the door")  # anything that might do something stays out of the self-test


async def test_weekly_says_what_it_got_wrong(hass: HomeAssistant) -> None:
    entry = await _setup(hass, briefing_sections=["time"])
    rt = entry.runtime_data
    rt.timeline.add("health", "Didn't happen: Landing light on", "asked by voice; the device didn't respond")
    rt.proposals.refresh([ASKED])
    out = rt.upkeep.weekly_report()
    assert "Got wrong: 1 thing asked for by voice didn't happen." in out["summary"]
    assert "I'd change: add weather to the briefing" in out["summary"]


async def test_stale_reading_flagged(hass: HomeAssistant, freezer) -> None:
    hass.states.async_set("sensor.hall_temp", "19.5", {"device_class": "temperature", "friendly_name": "Hall temperature"})
    hass.states.async_set("sensor.loft_temp", "11", {"device_class": "temperature", "friendly_name": "Loft temperature"})
    await _setup(hass)
    freezer.tick(timedelta(hours=7))
    hass.states.async_set("sensor.hall_temp", "19.5", {"device_class": "temperature", "friendly_name": "Hall temperature"},
                          force_update=True)
    api = await llm.async_get_api(hass, DOMAIN, llm.LLMContext(platform="t", context=None, language="en",
                                                               assistant=None, device_id=None))
    prompt = api.api_prompt
    assert "Not heard from for a while" in prompt and "Loft temperature (7 hours)" in prompt
    assert "Hall temperature" not in prompt.split("Not heard from")[1].split("\n")[0]
    assert dt_util.utcnow()


async def test_self_test_asks_the_question_asked_most(hass: HomeAssistant) -> None:
    entry = await _setup(hass)
    rt = entry.runtime_data
    now = dt_util.utcnow().isoformat()
    rt.conversations.episodes = [{"id": str(n), "start": now, "turns": [{"who": "them", "text": text}]}
                                 for n, text in enumerate(["Is it going to rain later?"] * 3 + ["Turn the lamp on please"] * 4)]
    qs = {q.key: q for q in rt.selftest.questions()}
    assert qs["asked often"].ask == "Is it going to rain later?"
    assert qs["asked often"].check("Yes, from about four.")[0]
    assert not qs["asked often"].check("Sorry, I don't know.")[0]
