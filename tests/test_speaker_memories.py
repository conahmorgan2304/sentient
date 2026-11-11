"""Each person's memories stay theirs when voice recognition knows who is speaking (off by default)."""
from types import SimpleNamespace
from unittest.mock import patch

from custom_components.sentient import voice_id
from custom_components.sentient.voice_id import UNKNOWN_SPEAKER, owner_of, speaker_scope


def _m(text):
    return SimpleNamespace(text=text, category="fact")


MEMORIES = [_m("Alex: likes tea with no sugar"), _m("Sam: allergic to cats"), _m("The bins go out on Tuesdays"),
            _m("Note: the boiler was serviced in May")]
ON = {"voice_id": True, "speaker_memories": True}


async def _scope(hass, speaker, options=ON):
    hass.states.async_set("person.alex", "home", {"friendly_name": "Alex"})
    hass.states.async_set("person.sam", "home", {"friendly_name": "Sam"})
    with patch.object(voice_id, "enabled", return_value=True):
        return [m.text for m in speaker_scope(hass, options, list(MEMORIES), speaker)]


async def test_a_matched_voice_gets_their_own_first_and_nobody_elses(hass):
    texts = await _scope(hass, SimpleNamespace(display_name="Alex", share_name=True))
    assert texts[0] == "Alex: likes tea with no sugar"
    assert "Sam: allergic to cats" not in texts
    assert "The bins go out on Tuesdays" in texts and "Note: the boiler was serviced in May" in texts


async def test_an_unmatched_voice_gets_only_household_facts(hass):
    texts = await _scope(hass, UNKNOWN_SPEAKER)
    assert texts == ["The bins go out on Tuesdays", "Note: the boiler was serviced in May"]


async def test_no_voice_or_switched_off_changes_nothing(hass):
    assert await _scope(hass, None) == [m.text for m in MEMORIES]
    off = await _scope(hass, SimpleNamespace(display_name="Alex"), {"voice_id": True})
    assert off == [m.text for m in MEMORIES]


def test_only_known_people_count_as_owners():
    people = {"alex", "sam"}
    assert owner_of("Alex: likes tea", people) == "alex"
    assert owner_of("Note: something", people) is None
    assert owner_of("Tea time is 4pm", people) is None
