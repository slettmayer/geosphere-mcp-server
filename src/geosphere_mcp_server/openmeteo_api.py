"""Async client for the Open-Meteo forecast API.

Module-level async functions (oebb-style). Open-Meteo is keyless, worldwide,
and serves the daily tool everywhere plus the current/hourly fallback when a
point lies outside GeoSphere coverage. Wind is requested in m/s to stay
consistent with GeoSphere. These functions raise typed exceptions; the server
layer converts them to user-facing text.
"""

from __future__ import annotations

import asyncio
import logging
import math
from typing import Any

import aiohttp

from geosphere_mcp_server.const import (
    OPENMETEO_AIR_QUALITY_BASE_URL,
    OPENMETEO_AIR_QUALITY_DAYS,
    OPENMETEO_AIR_QUALITY_VARIABLES,
    OPENMETEO_API_BASE_URL,
    OPENMETEO_CURRENT_DAILY_VARIABLES,
    OPENMETEO_CURRENT_VARIABLES,
    OPENMETEO_DAILY_VARIABLES,
    OPENMETEO_HOURLY_VARIABLES,
    OPENMETEO_MAX_DAYS,
    OPENMETEO_TIMEOUT,
    OPENMETEO_WIND_SPEED_UNIT,
)

_LOGGER = logging.getLogger(__name__)


class OpenMeteoApiError(Exception):
    """Base error talking to the Open-Meteo API."""


class OpenMeteoConnectionError(OpenMeteoApiError):
    """Network-level failure."""


class OpenMeteoTimeoutError(OpenMeteoConnectionError):
    """Request exceeded ``OPENMETEO_TIMEOUT``."""


async def _async_get(
    session: aiohttp.ClientSession,
    params: dict[str, str],
    base_url: str,
) -> dict[str, Any]:
    """GET the Open-Meteo endpoint and return the decoded JSON body."""
    try:
        async with asyncio.timeout(OPENMETEO_TIMEOUT):
            resp = await session.get(base_url, params=params)
            body = await resp.json()
            if resp.status >= 400 or (isinstance(body, dict) and body.get("error")):
                reason = (
                    body.get("reason", resp.status)
                    if isinstance(body, dict)
                    else resp.status
                )
                raise OpenMeteoApiError(f"Open-Meteo API error: {reason}")
    except TimeoutError as err:
        raise OpenMeteoTimeoutError(
            f"Timed out after {OPENMETEO_TIMEOUT}s talking to the Open-Meteo API"
        ) from err
    except aiohttp.ClientError as err:
        raise OpenMeteoConnectionError(
            f"Error connecting to the Open-Meteo API: {err}"
        ) from err

    if not isinstance(body, dict):
        raise OpenMeteoApiError("Unexpected Open-Meteo API response shape")
    return body


async def async_get_current(
    session: aiohttp.ClientSession,
    latitude: float,
    longitude: float,
    base_url: str = OPENMETEO_API_BASE_URL,
) -> dict[str, Any]:
    """Fetch current conditions (+ today's sunrise/sunset) worldwide.

    Returns the raw Open-Meteo body containing ``current``, ``current_units``,
    ``daily`` (sunrise/sunset), ``timezone`` and coordinate fields.
    """
    params = {
        "latitude": str(latitude),
        "longitude": str(longitude),
        "current": ",".join(OPENMETEO_CURRENT_VARIABLES),
        "daily": ",".join(OPENMETEO_CURRENT_DAILY_VARIABLES),
        "wind_speed_unit": OPENMETEO_WIND_SPEED_UNIT,
        "forecast_days": "1",
        "timezone": "auto",
    }
    return await _async_get(session, params, base_url)


async def async_get_hourly(
    session: aiohttp.ClientSession,
    latitude: float,
    longitude: float,
    hours: int = 24,
    lead_hours: float = 0.0,
    base_url: str = OPENMETEO_API_BASE_URL,
) -> dict[str, Any]:
    """Fetch the hourly forecast worldwide.

    ``forecast_days`` is derived from ``hours`` (ceil to whole days). This API
    counts its forecast days from **local midnight**, not from now, so a window
    that starts later than now needs the days in between as well: pass
    ``lead_hours`` — the hours between now and the caller's ``start`` — or the
    requested window falls off the end of the response and the caller filters
    every row away. Returns the raw Open-Meteo body containing ``hourly``,
    ``hourly_units``, ``timezone`` and coordinate fields.
    """
    span = max(hours, 1) + max(lead_hours, 0.0)
    # +1 day: the days are counted from local midnight, so the tail of the
    # window would otherwise be clipped by however far into the day it is.
    #
    # Clamped to OPENMETEO_MAX_DAYS: `lead_hours` comes from a caller-supplied
    # `start`, so nothing upstream bounds it. Asking for 17 days is rejected
    # outright ("Forecast days is invalid. Allowed range 0 to 16"), which would
    # fail the whole call for a start the API can in fact serve. Clamping
    # instead returns everything available and lets the caller's own window
    # filter come up empty, which is the honest answer for a start past the
    # horizon.
    forecast_days = min(max(1, math.ceil(span / 24) + 1), OPENMETEO_MAX_DAYS)
    params = {
        "latitude": str(latitude),
        "longitude": str(longitude),
        "hourly": ",".join(OPENMETEO_HOURLY_VARIABLES),
        "wind_speed_unit": OPENMETEO_WIND_SPEED_UNIT,
        "forecast_days": str(forecast_days),
        "timezone": "auto",
    }
    return await _async_get(session, params, base_url)


async def async_get_air_quality(
    session: aiohttp.ClientSession,
    latitude: float,
    longitude: float,
    base_url: str = OPENMETEO_AIR_QUALITY_BASE_URL,
) -> dict[str, Any]:
    """Fetch the hourly air-quality forecast worldwide (CAMS, keyless).

    A different host from the weather endpoints, same request/response shape.
    Returns the raw body containing ``hourly``, ``hourly_units``, ``timezone``
    and coordinate fields. Unlike GeoSphere this API publishes no daily index,
    so the per-day values are derived from the hourly series downstream.
    """
    params = {
        "latitude": str(latitude),
        "longitude": str(longitude),
        "hourly": ",".join(OPENMETEO_AIR_QUALITY_VARIABLES),
        "forecast_days": str(OPENMETEO_AIR_QUALITY_DAYS),
        "timezone": "auto",
    }
    return await _async_get(session, params, base_url)


async def async_get_daily(
    session: aiohttp.ClientSession,
    latitude: float,
    longitude: float,
    days: int = 7,
    start_date: str | None = None,
    end_date: str | None = None,
    base_url: str = OPENMETEO_API_BASE_URL,
) -> dict[str, Any]:
    """Fetch the daily forecast worldwide.

    Pass ``days`` for a count from today, or both ``start_date`` and
    ``end_date`` (ISO ``YYYY-MM-DD``) for an explicit calendar range; the range
    takes precedence and replaces ``forecast_days``. Returns the raw Open-Meteo
    body containing ``daily``, ``daily_units``, ``timezone`` and coordinate
    fields.
    """
    params = {
        "latitude": str(latitude),
        "longitude": str(longitude),
        "daily": ",".join(OPENMETEO_DAILY_VARIABLES),
        "wind_speed_unit": OPENMETEO_WIND_SPEED_UNIT,
        "timezone": "auto",
    }
    if start_date is not None and end_date is not None:
        params["start_date"] = start_date
        params["end_date"] = end_date
    else:
        params["forecast_days"] = str(max(days, 1))
    return await _async_get(session, params, base_url)
