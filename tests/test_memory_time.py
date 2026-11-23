"""Memory that understands time: temporary things end by themselves, old facts are checked, each day has a story."""

from datetime import date, timedelta
from types import SimpleNamespace
from unittest.mock import Mock, patch

from homeassistant.core import HomeAssistant
from homeassistant.helpers import llm
from homeassistant.util import dt as dt_util
from pytest_homeassistant_custom_component.common import MockConfigEntry
import pytest

from custom_components.sentient.const import DOMAIN
from custom_components.sentient.memory import MemoryStore, expiry_for, temporary_expiry
from custom_components.sentient.reflection import Reflection, parse_day

MONDAY = date(2026, 11, 23)


@pytest.mark.parametrize("text,until,once", [
    ("Conah turned the heating on himself.", "2026-11-24", True),
    ("I've switched the fan off", "2026-11-24", True),
    ("The boiler is broken for now", "2026-12-01", False),
    ("Granny is staying for two weeks", "2026-12-08", False),
    ("The garage is out of bounds at the moment", "2026-12-01", False),
    ("We turn the heating up in the evenings", None, False),  # a habit
    ("The left bedroom light is on the landing switch", None, False),  # "left" isn't something done
    ("Conah has a motorcycle", None, False),
])
def test_what_is_only_true_for_a_while(text, until, once) -> None:
    assert temporary_expiry(text, MONDAY) == (until, once)


def test_a_weekly_habit_named_by_its_day_never_expires() -> None:
    # "on Thursday nights" is every week; before 1.18 it ended the following Friday
    assert expiry_for("The bins go out on Thursday nights", MONDAY) is None
    assert expiry_for("We go swimming on Saturdays", MONDAY) is None
    assert expiry_for("The plumber is coming on Thursday", MONDAY) == "2026-11-27"


async def test_only_with_the_setting_on(hass: HomeAssistant, freezer) -> None:
    freezer.move_to("2026-11-23T12:00:00+00:00")
    store = MemoryStore(hass, "time")
    store._save = Mock()
    plain = store.add("Conah turned the heating on himself.", source="conversation")
    assert plain.expires is None and plain.text == "Conah turned the heating on himself."
    store.time_aware = True
    once = store.add("Kayleigh turned the heating up.", source="conversation")
    assert once.text == "Kayleigh turned the heating up (on 23 November 2026)."
    assert once.expires == "2026-11-24"
    lesson = store.add("When someone turned the fan on, don't turn it off", "correction")
    assert lesson.expires is None  # corrections are standing instructions
    liking = store.add("Kayleigh likes the lamp turned down low", "preference")
    assert liking.expires is None and "(on " not in liking.text  # a preference, not something done once
    freezer.move_to("2026-11-25T12:00:00+00:00")
    assert not store.live(once) and store.live(plain)


def test_days_in_words() -> None:
    assert parse_day("yesterday", MONDAY) == date(2026, 11, 22)
    assert parse_day("last saturday", MONDAY) == date(2026, 11, 21)
    assert parse_day("monday", MONDAY) == date(2026, 11, 16)  # a weekday asked about is the last one gone
    assert parse_day("3 days ago", MONDAY) == date(2026, 11, 20)
    assert parse_day("21st November", MONDAY) == date(2026, 11, 21)
    assert parse_day("25 december", MONDAY) == date(2025, 12, 25)
    assert parse_day("2026-11-01", MONDAY) == date(2026, 11, 1)
    assert parse_day("some time", MONDAY) is None


@pytest.fixture
def reflection(hass):
    memory = MemoryStore(hass, "time-reflect")
    memory._save = Mock()
    options = {"memory_time": True}
    rt = SimpleNamespace(options=options, speech=SimpleNamespace(guest_mode=False, _privacy_generation=0, _guest_generation=0),
                         memory=memory, watches=SimpleNamespace(spend=Mock(return_value=True)),
                         curiosity=SimpleNamespace(wonder=Mock(return_value=True)), followyou=SimpleNamespace(keep=Mock()),
                         timeline=Mock())
    obj = Reflection(hass, "time", options, lambda: rt)
    obj._material = Mock(return_value=(["Sat 10:00 [said] the boiler stopped"], []))
    obj._store.async_delay_save = Mock()
    return obj, rt


async def test_the_day_gets_a_story_from_the_same_ai_call(reflection, freezer) -> None:
    obj, rt = reflection
    freezer.move_to("2026-11-22T04:30:00+00:00")  # Sunday morning: the story is Saturday's
    seen = {}

    async def generate(hass, data):
        seen["data"] = data
        return {"data": {"story": "The boiler stopped in the morning and was reset by lunchtime. Someone came to the door."}}

    with patch("custom_components.sentient.reflection.async_generate", side_effect=generate):
        result = await obj.async_reflect()
    assert "story" in seen["data"]["structure"] and "6. story" in seen["data"]["instructions"]
    assert result["story"]["date"] == "2026-11-21"
    assert obj.find_days("2026-11-21") == [{"day": "Saturday 21 November", "story": result["story"]["story"]}]
    assert obj.find_days(about="radiator boiler") and not obj.find_days(about="dishwasher")
    rt.watches.spend.assert_called_once()  # one AI call, as before


async def test_no_story_without_the_setting(reflection) -> None:
    obj, rt = reflection
    rt.options["memory_time"] = False
    seen = {}

    async def generate(hass, data):
        seen["data"] = data
        return {"data": {"story": "Something."}}

    obj.days = [{"date": "2026-11-20", "story": "kept"}]
    with patch("custom_components.sentient.reflection.async_generate", side_effect=generate):
        result = await obj.async_reflect()
    assert "story" not in seen["data"]["structure"] and "story" not in result
    assert obj.days == []  # switched off: what was kept goes


async def test_an_old_unconfirmed_fact_is_checked_once(reflection, freezer) -> None:
    obj, rt = reflection
    freezer.move_to("2026-08-01T12:00:00+00:00")
    old = rt.memory.add("The garage light is on a timer", source="conversation")
    told = rt.memory.add("There is one robot vacuum", source="service")
    freezer.move_to("2026-11-23T04:30:00+00:00")
    rt.memory.add("The shed is new", source="conversation")

    async def generate(hass, data):
        return {"data": {}}

    with patch("custom_components.sentient.reflection.async_generate", side_effect=generate):
        result = await obj.async_reflect()
    assert result["checking_old"] == "A while ago I was told that the garage light is on a timer — is that still right?"
    assert obj.asked_old == [old.id] and told.id not in obj.asked_old
    rt.curiosity.wonder.reset_mock()
    obj._material = Mock(return_value=(["another day"], []))
    with patch("custom_components.sentient.reflection.async_generate", side_effect=generate):
        result = await obj.async_reflect()
    assert "checking_old" not in result  # asked once, never again


async def test_voice_tool_only_with_the_setting(hass: HomeAssistant) -> None:
    entry = MockConfigEntry(domain=DOMAIN, title="Nova", unique_id=DOMAIN,
                            data={"persona": "Nova", "quiet_start": "00:00:00", "quiet_end": "00:00:00", "memory_time": True})
    entry.add_to_hass(hass)
    assert await hass.config_entries.async_setup(entry.entry_id)
    await hass.async_block_till_done()
    ctx = llm.LLMContext(platform="test", context=None, language="en", assistant="conversation", device_id=None)
    api = await llm.async_get_api(hass, "sentient", ctx)
    yesterday = (dt_util.now().date() - timedelta(days=1)).isoformat()
    entry.runtime_data.reflection.add_day("Everyone was in; the vacuum got stuck under the sofa.", yesterday)
    out = await api.async_call_tool(llm.ToolInput(tool_name="sentient_past_days", tool_args={"day": "yesterday"}))
    assert out["days"][0]["story"].startswith("Everyone was in")
    out = await api.async_call_tool(llm.ToolInput(tool_name="sentient_past_days", tool_args={"about": "hoover"}))
    assert len(out["days"]) == 1  # the vacuum, asked about as the hoover
    out = await api.async_call_tool(llm.ToolInput(tool_name="sentient_past_days", tool_args={"day": "the other day"}))
    assert "can't tell which day" in out["error"]
    hass.config_entries.async_update_entry(entry, options={**entry.options, **entry.data, "memory_time": False})
    await hass.async_block_till_done()
    api = await llm.async_get_api(hass, "sentient", ctx)
    assert "sentient_past_days" not in {t.name for t in api.tools}
    assert entry.runtime_data.reflection.days == []
