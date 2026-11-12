"""Names said the way they're pronounced, on speakers only."""
from homeassistant.core import HomeAssistant
from pytest_homeassistant_custom_component.common import MockConfigEntry

from custom_components.sentient.const import DOMAIN
from custom_components.sentient.pronounce import Pronouncer, StreamPronouncer, apply, from_memories, parse_setting
from custom_components.sentient.voice_stream import StreamWatch

MEMORIES = [
    "Always pronounce Conah's name as Connor.",
    "Conah's name is pronounced Connor.",
    "My name is spelled CONAH, and it is pronounced Connor",
    "Conah is pronounced Connor. Never mention the spelling or pronunciation.",
    "Kayleigh drives a Ford Cougar 2011.",
]


def test_the_households_own_memories_give_the_pronunciation():
    table = from_memories(MEMORIES)
    assert table.get("Conah") == "Connor" or table.get("CONAH") == "Connor"
    assert apply("Morning, Conah. Conah's bike is out.", table) == "Morning, Connor. Connor's bike is out."
    assert apply("Conahs and Conah-ish stay", {"Conah": "Connor"}) == "Conahs and Conah-ish stay"
    assert parse_setting("Siobhan = Shivawn\nNiamh=Neev\nbad line") == {"Siobhan": "Shivawn", "Niamh": "Neev"}


def test_a_name_split_across_streamed_pieces_is_still_said_right():
    say = Pronouncer(lambda: {}, lambda: MEMORIES)
    stream = StreamPronouncer(say, say.table)
    out = stream.feed("Good evening, Con") + stream.feed("ah, the heating") + stream.feed(" is on, Conah")
    out += stream.flush()
    assert out == "Good evening, Connor, the heating is on, Connor"
    assert stream.feed("a Con") + stream.flush() == "a Con"  # a reply that really ends "Con" stays "Con"
    assert StreamPronouncer(say, lambda: {}).feed("Con") == "Con"  # nothing to watch for: nothing held


def test_switched_off_names_are_read_as_spelt():
    say = Pronouncer(lambda: {"pronounce_names": False}, lambda: MEMORIES)
    assert say("Hello Conah") == "Hello Conah"


def test_the_stream_watch_speaks_the_pronunciation_and_flushes_at_the_end():
    class Log:
        content: list = []
        delta_listener = None

    heard = []
    log = Log()
    log.delta_listener = lambda _log, delta: heard.append(delta.get("content", ""))
    say = Pronouncer(lambda: {}, lambda: MEMORIES)
    with StreamWatch(log, "conversation.x", say) as watch:
        watch._spy(log, {"role": "assistant"})
        watch._spy(log, {"content": "Night, Con"})
        watch._spy(log, {"content": "ah"})
    assert "".join(heard) == "Night, Connor"


async def test_announcements_are_pronounced_and_the_timeline_keeps_the_spelling(hass: HomeAssistant):
    from pytest_homeassistant_custom_component.common import async_mock_service

    entry = MockConfigEntry(domain=DOMAIN, title="Nova", unique_id=DOMAIN, data={"persona": "Nova"},
                            options={"speakers": ["assist_satellite.kitchen"], "default_speaker": "assist_satellite.kitchen",
                                     "pronunciations": "Conah = Connor", "quiet_start": "00:00:00", "quiet_end": "00:00:01"})
    entry.add_to_hass(hass)
    assert await hass.config_entries.async_setup(entry.entry_id)
    await hass.async_block_till_done()
    hass.states.async_set("assist_satellite.kitchen", "idle")
    said = async_mock_service(hass, "assist_satellite", "announce")
    await entry.runtime_data.speech.async_say("Conah, the bins go out tonight.", "normal", ["assist_satellite.kitchen"], source="test")
    assert said and said[0].data["message"] == "Connor, the bins go out tonight."
    assert any("Conah, the bins" in (i.get("text") or "") for i in entry.runtime_data.timeline.items)
