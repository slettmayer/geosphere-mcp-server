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
    OPENMETEO_API_BASE_URL,
    OPENMETEO_CURRENT_DAILY_VARIABLES,
    OPENMETEO_CURRENT_VARIABLES,
    OPENMETEO_DAILY_VARIABLES,
    OPENMETEO_HOURLY_VARIABLES,
    OPENMETEO_TIMEOUT,
    OPENMETEO_WIND_SPEED_UNIT,
)

_LOGGER = logging.getLogger(__name__)


class OpenMeteoApiError(Exception):
    """Base error talking to the Open-Meteo API."""


class OpenMeteoConnectionError(OpenMeteoApiError):
    """Network-level failure (timeout / connection)."""


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
    except (TimeoutError, aiohttp.ClientError) as err:
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
    base_url: str = OPENMETEO_API_BASE_URL,
) -> dict[str, Any]:
    """Fetch the hourly forecast worldwide.

    ``forecast_days`` is derived from ``hours`` (ceil to whole days). Returns
    the raw Open-Meteo body containing ``hourly``, ``hourly_units``,
    ``timezone`` and coordinate fields.
    """
    forecast_days = max(1, math.ceil(max(hours, 1) / 24))
    params = {
        "latitude": str(latitude),
        "longitude": str(longitude),
        "hourly": ",".join(OPENMETEO_HOURLY_VARIABLES),
        "wind_speed_unit": OPENMETEO_WIND_SPEED_UNIT,
        "forecast_days": str(forecast_days),
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
