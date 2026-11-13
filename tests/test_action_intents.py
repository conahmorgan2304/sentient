"""Advisory-only cross-feature intent monitoring: never an actuator."""
from datetime import datetime, timedelta, timezone

import pytest

from custom_components.sentient.action_intents import ActionIntentMonitor

NOW = datetime(2026, 11, 13, 13, 0, tzinfo=timezone.utc)


def test_opposing_intents_across_subsystems_are_reported_without_actions():
    monitor = ActionIntentMonitor()
    assert monitor.observe("light.girls_room", "turn_off", "auto_off", when=NOW) is None
    conflict = monitor.observe("light.girls_room", "turn_on", "motion_lights",
                               when=NOW + timedelta(seconds=20))
    assert conflict == {"earlier_source": "auto_off", "later_source": "motion_lights",
                        "relationship": "opposing_intents", "verified_conflict": "no"}
    out = monitor.summary()
    assert out["potential_conflicts"] == 1
    assert out["automatic_interventions"] == 0
    assert "girls_room" not in str(out)
    assert all(len(key) == 32 for key in monitor._last)


def test_same_direction_and_same_source_are_not_conflicts():
    monitor = ActionIntentMonitor()
    monitor.observe("light.hall", "turn_on", "guarded_voice", when=NOW)
    assert monitor.observe("light.hall", "on", "motion_lights",
                           when=NOW + timedelta(seconds=10)) is None
    assert monitor.observe("light.hall", "turn_off", "motion_lights",
                           when=NOW + timedelta(seconds=20)) is None
    assert monitor.summary()["potential_conflicts"] == 0


def test_old_intents_expire_and_records_are_bounded():
    monitor = ActionIntentMonitor()
    monitor.observe("light.hall", "turn_on", "guarded_voice", when=NOW)
    assert monitor.observe("light.hall", "turn_off", "auto_off",
                           when=NOW + timedelta(minutes=3)) is None
    for n in range(300):
        monitor.observe(f"light.room_{n}", "turn_on", "motion_lights",
                        when=NOW + timedelta(minutes=4))
    assert monitor.summary()["targets_tracked"] <= 128


def test_clear_removes_history_and_rotates_private_digest_key():
    monitor = ActionIntentMonitor()
    monitor.observe("light.hall", "turn_on", "guarded_voice", when=NOW)
    original = list(monitor._last)
    monitor.clear()
    assert monitor.summary()["intents_seen"] == 0
    assert not monitor._last
    monitor.observe("light.hall", "turn_on", "guarded_voice", when=NOW)
    assert list(monitor._last) != original


def test_invalid_calls_never_count_and_naive_time_is_rejected():
    monitor = ActionIntentMonitor()
    assert monitor.observe("not-an-entity", "turn_on", "motion_lights", when=NOW) is None
    assert monitor.observe("light.hall", "toggle", "motion_lights", when=NOW) is None
    assert monitor.observe("light.hall", "turn_on", "unknown", when=NOW) is None
    assert monitor.summary()["intents_seen"] == 0
    with pytest.raises(ValueError):
        monitor.observe("light.hall", "turn_on", "motion_lights",
                        when=datetime(2026, 11, 13))
