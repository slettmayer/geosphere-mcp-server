"""In-process cache of the forecast runs behind the hourly and storm tools.

The GeoSphere API takes anywhere from under a second to ~20 s to answer a
forecast request, while a run only changes every 3 h. Each entry holds the
task fetching one dataset's full horizon for one point, so concurrent calls
share a single request, a finished run is reused until it expires, and a
request a caller stopped waiting for still completes into the cache.

Each fetch owns its HTTP session: it may outlive the tool call that started
it. Failures are never cached.
"""

from __future__ import annotations

import asyncio
import logging
import os
from collections.abc import Awaitable, Callable
from dataclasses import dataclass
from functools import partial
from time import monotonic

from geosphere_mcp_server.const import (
    FORECAST_CACHE_COORD_DECIMALS,
    FORECAST_CACHE_DEFAULT_MINUTES,
    FORECAST_CACHE_TTL_ENV,
)
from geosphere_mcp_server.geosphere_api import GeoSphereResponse

_LOGGER = logging.getLogger(__name__)

CacheKey = tuple[str, tuple[str, ...], float, float]


@dataclass(slots=True)
class _Entry:
    """One dataset run for one point: the fetch task and when it started."""

    task: asyncio.Task[GeoSphereResponse]
    started: float


_ENTRIES: dict[CacheKey, _Entry] = {}
# Strong references to running fetches: with caching off nothing else holds
# one, and the event loop keeps only weak references to tasks.
_RUNNING: set[asyncio.Task[GeoSphereResponse]] = set()


def cache_ttl_s() -> float:
    """The cache lifetime in seconds, from the environment or the default."""
    raw = os.environ.get(FORECAST_CACHE_TTL_ENV, "").strip()
    if not raw:
        return FORECAST_CACHE_DEFAULT_MINUTES * 60
    try:
        minutes = float(raw)
    except ValueError:
        _LOGGER.warning(
            "Ignoring %s=%r (not a number); using %s minutes",
            FORECAST_CACHE_TTL_ENV,
            raw,
            FORECAST_CACHE_DEFAULT_MINUTES,
        )
        return FORECAST_CACHE_DEFAULT_MINUTES * 60
    return max(0.0, minutes * 60)


def cache_key(
    resource_id: str,
    parameters: tuple[str, ...],
    latitude: float,
    longitude: float,
) -> CacheKey:
    """Key one dataset's run for one point, with the coordinates rounded."""
    return (
        resource_id,
        parameters,
        round(latitude, FORECAST_CACHE_COORD_DECIMALS),
        round(longitude, FORECAST_CACHE_COORD_DECIMALS),
    )


def forecast_run_task(
    key: CacheKey,
    fetch: Callable[[], Awaitable[GeoSphereResponse]],
) -> asyncio.Task[GeoSphereResponse]:
    """Return the task for ``key``: a usable cached one, or a new ``fetch()``.

    Await the result through ``asyncio.shield`` so a caller that gives up
    cannot cancel a request other callers share.
    """
    loop = asyncio.get_running_loop()
    ttl = cache_ttl_s()
    now = monotonic()
    _prune(now, ttl)

    entry = _ENTRIES.get(key)
    if entry is not None and _usable(entry, loop, now, ttl):
        return entry.task

    task: asyncio.Task[GeoSphereResponse] = loop.create_task(fetch())
    _RUNNING.add(task)
    if ttl > 0:
        _ENTRIES[key] = _Entry(task=task, started=now)
    task.add_done_callback(partial(_settle, key))
    return task


def clear() -> None:
    """Forget every entry (tests; a running fetch still completes)."""
    _ENTRIES.clear()


def _usable(
    entry: _Entry,
    loop: asyncio.AbstractEventLoop,
    now: float,
    ttl: float,
) -> bool:
    """Whether ``entry`` can answer a call on ``loop`` at ``now``."""
    task = entry.task
    if task.get_loop() is not loop:
        return False
    if not task.done():
        return True
    if task.cancelled() or task.exception() is not None:
        return False
    return now - entry.started < ttl


def _prune(now: float, ttl: float) -> None:
    """Drop finished entries past their lifetime."""
    expired = [
        key
        for key, entry in _ENTRIES.items()
        if entry.task.done() and now - entry.started >= ttl
    ]
    for key in expired:
        del _ENTRIES[key]


def _settle(key: CacheKey, task: asyncio.Task[GeoSphereResponse]) -> None:
    """Release a finished fetch; drop its entry if it failed.

    Reading the exception here also marks it retrieved, so a late fetch that
    nobody awaited fails quietly instead of as "exception never retrieved".
    """
    _RUNNING.discard(task)
    failed = task.cancelled() or task.exception() is not None
    if not failed:
        return
    if not task.cancelled():
        _LOGGER.debug("Forecast fetch for %s failed: %s", key[0], task.exception())
    entry = _ENTRIES.get(key)
    if entry is not None and entry.task is task:
        del _ENTRIES[key]
