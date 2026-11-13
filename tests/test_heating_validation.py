"""Held-out heating validation must never claim savings or accuracy without data."""
from datetime import datetime, timedelta, timezone
from types import SimpleNamespace

from custom_components.sentient.heating import warmup_validation


def _episode(start, delta=1.5, hours=1):
    return [
        SimpleNamespace(state="heat", attributes={"current_temperature": 18.0, "temperature": 20.0},
                        last_updated=start),
        SimpleNamespace(state="heat", attributes={"current_temperature": 19.9, "temperature": 20.0},
                        last_updated=start + timedelta(hours=hours)),
        SimpleNamespace(state="off", attributes={}, last_updated=start + timedelta(hours=hours, minutes=1)),
    ]


def test_no_heating_savings_claim_without_observations():
    result = warmup_validation([])
    assert result["status"] == "insufficient_data"
    assert result["absolute_error_degrees_per_hour"] is None


def test_validation_uses_chronologically_separate_episodes():
    start = datetime(2026, 11, 1, tzinfo=timezone.utc)
    states = []
    for i in range(8):
        states.extend(_episode(start + timedelta(days=i)))
    result = warmup_validation(states)
    assert result["status"] == "measured"
    assert result["training_episodes"] >= 2
    assert result["test_episodes"] >= 1
    assert result["absolute_error_degrees_per_hour"] == 0


def test_unfinished_episode_does_not_produce_accuracy():
    t = datetime(2026, 11, 1, tzinfo=timezone.utc)
    states = [SimpleNamespace(state="heat",
                              attributes={"current_temperature": 18, "temperature": 20},
                              last_updated=t + timedelta(hours=i)) for i in range(20)]
    result = warmup_validation(states)
    assert result["status"] == "insufficient_data"
    assert result["absolute_error_degrees_per_hour"] is None


def test_a_warm_up_across_the_split_is_not_cut_in_two():
    """4 off readings then 10 warm-ups: the 70% point (state 23) is between the start and end of the 7th warm-up.
    The split moves to the next off, so all 10 are counted."""
    start = datetime(2026, 11, 1, tzinfo=timezone.utc)
    states = [SimpleNamespace(state="off", attributes={}, last_updated=start - timedelta(hours=4 - h)) for h in range(4)]
    for i in range(10):
        states.extend(_episode(start + timedelta(days=i)))
    result = warmup_validation(states)
    assert result["training_episodes"] + result["test_episodes"] == 10
