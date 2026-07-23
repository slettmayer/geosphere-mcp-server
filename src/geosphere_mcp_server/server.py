"""MCP server exposing GeoSphere Austria + Open-Meteo weather tools.

Three tools mirror the OpenWeatherMap MCP surface they replace
(``get_current_weather`` / ``get_hourly_forecast`` / ``get_daily_forecast``)
so existing agent-prompt routing transfers unchanged. Current and hourly try
the high-resolution GeoSphere path first and transparently fall back to
Open-Meteo when the point is outside GeoSphere coverage; daily is always
Open-Meteo. Tools never raise — every failure resolves to a short markdown
error line.
"""

from __future__ import annotations

import asyncio
import logging
from collections.abc import Awaitable, Callable
from datetime import UTC, date, datetime, timedelta

import aiohttp
from mcp.server.fastmcp import FastMCP

from geosphere_mcp_server import format as fmt
from geosphere_mcp_server import openmeteo_api, weather
from geosphere_mcp_server.const import (
    AROME_MAX_HOURS,
    OPENMETEO_MAX_DAYS,
    OPENMETEO_MAX_HOURS,
)
from geosphere_mcp_server.geosphere_api import (
    GeoSphereOutOfDomainError,
    GeoSphereRateLimitError,
)

_LOGGER = logging.getLogger(__name__)

# Retry a rate-limited GeoSphere request once when the server asks us to wait
# no longer than this; otherwise surface the limit to the caller immediately.
RATE_LIMIT_RETRY_MAX_S = 5.0

mcp = FastMCP(
    "geosphere",
    instructions=(
        "Weather forecasts, current conditions, and multi-day outlooks for any "
        "location worldwide. Pass a decimal latitude and longitude — geocode "
        "city names to coordinates yourself; this server has no geocoder. "
        "Austria and the Alpine region are served by high-resolution GeoSphere "
        "Austria data (AROME/INCA/C-LAEF); everywhere else automatically falls "
        "back to Open-Meteo, and each response states its source. Use "
        "get_current_weather for conditions now, get_hourly_forecast for the "
        "next hours (up to ~60 h on GeoSphere, 48 h on the Open-Meteo "
        "fallback), and get_daily_forecast for a 1–16 day outlook worldwide "
        "(by a day count or an explicit start_date/end_date range)."
    ),
)


def _clamp(value: int, low: int, high: int) -> int:
    """Clamp ``value`` into the inclusive ``[low, high]`` range."""
    return max(low, min(value, high))


def _rate_limit_message(retry_after: float | None, *, note_daily: bool) -> str:
    """Build the user-facing GeoSphere rate-limit line."""
    if retry_after is not None:
        message = f"⚠️ GeoSphere rate limit exceeded (retry in {int(retry_after)}s)"
    else:
        message = "⚠️ GeoSphere rate limit exceeded (retry shortly)"
    if note_daily:
        message += " — get_daily_forecast still works (Open-Meteo)."
    return message


async def _guarded(
    work: Callable[[], Awaitable[str]],
    *,
    note_daily: bool,
) -> str:
    """Run ``work`` translating every failure into a markdown error line.

    Handles the single retry-once behaviour for short GeoSphere rate limits;
    ``work`` is responsible for the out-of-domain → Open-Meteo fallback.
    """
    try:
        return await work()
    except TimeoutError:
        return "⚠️ Timeout fetching weather data"
    except GeoSphereRateLimitError as err:
        retry_after = err.retry_after
        if retry_after is not None and retry_after <= RATE_LIMIT_RETRY_MAX_S:
            _LOGGER.info("GeoSphere rate limited, retrying in %ss", retry_after)
            await asyncio.sleep(retry_after)
            try:
                return await work()
            except Exception as retry_err:  # noqa: BLE001
                _LOGGER.warning("GeoSphere retry failed: %s", retry_err)
        return _rate_limit_message(retry_after, note_daily=note_daily)
    except Exception as err:  # noqa: BLE001
        _LOGGER.warning("Weather fetch failed: %s", err)
        return "⚠️ No weather data available"


def _parse_start(start: str | None) -> tuple[datetime | None, str | None]:
    """Parse an ISO start string; return (datetime, error-line-or-None)."""
    if start is None:
        return None, None
    try:
        parsed = datetime.fromisoformat(start)
    except ValueError:
        return None, (
            f"⚠️ Invalid start time '{start}'; use ISO 8601 (e.g. 2026-07-22T15:00)"
        )
    if parsed.tzinfo is None:
        parsed = parsed.replace(tzinfo=UTC)
    return parsed, None


def _parse_date(value: str, label: str) -> tuple[date | None, str | None]:
    """Parse an ISO calendar date; return (date, error-line-or-None)."""
    try:
        return date.fromisoformat(value), None
    except ValueError:
        return None, (
            f"⚠️ Invalid {label} '{value}'; use an ISO 8601 date (e.g. 2026-07-25)"
        )


@mcp.tool()
async def get_current_weather(latitude: float, longitude: float) -> str:
    """Get current weather conditions for a location.

    High-resolution GeoSphere Austria data (AROME/INCA) is used inside its
    Austria/Alps coverage; elsewhere the tool falls back to Open-Meteo. The
    response states which source served it.

    Args:
        latitude: Decimal latitude (e.g. 48.2208 for Vienna). Geocode city
            names to coordinates yourself.
        longitude: Decimal longitude (e.g. 16.3738 for Vienna).
    """

    async def work() -> str:
        async with aiohttp.ClientSession() as session:
            try:
                current = await weather.async_fetch_current_conditions(
                    session, latitude, longitude
                )
                data = fmt.normalize_current_geosphere(current, latitude, longitude)
            except GeoSphereOutOfDomainError:
                body = await openmeteo_api.async_get_current(
                    session, latitude, longitude
                )
                data = fmt.normalize_current_openmeteo(body, latitude, longitude)
            return fmt.render_current(data)

    return await _guarded(work, note_daily=True)


@mcp.tool()
async def get_hourly_forecast(
    latitude: float,
    longitude: float,
    hours: int = 24,
    start: str | None = None,
) -> str:
    """Get an hour-by-hour weather forecast for a location.

    High-resolution GeoSphere AROME data (up to ~60 h, with C-LAEF
    precipitation probability) is used inside its Austria/Alps coverage;
    elsewhere the tool falls back to Open-Meteo (up to 48 h). The response
    states which source served it.

    Args:
        latitude: Decimal latitude (e.g. 48.2208 for Vienna). Geocode city
            names to coordinates yourself.
        longitude: Decimal longitude (e.g. 16.3738 for Vienna).
        hours: Number of forecast hours (default 24, clamped to 1–60 on
            GeoSphere / 1–48 on the Open-Meteo fallback).
        start: Optional ISO 8601 start time (e.g. "2026-07-22T15:00"); the
            forecast begins at/after this instant instead of now.
    """
    start_dt, error = _parse_start(start)
    if error is not None:
        return error

    geosphere_hours = _clamp(hours, 1, AROME_MAX_HOURS)
    fallback_hours = _clamp(hours, 1, OPENMETEO_MAX_HOURS)

    async def work() -> str:
        async with aiohttp.ClientSession() as session:
            try:
                assembled = await weather.async_fetch_hourly_forecast(
                    session,
                    latitude,
                    longitude,
                    hours=geosphere_hours,
                    start=start_dt,
                )
                data = fmt.normalize_hourly_geosphere(
                    assembled, latitude, longitude, geosphere_hours
                )
            except GeoSphereOutOfDomainError:
                body = await openmeteo_api.async_get_hourly(
                    session, latitude, longitude, hours=fallback_hours
                )
                data = fmt.normalize_hourly_openmeteo(
                    body, latitude, longitude, fallback_hours, start=start_dt
                )
            return fmt.render_hourly(data)

    return await _guarded(work, note_daily=True)


@mcp.tool()
async def get_daily_forecast(
    latitude: float,
    longitude: float,
    days: int = 7,
    start_date: str | None = None,
    end_date: str | None = None,
) -> str:
    """Get a multi-day weather forecast for a location.

    Always served by Open-Meteo (worldwide, including Austria), covering 1–16
    days with daily highs/lows, precipitation, and wind. Request either a count
    of days from today (``days``) or an explicit calendar range
    (``start_date``/``end_date``).

    Args:
        latitude: Decimal latitude (e.g. 48.2208 for Vienna). Geocode city
            names to coordinates yourself.
        longitude: Decimal longitude (e.g. 16.3738 for Vienna).
        days: Number of forecast days from today (default 7, clamped to 1–16).
            Ignored when start_date/end_date are given.
        start_date: Optional first forecast day as an ISO date (YYYY-MM-DD).
            For a named period like "the weekend" or "next Tuesday", call
            GetDateTime first and pass the exact calendar dates here instead of
            converting the period into a day count.
        end_date: Optional last forecast day, inclusive (YYYY-MM-DD); defaults
            to start_date (a single day). The range is capped at 16 days.
    """
    if start_date is not None or end_date is not None:
        raw_start = start_date if start_date is not None else end_date
        raw_end = end_date if end_date is not None else start_date
        start, error = _parse_date(raw_start, "start_date")
        if error is not None:
            return error
        end, error = _parse_date(raw_end, "end_date")
        if error is not None:
            return error
        if end < start:
            return f"⚠️ end_date '{end}' is before start_date '{start}'"
        # Cap the inclusive span at the Open-Meteo maximum, matching the
        # silent clamp applied to the days count below.
        end = min(end, start + timedelta(days=OPENMETEO_MAX_DAYS - 1))
        render_days = (end - start).days + 1
        api_kwargs: dict[str, object] = {
            "start_date": start.isoformat(),
            "end_date": end.isoformat(),
        }
    else:
        render_days = _clamp(days, 1, OPENMETEO_MAX_DAYS)
        api_kwargs = {"days": render_days}

    async def work() -> str:
        async with aiohttp.ClientSession() as session:
            body = await openmeteo_api.async_get_daily(
                session, latitude, longitude, **api_kwargs
            )
        return fmt.render_daily(
            fmt.normalize_daily_openmeteo(body, latitude, longitude, render_days)
        )

    return await _guarded(work, note_daily=False)


def main() -> None:
    """Run the GeoSphere MCP server over stdio."""
    mcp.run()


if __name__ == "__main__":
    main()
