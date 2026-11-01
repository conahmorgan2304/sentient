"""Optional release checks. Never installs updates or restarts Home Assistant."""
from __future__ import annotations

import asyncio
from datetime import timedelta
import re

from homeassistant.helpers.aiohttp_client import async_get_clientsession
from homeassistant.helpers.event import async_track_time_interval
from homeassistant.loader import async_get_integration
from homeassistant.util import dt as dt_util

from .const import DOMAIN, CONF_UPDATE_CHECKER

RELEASE_URL = 'https://api.github.com/repos/conahmorgan2304/sentient/releases/latest'
RELEASE_PAGE = 'https://github.com/conahmorgan2304/sentient/releases'
EVENT = 'sentient_update_check'
INTERVAL = timedelta(hours=6)


def version_tuple(value):
    match = re.fullmatch(r'v?(\d+)\.(\d+)\.(\d+)', str(value))
    if not match:
        raise ValueError('Unsupported release version')
    return tuple(int(v) for v in match.groups())


class UpdateChecker:
    def __init__(self, hass):
        self.hass = hass
        self.enabled = False
        self.installed = None
        self.latest = None
        self.checked = None
        self.error = None
        self._unsub = None
        self._task = None
        self._generation = 0
        self._checking = False
        self._next_check = 0

    async def async_start(self, options):
        self.installed = (await async_get_integration(self.hass, DOMAIN)).version
        self.configure(options)

    def configure(self, options):
        enabled = bool(options.get(CONF_UPDATE_CHECKER, False))
        if enabled == self.enabled:
            return
        self.stop()
        self.enabled = enabled
        if enabled:
            self._unsub = async_track_time_interval(self.hass, self._tick, INTERVAL)
            self._start_check()
        self._notify()

    def stop(self):
        self._generation += 1
        self.enabled = False
        if self._unsub:
            self._unsub()
            self._unsub = None
        if self._task and not self._task.done():
            self._task.cancel()
        self._task = None
        self.latest = self.checked = self.error = None
        self._checking = False
        self._next_check = 0

    def _notify(self):
        self.hass.bus.async_fire(EVENT)

    async def _tick(self, _now):
        await self.async_check()

    def _start_check(self):
        if not self.enabled:
            return None
        if self._task and not self._task.done():
            return self._task
        if asyncio.get_running_loop().time() < self._next_check:
            return None
        self._checking = True
        self._next_check = asyncio.get_running_loop().time() + 60
        self._task = self.hass.async_create_background_task(self._fetch(self._generation), 'sentient_update_check')
        return self._task

    async def async_check(self):
        if task := self._start_check():
            try:
                await asyncio.shield(task)
            except asyncio.CancelledError:
                if self.enabled:  # preserve caller cancellation; disabling cancels the check
                    raise
        return self.status()

    async def _fetch(self, generation):
        latest, error = None, None
        try:
            async with asyncio.timeout(10):
                async with async_get_clientsession(self.hass).get(RELEASE_URL, headers={'Accept': 'application/vnd.github+json'}) as response:
                    if response.status in (403, 429):
                        self._next_check = asyncio.get_running_loop().time() + 3600
                        raise ValueError('GitHub limited update checks. Try again after one hour.')
                    response.raise_for_status()
                    data = await response.json()
                    if data.get('draft') or data.get('prerelease'):
                        raise ValueError('No stable release returned')
                    latest = str(data.get('tag_name', ''))
                    version_tuple(latest)
                    version_tuple(self.installed)
        except Exception as err:  # offline checks must not affect conversations
            error = str(err)[:200] or type(err).__name__
        if not self.enabled or generation != self._generation:
            return
        if error is None:
            self.latest = latest.removeprefix('v')
        self.checked = dt_util.utcnow().isoformat()
        self.error = error
        self._checking = False
        self._notify()

    def status(self):
        available = None
        if self.latest:
            available = version_tuple(self.latest) > version_tuple(self.installed)
        return {'enabled': self.enabled, 'installed_version': self.installed,
                'latest_version': self.latest, 'update_available': available,
                'last_checked': self.checked, 'error': self.error, 'release_url': RELEASE_PAGE,
                'checking': self._checking,
                'note': 'Checking only. Install through HACS; restart Home Assistant when required, then refresh this panel.'}
