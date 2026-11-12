"""Names said the way they're pronounced, on speakers only."""
from homeassistant.core import HomeAssistant
from pytest_homeassistant_custom_component.common import MockConfigEntry

from custom_components.sentient.const import DOMAIN
from custom_components.sentient.pronounce import Pronouncer, StreamPronouncer, apply, from_memories, parse_setting
from custom_components.sentient.voice_stream import StreamWatch

MEMORIES = [
    "Always pronounce Siobhan's name as Shivawn.",
    "Siobhan's name is pronounced Shivawn.",
    "My name is spelled SIOBHAN, and it is pronounced Shivawn",
    "Siobhan is pronounced Shivawn. Never mention the spelling or pronunciation.",
    "Alex drives a blue estate car.",
]


def test_the_households_own_memories_give_the_pronunciation():
    table = from_memories(MEMORIES)
    assert table.get("Siobhan") == "Shivawn" or table.get("SIOBHAN") == "Shivawn"
    assert apply("Morning, Siobhan. Siobhan's bike is out.", table) == "Morning, Shivawn. Shivawn's bike is out."
    assert apply("Siobhans and Siobhan-ish stay", {"Siobhan": "Shivawn"}) == "Siobhans and Siobhan-ish stay"
    assert parse_setting("Siobhan = Shivawn\nNiamh=Neev\nbad line") == {"Siobhan": "Shivawn", "Niamh": "Neev"}


def test_a_name_split_across_streamed_pieces_is_still_said_right():
    say = Pronouncer(lambda: {}, lambda: MEMORIES)
    stream = StreamPronouncer(say, say.table)
    out = stream.feed("Good evening, Siob") + stream.feed("han, the heating") + stream.feed(" is on, Siobhan")
    out += stream.flush()
    assert out == "Good evening, Shivawn, the heating is on, Shivawn"
    assert stream.feed("a Siob") + stream.flush() == "a Siob"  # a reply that really ends "Siob" stays "Siob"
    assert StreamPronouncer(say, lambda: {}).feed("Siob") == "Siob"  # nothing to watch for: nothing held


def test_switched_off_names_are_read_as_spelt():
    say = Pronouncer(lambda: {"pronounce_names": False}, lambda: MEMORIES)
    assert say("Hello Siobhan") == "Hello Siobhan"


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
        watch._spy(log, {"content": "Night, Siob"})
        watch._spy(log, {"content": "han"})
    assert "".join(heard) == "Night, Shivawn"


async def test_announcements_are_pronounced_and_the_timeline_keeps_the_spelling(hass: HomeAssistant):
    from pytest_homeassistant_custom_component.common import async_mock_service

    entry = MockConfigEntry(domain=DOMAIN, title="Nova", unique_id=DOMAIN, data={"persona": "Nova"},
                            options={"speakers": ["assist_satellite.kitchen"], "default_speaker": "assist_satellite.kitchen",
                                     "pronunciations": "Siobhan = Shivawn", "quiet_start": "00:00:00", "quiet_end": "00:00:01"})
    entry.add_to_hass(hass)
    assert await hass.config_entries.async_setup(entry.entry_id)
    await hass.async_block_till_done()
    hass.states.async_set("assist_satellite.kitchen", "idle")
    said = async_mock_service(hass, "assist_satellite", "announce")
    await entry.runtime_data.speech.async_say("Siobhan, the bins go out tonight.", "normal", ["assist_satellite.kitchen"], source="test")
    assert said and said[0].data["message"] == "Shivawn, the bins go out tonight."
    assert any("Siobhan, the bins" in (i.get("text") or "") for i in entry.runtime_data.timeline.items)
