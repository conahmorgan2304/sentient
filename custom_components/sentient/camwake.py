"""Share bounded camera wake work; never analyse a frame after a required wake failed."""
from __future__ import annotations

import asyncio
from copy import deepcopy
import json
import logging
from typing import Any

from homeassistant.core import HomeAssistant
from homeassistant.exceptions import HomeAssistantError

from .const import CONF_CAMERA_WAKE, CONF_CAMERA_WAKE_CAMERAS, CONF_CAMERA_WAKE_SECONDS, DEFAULT_CAMERA_WAKE_SECONDS

_LOGGER = logging.getLogger(__name__)
AWAKE_FOR = 60
WAKE_TIMEOUT = 30
CACHE_LIMIT = 64
_CACHE = 'sentient_camera_wake'


def clear(hass):
    for row in hass.data.pop(_CACHE, {}).values():
        if not row['task'].done():
            row['task'].cancel()


def wanted(options: dict[str, Any], camera: str) -> bool:
    if not options.get(CONF_CAMERA_WAKE):
        return False
    only = options.get(CONF_CAMERA_WAKE_CAMERAS) or []
    return not only or camera in only


async def async_wake(hass: HomeAssistant, options: dict[str, Any], camera: str) -> bool:
    """Wait for a shared wake and stabilisation; only cache a completed success."""
    if not camera or not wanted(options, camera):
        return False
    actions = deepcopy(options[CONF_CAMERA_WAKE])
    delay = float(options.get(CONF_CAMERA_WAKE_SECONDS, DEFAULT_CAMERA_WAKE_SECONDS) or 0)
    signature = json.dumps([actions, delay], sort_keys=True, default=str)
    cache = hass.data.setdefault(_CACHE, {})
    loop = asyncio.get_running_loop()
    while (row := cache.get(camera)) is not None:
        if not row['task'].done():
            await asyncio.shield(row['task'])
            if row['signature'] == signature:
                return False
            continue
        if row['signature'] == signature and row.get('ready') is not None and loop.time() - row['ready'] < AWAKE_FOR:
            return False
        cache.pop(camera, None)
    for eid, row in list(cache.items()):
        if row['task'].done() and (row.get('ready') is None or loop.time() - row['ready'] >= AWAKE_FOR):
            cache.pop(eid, None)
    if len(cache) >= CACHE_LIMIT:
        idle = next((eid for eid, row in cache.items() if row['task'].done()), None)
        if idle is None:
            raise HomeAssistantError('Camera wake queue is full; no fresh image was checked')
        cache.pop(idle)
    row = {'signature': signature, 'ready': None}
    task = hass.async_create_task(_perform(hass, camera, actions, delay, cache, row), eager_start=False)
    row['task'] = task
    cache[camera] = row
    # A cancelled caller does not cancel a wake another caller still needs.
    task.add_done_callback(lambda completed: None if completed.cancelled() else completed.exception())
    return await asyncio.shield(task)


async def _perform(hass, camera, actions, delay, cache, row):
    from .shortcuts import async_run_actions
    from .never import touches

    try:
        async with asyncio.timeout(WAKE_TIMEOUT):
            if touches(hass, actions):
                raise HomeAssistantError('Camera wake actions include a never-touch device')
            await async_run_actions(hass, actions, f'wake {camera}', variables={'camera': camera})
            await asyncio.sleep(delay)
        if hass.data.get(_CACHE) is not cache:
            raise asyncio.CancelledError()
        row['ready'] = asyncio.get_running_loop().time()
        return True
    except asyncio.CancelledError:
        if cache.get(camera) is row:
            cache.pop(camera, None)
        raise
    except Exception as err:
        if cache.get(camera) is row:
            cache.pop(camera, None)
        _LOGGER.warning("Sentient couldn't wake %s; no fresh image was checked: %s", camera, err)
        raise HomeAssistantError(f"Could not wake {camera}; image freshness is unconfirmed") from err
