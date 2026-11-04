"""Wake a sleepy camera before the AI looks at it.

Battery cameras (and some others) go into standby when nothing's happening; the picture Home Assistant has is
then old, black or the camera's "asleep" frame, and the AI would say "the drive is empty" from it. With wake
actions set (a button that wakes it, a stream turned on, a script — `{{ camera }}` is the camera being looked
at), Sentient runs them first and waits a few seconds for a fresh picture. Off unless actions are set.
"""

from __future__ import annotations

import asyncio
import logging
from typing import Any

from homeassistant.core import HomeAssistant
from homeassistant.util import dt as dt_util

from .const import CONF_CAMERA_WAKE, CONF_CAMERA_WAKE_CAMERAS, CONF_CAMERA_WAKE_SECONDS, DEFAULT_CAMERA_WAKE_SECONDS

_LOGGER = logging.getLogger(__name__)
AWAKE_FOR = 60  # seconds: a camera woken this recently is still awake — don't wake it again for every look
_woken: dict[str, float] = {}


def wanted(options: dict[str, Any], camera: str) -> bool:
    if not options.get(CONF_CAMERA_WAKE):
        return False
    only = options.get(CONF_CAMERA_WAKE_CAMERAS) or []
    return not only or camera in only


async def async_wake(hass: HomeAssistant, options: dict[str, Any], camera: str) -> bool:
    """Run the wake actions for this camera and wait for it; True if it was woken now."""
    if not camera or not wanted(options, camera):
        return False
    now = dt_util.utcnow().timestamp()
    if now - _woken.get(camera, 0) < AWAKE_FOR:
        return False
    _woken[camera] = now
    from .shortcuts import async_run_actions

    try:
        await async_run_actions(hass, options[CONF_CAMERA_WAKE], f"wake {camera}", variables={"camera": camera})
    except Exception as err:  # noqa: BLE001 - look anyway: an old picture is better than none
        _LOGGER.warning("Sentient couldn't wake %s before looking: %s", camera, err)
        return False
    await asyncio.sleep(float(options.get(CONF_CAMERA_WAKE_SECONDS, DEFAULT_CAMERA_WAKE_SECONDS) or 0))
    return True
