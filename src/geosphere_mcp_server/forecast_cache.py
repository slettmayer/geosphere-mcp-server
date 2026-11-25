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
from datetime import UTC, datetime, timedelta
from functools import partial
from time import monotonic

from geosphere_mcp_server.const import (
    FORECAST_CACHE_COORD_DECIMALS,
    FORECAST_CACHE_DEFAULT_MINUTES,
    FORECAST_CACHE_MAX_ENTRIES,
    FORECAST_CACHE_TTL_ENV,
)
from geosphere_mcp_server.geosphere_api import GeoSphereResponse

_LOGGER = logging.getLogger(__name__)

CacheKey = tuple[str, tuple[str, ...], float, float]


@dataclass(slots=True)
class _Entry:
    """One dataset run for one point: the fetch task and its expiry rules."""

    task: asyncio.Task[GeoSphereResponse]
    started: float
    max_run_age: timedelta | None


_ENTRIES: dict[CacheKey, _Entry] = {}
# Strong references to running fetches: with caching off nothing else holds
# one, and the event loop keeps only weak references to tasks.
_RUNNING: set[asyncio.Task[GeoSphereResponse]] = set()
# Invalid lifetime values already warned about, so one warns once rather than
# on every fetch for as long as it is set.
_REPORTED_TTL_VALUES: set[str] = set()


def _utcnow() -> datetime:
    """Wall-clock time, for run ages (patched in tests)."""
    return datetime.now(UTC)


def cache_ttl_s() -> float:
    """The cache lifetime in seconds, from the environment or the default."""
    raw = os.environ.get(FORECAST_CACHE_TTL_ENV, "").strip()
    if not raw:
        return FORECAST_CACHE_DEFAULT_MINUTES * 60
    try:
        minutes = float(raw)
    except ValueError:
        if raw not in _REPORTED_TTL_VALUES:
            _REPORTED_TTL_VALUES.add(raw)
            _LOGGER.warning(
                "Ignoring %s=%r (not a number); using %s minutes",
                FORECAST_CACHE_TTL_ENV,
                raw,
                FORECAST_CACHE_DEFAULT_MINUTES,
            )
        return FORECAST_CACHE_DEFAULT_MINUTES * 60
    return max(0.0, minutes * 60)


def cache_enabled() -> bool:
    """Whether finished runs are kept at all."""
    return cache_ttl_s() > 0


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
    max_run_age: timedelta | None = None,
) -> asyncio.Task[GeoSphereResponse]:
    """Return the task for ``key``: a usable cached one, or a new ``fetch()``.

    ``max_run_age`` also expires a cached run once its ``reference_time`` is
    that old, however recently it was fetched: a run is published hours after
    its reference time, so a fetch can already be close to an age limit a
    consumer applies.

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
        _ENTRIES[key] = _Entry(task=task, started=now, max_run_age=max_run_age)
        _enforce_cap()
    task.add_done_callback(partial(_settle, key))
    return task


def clear() -> None:
    """Forget every entry (tests; a running fetch still completes)."""
    _ENTRIES.clear()
    _REPORTED_TTL_VALUES.clear()


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
    if now - entry.started >= ttl:
        return False
    reference = task.result().reference_time
    return (
        entry.max_run_age is None
        or reference is None
        or _utcnow() - reference < entry.max_run_age
    )


def _prune(now: float, ttl: float) -> None:
    """Drop finished entries past their lifetime."""
    expired = [
        key
        for key, entry in _ENTRIES.items()
        if entry.task.done() and now - entry.started >= ttl
    ]
    for key in expired:
        del _ENTRIES[key]


def _enforce_cap() -> None:
    """Keep at most FORECAST_CACHE_MAX_ENTRIES, dropping the oldest finished.

    Running fetches are never dropped: their callers still wait on them.
    """
    excess = len(_ENTRIES) - FORECAST_CACHE_MAX_ENTRIES
    if excess <= 0:
        return
    finished = sorted(
        (entry.started, key) for key, entry in _ENTRIES.items() if entry.task.done()
    )
    for _, key in finished[:excess]:
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
