"""15 Nov: with the warnings feed unreachable, the voice was handed "Couldn't read the warnings: Task <Task pending
name='Task-151' coro=<TCPConnector._resolve_host_with_throttle() …>" — a raw error it could read aloud."""

from unittest.mock import patch

from homeassistant.core import HomeAssistant

from custom_components.sentient.warnings_feed import Warnings


async def test_an_unreachable_feed_gives_a_short_plain_reason(hass: HomeAssistant) -> None:
    feed = Warnings(hass, {"weather_warnings": True, "warnings_country": "GB"}, lambda: None)

    async def boom(*_a, **_k):
        raise RuntimeError("Task <Task pending name='Task-151' coro=<TCPConnector._resolve_host_with_throttle()>>")

    with patch.object(feed, "_fetch", boom):
        await feed.async_check()
    assert feed.error == "Couldn't read the warnings just now (RuntimeError)"
    assert "Task" not in feed.error
