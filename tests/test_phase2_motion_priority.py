"""Opted-in motion rules may hold only fresh reversible voice conflicts."""
from datetime import timedelta
from types import SimpleNamespace
from unittest.mock import patch

from homeassistant.util import dt as dt_util
from custom_components.sentient.action_intents import ActionIntentMonitor
from custom_components.sentient.motion_lights import MotionLights, MotionRule


def test_motion_does_not_undo_recent_voice_off_when_opted_in(hass):
    monitor = ActionIntentMonitor()
    light = "light.girls_room"
    monitor.observe(light, "turn_off", "guarded_voice", when=dt_util.utcnow())
    motion = MotionLights(hass, "test-priority", SimpleNamespace())
    motion.intents = monitor
    rule = MotionRule(light=light, motion=["binary_sensor.girls_motion"],
                      protect_voice_override=True)
    with patch.object(motion, "_note") as note:
        assert motion._turn(rule, True) is False
    assert note.call_args.args[1] == "held: recent higher-priority command"
    assert not motion._ours
    assert monitor.summary()["intents_seen"] == 1


def test_expired_voice_intent_is_not_a_reason_to_hold(hass):
    monitor = ActionIntentMonitor()
    light = "light.girls_room"
    stamp = dt_util.utcnow() - timedelta(minutes=4)
    monitor.observe(light, "turn_off", "guarded_voice", when=stamp)
    assert monitor.preview(light, "turn_on", "motion_lights")["decision"] == "no_conflict"
