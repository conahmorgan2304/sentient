"""Cross-source announcements share the same punctuation-neutral identity."""
from custom_components.sentient.speech import SpeechRouter, _remark_key


def test_remark_identity_does_not_depend_on_source_or_punctuation():
    assert _remark_key("Someone opened the gate!") == _remark_key("Someone opened the gate.")
    assert _remark_key("The door opened") != _remark_key("The door closed")


def test_duplicate_gate_uses_shared_identity_even_for_other_sources(hass, freezer):
    freezer.move_to("2026-06-15 20:00:00+00:00")  # midday in the test time zone, outside quiet hours
    router = SpeechRouter(hass, {})
    router._remember_said("Someone opened the gate!")
    held = router._gate("Someone opened the gate.", "normal", "briefing")
    assert held is not None
    assert held.reason == "duplicate"
