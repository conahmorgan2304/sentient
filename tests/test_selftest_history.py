"""Old self-test questions are cleared from history so they stop counting as people asking."""
from custom_components.sentient.conversations import drop_selftest_bursts


def _ep(start, text):
    return {"id": text[:4] + start[-6:], "start": start, "turns": [{"who": "them", "text": text}, {"who": "me", "text": "ok"}]}


def test_burst_of_selftest_questions_is_removed_but_real_ones_stay():
    burst = [_ep(f"2026-11-09T03:25:0{i}+00:00", q) for i, q in enumerate(
        ["Is the alarm on?", "What time is it?", "Is anything open?", "What reminders have I got?"])]
    real = [_ep("2026-11-09T08:00:00+00:00", "Is the alarm on?"), _ep("2026-11-08T08:00:00+00:00", "Play some jazz")]
    kept = drop_selftest_bursts(burst + real)
    assert kept == real


def test_two_lookalike_questions_are_kept():
    eps = [_ep("2026-11-09T08:00:00+00:00", "Is the alarm on?"), _ep("2026-11-09T08:00:30+00:00", "Is anything open?")]
    assert drop_selftest_bursts(eps) == eps
