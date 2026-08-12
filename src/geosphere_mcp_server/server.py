"""MCP server exposing GeoSphere Austria weather tools.

Two of the tools mirror the OpenWeatherMap MCP surface they replace
(``get_current_weather`` / ``get_hourly_forecast``) so existing agent-prompt
routing transfers unchanged; ``get_storm_outlook`` and ``get_air_quality`` are
additions. Every tool is served by GeoSphere Austria alone, so coverage stops
at the AROME grid — Austria and the Alpine region — and the horizon stops at
~60 h. Tools never raise — every failure, including a point outside coverage,
resolves to a short markdown error line.
"""

from __future__ import annotations

import asyncio
import logging
from collections.abc import Awaitable, Callable
from datetime import UTC, datetime

import aiohttp
from mcp.server import MCPServer

from geosphere_mcp_server import __version__, air_quality, weather
from geosphere_mcp_server import format as fmt
from geosphere_mcp_server.const import AROME_MAX_HOURS
from geosphere_mcp_server.geosphere_api import (
    GeoSphereOutOfDomainError,
    GeoSphereRateLimitError,
    GeoSphereTimeoutError,
)

_LOGGER = logging.getLogger(__name__)

# Retry a rate-limited GeoSphere request once when the server asks us to wait
# no longer than this; otherwise surface the limit to the caller immediately.
RATE_LIMIT_RETRY_MAX_S = 5.0

# Returned verbatim for a point outside the grid of whichever dataset the tool
# asked for. Phrased so a caller learns the location is unservable rather than
# that the request failed — retrying will not help.
#
# Deliberately does NOT name a grid. The tools do not all query the same one:
# the three forecast tools go to AROME (2.5 km) while `get_air_quality` goes to
# WRF-Chem (3 km), so a point can be inside one and outside the other. Naming
# AROME here would tell an air-quality caller its location is unservable when
# the other three tools answer it fine.
OUT_OF_DOMAIN_MESSAGE = (
    "⚠️ Outside coverage — this server only serves Austria and the Alpine region."
)

mcp = MCPServer(
    "geosphere",
    # v2 advertises this verbatim and defaults it to "" (v1 had no such
    # parameter and reported the SDK's own version instead).
    version=__version__,
    instructions=(
        "High-resolution weather forecasts and current conditions for "
        "**Austria and the Alpine region only**, from GeoSphere Austria "
        "(AROME/INCA/C-LAEF/WRF-Chem). Pass a decimal latitude and longitude — "
        "geocode city names to coordinates yourself; this server has no "
        "geocoder. Points outside that region are not served and return an "
        "out-of-coverage notice, so use a different weather source for the "
        "rest of the world. The forecast horizon is ~60 h; there is no "
        "multi-day outlook beyond it. Use get_current_weather for conditions "
        "now, get_hourly_forecast for the next hours, get_storm_outlook for "
        "peak gusts and thunderstorm timing, and get_air_quality for "
        "pollutant concentrations and the European AQI."
    ),
)


def _clamp(value: int, low: int, high: int) -> int:
    """Clamp ``value`` into the inclusive ``[low, high]`` range."""
    return max(low, min(value, high))


def _rate_limit_message(retry_after: float | None) -> str:
    """Build the user-facing GeoSphere rate-limit line."""
    if retry_after is not None:
        return f"⚠️ GeoSphere rate limit exceeded (retry in {int(retry_after)}s)"
    return "⚠️ GeoSphere rate limit exceeded (retry shortly)"


async def _guarded(work: Callable[[], Awaitable[str]]) -> str:
    """Run ``work`` translating every failure into a markdown error line.

    Handles the single retry-once behaviour for short GeoSphere rate limits.
    Out-of-domain is caught here rather than inside each tool: with no
    worldwide source behind it, every tool answers an uncovered point the same
    way, and it is the one failure the caller can act on.
    """
    try:
        return await work()
    except GeoSphereOutOfDomainError:
        return OUT_OF_DOMAIN_MESSAGE
    except (TimeoutError, GeoSphereTimeoutError):
        # The API client wraps asyncio timeouts into its own typed error, so
        # both that and a bare TimeoutError must be handled here.
        return "⚠️ Timeout fetching weather data"
    except GeoSphereRateLimitError as err:
        retry_after = err.retry_after
        if retry_after is not None and retry_after <= RATE_LIMIT_RETRY_MAX_S:
            _LOGGER.info("GeoSphere rate limited, retrying in %ss", retry_after)
            await asyncio.sleep(retry_after)
            try:
                return await work()
            except GeoSphereOutOfDomainError:
                return OUT_OF_DOMAIN_MESSAGE
            except Exception as retry_err:  # noqa: BLE001
                _LOGGER.warning("GeoSphere retry failed: %s", retry_err)
        return _rate_limit_message(retry_after)
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


@mcp.tool()
async def get_current_weather(latitude: float, longitude: float) -> str:
    """Get current weather conditions for a location.

    High-resolution GeoSphere Austria data (AROME/INCA/nowcast). Serves Austria
    and the Alpine region only; a point outside that grid returns an
    out-of-coverage notice. The response states which datasets served it.

    Args:
        latitude: Decimal latitude (e.g. 48.2208 for Vienna). Geocode city
            names to coordinates yourself.
        longitude: Decimal longitude (e.g. 16.3738 for Vienna).
    """

    async def work() -> str:
        async with aiohttp.ClientSession() as session:
            current = await weather.async_fetch_current_conditions(
                session, latitude, longitude
            )
            data = fmt.normalize_current_geosphere(current, latitude, longitude)
            return fmt.render_current(data)

    return await _guarded(work)


@mcp.tool()
async def get_hourly_forecast(
    latitude: float,
    longitude: float,
    hours: int = 24,
    start: str | None = None,
) -> str:
    """Get an hour-by-hour weather forecast for a location.

    High-resolution GeoSphere AROME data (up to ~60 h, with C-LAEF
    precipitation probability). Serves Austria and the Alpine region only; a
    point outside that grid returns an out-of-coverage notice.

    Args:
        latitude: Decimal latitude (e.g. 48.2208 for Vienna). Geocode city
            names to coordinates yourself.
        longitude: Decimal longitude (e.g. 16.3738 for Vienna).
        hours: Number of forecast hours (default 24, clamped to 1–60).
        start: Optional ISO 8601 start time (e.g. "2026-07-22T15:00"); the
            forecast begins at/after this instant instead of now.
    """
    start_dt, error = _parse_start(start)
    if error is not None:
        return error

    forecast_hours = _clamp(hours, 1, AROME_MAX_HOURS)

    async def work() -> str:
        async with aiohttp.ClientSession() as session:
            assembled = await weather.async_fetch_hourly_forecast(
                session,
                latitude,
                longitude,
                hours=forecast_hours,
                start=start_dt,
            )
            data = fmt.normalize_hourly_geosphere(
                assembled, latitude, longitude, forecast_hours
            )
            return fmt.render_hourly(data)

    return await _guarded(work)


@mcp.tool()
async def get_storm_outlook(latitude: float, longitude: float) -> str:
    """Get the severe-weather outlook for a location: gusts and thunderstorms.

    Reports the peak wind gust within the next hour and the next 12 hours,
    whether a thunderstorm is expected within the next hour, when the next
    thunderstorm is expected across the whole forecast horizon, and the peak
    CAPE over the next 12 hours. Deliberately reports no severity verdict —
    what counts as dangerous is the caller's judgement.

    High-resolution GeoSphere AROME data, whose CAPE is gated by convective
    inhibition. Serves Austria and the Alpine region only; a point outside that
    grid returns an out-of-coverage notice.

    The thunderstorm scan covers the AROME horizon, nominally ~60 h but shorter
    when a run is stale or truncated. A "none in the next N h" answer names the
    horizon it actually covered; it is not an all-clear beyond that.

    Horizons round up to whole hours: the "next hour" window covers the hour
    already under way plus the next one. A thunderstorm timestamp at or before
    the current time means one is already in progress.

    Args:
        latitude: Decimal latitude (e.g. 48.2208 for Vienna). Geocode city
            names to coordinates yourself.
        longitude: Decimal longitude (e.g. 16.3738 for Vienna).
    """

    async def work() -> str:
        async with aiohttp.ClientSession() as session:
            assembled = await weather.async_fetch_hourly_forecast(
                session,
                latitude,
                longitude,
                hours=AROME_MAX_HOURS,
                # The ensemble only adds precipitation probability, which the
                # outlook does not report — skip the request.
                include_ensemble=False,
            )
            data = fmt.normalize_outlook_geosphere(assembled, latitude, longitude)
            return fmt.render_outlook(data)

    return await _guarded(work)


@mcp.tool()
async def get_air_quality(latitude: float, longitude: float) -> str:
    """Get air quality for a location: pollutants now and the AQI outlook.

    Reports current NO₂, O₃, PM10 and PM2.5 surface concentrations plus the
    European Air Quality Index (1-6 EEA bands) for today, tomorrow and in two
    days.

    GeoSphere's WRF-Chem forecast (3 km) serves Austria and the Alpine region
    only; a point outside that grid returns an out-of-coverage notice. These
    are model forecasts, not station measurements.

    An in-coverage point can still come back empty when a WRF-Chem run is
    stale or incomplete — the response then says so and names the source that
    drew the blank, which is a different answer from being out of coverage.

    Args:
        latitude: Decimal latitude (e.g. 48.2208 for Vienna). Geocode city
            names to coordinates yourself.
        longitude: Decimal longitude (e.g. 16.3738 for Vienna).
    """

    async def work() -> str:
        async with aiohttp.ClientSession() as session:
            merged = await air_quality.async_fetch_air_quality(
                session, latitude, longitude
            )
            data = fmt.normalize_air_quality_geosphere(merged, latitude, longitude)
            return fmt.render_air_quality(data)

    return await _guarded(work)


def main() -> None:
    """Run the GeoSphere MCP server over stdio."""
    mcp.run()


if __name__ == "__main__":
    main()
