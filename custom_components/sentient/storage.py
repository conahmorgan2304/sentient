"""Checked writes for privacy deletion and its recovery record."""

from __future__ import annotations

import asyncio
from inspect import signature

from homeassistant.helpers.storage import Store

# HA 2025.10 passes the path explicitly. Later releases derive it from the store.
_WRITER_TAKES_PATH = "path" in signature(Store._async_write_data).parameters


async def _async_write(store, data):
    """Write under the Store lock and let storage errors reach the caller."""
    if store._read_only:
        raise OSError(f"Storage is read-only: {store.key}")
    file_io = getattr(store, "_io", None)
    if file_io is None:
        store._manager.async_invalidate(store.key)
        file_io = store
    else:
        file_io.async_invalidate_cache()
    atomic = file_io._atomic_writes
    file_io._atomic_writes = True
    try:
        if _WRITER_TAKES_PATH:
            await store._async_write_data(store.path, dict(data))
        else:
            await store._async_write_data(dict(data))
    finally:
        file_io._atomic_writes = atomic


class RecoveryStore(Store):
    """Privacy journal. A failed write must never look like a committed one."""

    async def async_save(self, data):
        async with self._write_lock:
            await _async_write(self, {
                "version": self.version, "minor_version": self.minor_version,
                "key": self.key, "data": data,
            })


async def async_flush_pending(stores):
    """Flush the saves queued by the scrub, including writes a timer took meanwhile."""
    queued = [(store, dict(store._data)) for store in stores if store is not None and store._data is not None]

    async def flush(store, fallback):
        async with store._write_lock:
            data = store._data or fallback
            store._async_cleanup_delay_listener()
            store._async_cleanup_final_write_listener()
            store._data = None
            try:
                await _async_write(store, data)
            except BaseException:
                # A retry in the same process still needs to write the scrubbed
                # value. Keep any newer update queued while this write ran.
                if store._data is None:
                    store._data = data
                raise

    results = await asyncio.gather(*(flush(store, data) for store, data in queued), return_exceptions=True)
    for result in results:
        if isinstance(result, BaseException):
            raise result
