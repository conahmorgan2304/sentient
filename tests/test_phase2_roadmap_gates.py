"""Future-feature review gates remain side-effect-free and permission-sensitive."""
from datetime import datetime, timedelta, timezone
from custom_components.sentient.stream_health import assess
from custom_components.sentient.speaker_evaluation import report
from custom_components.sentient.layout_policy import validate
from custom_components.sentient.action_intents import ActionIntentMonitor


def test_stream_needs_actual_frame_not_just_connected():
    assert assess([{"stage": "connected", "elapsed_ms": 10}])["playback"] == "unverified"
    assert assess([{"stage": "buffering"}, {"stage": "expired"}])["fallback_recommended"]
    assert assess([{"stage": "frame", "elapsed_ms": 17000}])["fallback_recommended"]


def test_voice_metrics_do_not_grant_identity_access():
    result = report([{"reason": "matched"}] * 25)
    assert result["matched_fraction"] == 1
    assert result["automatic_authorisation"] is False
    assert report([{"reason": "matched"}])["matched_fraction"] is None


def test_layout_needs_admin_and_does_not_save_or_grant_entity_access():
    proposal = {"mind": [{"id": "thinking", "visible": True}]}
    assert not validate(proposal, admin=False)["accepted"]
    accepted = validate(proposal, admin=True)
    assert accepted["accepted"]
    assert accepted["does_not_save"]
    assert accepted["does_not_grant_card_access"]
    assert not validate({"mind": [{"id": "thinking", "visible": True}] * 2}, admin=True)["accepted"]


def test_monitor_preview_makes_no_new_observation_and_hides_device_name():
    now = datetime(2026, 11, 13, tzinfo=timezone.utc)
    monitor = ActionIntentMonitor()
    monitor.observe("light.girls_room", "turn_off", "guarded_voice", when=now)
    before = monitor.summary()["intents_seen"]
    preview = monitor.preview("light.girls_room", "turn_on", "motion_lights",
                              when=now + timedelta(seconds=10))
    assert preview["decision"] == "recommend_hold"
    assert preview["may_dispatch"] is False
    assert "girls_room" not in str(preview)
    assert monitor.summary()["intents_seen"] == before
