"""Tests for the FastMCP tool functions in server.py.

The GeoSphere fetch helpers (``weather.async_fetch_*``) and the Open-Meteo
clients (``openmeteo_api.async_get_*``) are patched; the tool functions are
called directly (FastMCP's ``@mcp.tool()`` returns the plain coroutine).
"""

from __future__ import annotations

from datetime import UTC, datetime
from unittest.mock import AsyncMock, patch

import pytest

from geosphere_mcp_server import openmeteo_api, weather
from geosphere_mcp_server.geosphere_api import (
    GeoSphereOutOfDomainError,
    GeoSphereRateLimitError,
)
from geosphere_mcp_server.server import (
    get_current_weather,
    get_daily_forecast,
    get_hourly_forecast,
)

LAT, LON = 48.2208, 16.3738

SAMPLE_CURRENT = {
    "observed_at": datetime(2026, 7, 15, 12, 30, tzinfo=UTC),
    "temperature_c": 21.3,
    "apparent_temperature_c": 20.1,
    "humidity_pct": 55.0,
    "pressure_hpa": 1013.0,
    "wind_speed_ms": 2.7,
    "wind_bearing_deg": 240.0,
    "wind_gust_ms": 6.0,
    "precipitation_1h_mm": 0.0,
    "cloud_cover_pct": 40.0,
    "condition": "partlycloudy",
    "sources": ["INCA", "AROME"],
    "grid_latitude": 48.219,
    "grid_longitude": 16.362,
}

SAMPLE_ASSEMBLED = {
    "reference_time": datetime(2026, 7, 15, 10, 0, tzinfo=UTC),
    "grid_latitude": 48.219,
    "grid_longitude": 16.362,
    "sources": ["AROME", "C-LAEF ensemble"],
    "hourly": [
        {
            "time": datetime(2026, 7, 15, 12, 0, tzinfo=UTC),
            "condition": "rainy",
            "temperature_c": 21.3,
            "wind_speed_ms": 3.0,
            "precipitation_mm": 1.2,
            "precipitation_probability_pct": 70,
        }
    ],
}

SAMPLE_OPENMETEO_CURRENT = {
    "timezone": "Europe/Lisbon",
    "timezone_abbreviation": "WEST",
    "utc_offset_seconds": 3600,
    "current": {
        "time": "2026-07-15T15:00",
        "temperature_2m": 24.6,
        "apparent_temperature": 25.0,
        "relative_humidity_2m": 60,
        "weather_code": 2,
        "cloud_cover": 35,
        "pressure_msl": 1015.0,
        "wind_speed_10m": 3.2,
        "wind_direction_10m": 300,
        "wind_gusts_10m": 7.0,
        "is_day": 1,
    },
    "daily": {
        "time": ["2026-07-15"],
        "sunrise": ["2026-07-15T06:20"],
        "sunset": ["2026-07-15T21:05"],
    },
}

SAMPLE_OPENMETEO_HOURLY = {
    "timezone": "Europe/Lisbon",
    "timezone_abbreviation": "WEST",
    "utc_offset_seconds": 3600,
    "hourly": {
        "time": ["2026-07-15T15:00", "2026-07-15T16:00"],
        "temperature_2m": [21.0, 22.0],
        "weather_code": [2, 0],
        "precipitation": [0.0, 0.0],
        "precipitation_probability": [0, 0],
        "wind_speed_10m": [2.0, 1.0],
    },
}

SAMPLE_OPENMETEO_DAILY = {
    "timezone": "Europe/Vienna",
    "timezone_abbreviation": "CEST",
    "daily": {
        "time": ["2026-07-25", "2026-07-26"],
        "weather_code": [2, 61],
        "temperature_2m_min": [18.0, 16.0],
        "temperature_2m_max": [27.0, 22.0],
        "precipitation_sum": [2.1, 0.0],
        "precipitation_probability_max": [40, 10],
        "wind_speed_10m_max": [6.0, 5.0],
        "wind_gusts_10m_max": [8.0, 12.0],
    },
}


# --- get_current_weather ---


async def test_current_geosphere_happy_path() -> None:
    fetch = AsyncMock(return_value=SAMPLE_CURRENT)
    with patch.object(weather, "async_fetch_current_conditions", fetch):
        out = await get_current_weather(LAT, LON)
    assert "# Current Weather at 48.2208, 16.3738" in out
    assert "🌡️ Temperature: 21.3°C" in out
    assert "📡 Source: GeoSphere (INCA + AROME)" in out


async def test_current_out_of_domain_falls_back_to_openmeteo() -> None:
    fetch = AsyncMock(side_effect=GeoSphereOutOfDomainError("oob"))
    om = AsyncMock(return_value=SAMPLE_OPENMETEO_CURRENT)
    with (
        patch.object(weather, "async_fetch_current_conditions", fetch),
        patch.object(openmeteo_api, "async_get_current", om),
    ):
        out = await get_current_weather(38.7, -9.1)
    assert "📡 Source: Open-Meteo" in out
    assert "🌡️ Temperature: 24.6°C" in out
    om.assert_awaited_once()


async def test_current_timeout_returns_warning() -> None:
    fetch = AsyncMock(side_effect=TimeoutError())
    with patch.object(weather, "async_fetch_current_conditions", fetch):
        out = await get_current_weather(LAT, LON)
    assert out == "⚠️ Timeout fetching weather data"


async def test_current_unexpected_error_returns_warning() -> None:
    fetch = AsyncMock(side_effect=ValueError("boom"))
    with patch.object(weather, "async_fetch_current_conditions", fetch):
        out = await get_current_weather(LAT, LON)
    assert out == "⚠️ No weather data available"


# --- rate limiting ---


async def test_rate_limit_large_retry_after_no_retry() -> None:
    fetch = AsyncMock(side_effect=GeoSphereRateLimitError("429", retry_after=30))
    with patch.object(weather, "async_fetch_current_conditions", fetch):
        out = await get_current_weather(LAT, LON)
    assert "⚠️ GeoSphere rate limit exceeded (retry in 30s)" in out
    assert "get_daily_forecast still works" in out
    fetch.assert_awaited_once()  # not retried


async def test_rate_limit_short_retry_then_success() -> None:
    fetch = AsyncMock(
        side_effect=[
            GeoSphereRateLimitError("429", retry_after=1),
            SAMPLE_CURRENT,
        ]
    )
    sleep = AsyncMock()
    with (
        patch.object(weather, "async_fetch_current_conditions", fetch),
        patch("geosphere_mcp_server.server.asyncio.sleep", sleep),
    ):
        out = await get_current_weather(LAT, LON)
    assert "🌡️ Temperature: 21.3°C" in out
    assert fetch.await_count == 2
    sleep.assert_awaited_once()


async def test_rate_limit_short_retry_then_fails() -> None:
    fetch = AsyncMock(
        side_effect=[
            GeoSphereRateLimitError("429", retry_after=2),
            GeoSphereRateLimitError("429", retry_after=2),
        ]
    )
    with (
        patch.object(weather, "async_fetch_current_conditions", fetch),
        patch("geosphere_mcp_server.server.asyncio.sleep", AsyncMock()),
    ):
        out = await get_current_weather(LAT, LON)
    assert "⚠️ GeoSphere rate limit exceeded (retry in 2s)" in out
    assert fetch.await_count == 2


# --- get_hourly_forecast ---


async def test_hourly_geosphere_happy_path() -> None:
    fetch = AsyncMock(return_value=SAMPLE_ASSEMBLED)
    with patch.object(weather, "async_fetch_hourly_forecast", fetch):
        out = await get_hourly_forecast(LAT, LON, hours=24)
    assert "# 24-Hour Forecast for 48.2208, 16.3738" in out
    assert "14:00: 21.3°C — rainy, 1.2 mm (70% chance), wind 3 m/s" in out


async def test_hourly_clamps_hours_to_60_on_geosphere() -> None:
    fetch = AsyncMock(return_value=SAMPLE_ASSEMBLED)
    with patch.object(weather, "async_fetch_hourly_forecast", fetch):
        await get_hourly_forecast(LAT, LON, hours=200)
    assert fetch.await_args.kwargs["hours"] == 60


async def test_hourly_clamps_hours_to_48_on_fallback() -> None:
    fetch = AsyncMock(side_effect=GeoSphereOutOfDomainError("oob"))
    om = AsyncMock(return_value=SAMPLE_OPENMETEO_HOURLY)
    with (
        patch.object(weather, "async_fetch_hourly_forecast", fetch),
        patch.object(openmeteo_api, "async_get_hourly", om),
    ):
        out = await get_hourly_forecast(38.7, -9.1, hours=200)
    assert om.await_args.kwargs["hours"] == 48
    assert "Source: Open-Meteo" in out


async def test_hourly_invalid_start_returns_error_line() -> None:
    fetch = AsyncMock(return_value=SAMPLE_ASSEMBLED)
    with patch.object(weather, "async_fetch_hourly_forecast", fetch):
        out = await get_hourly_forecast(LAT, LON, start="not-a-date")
    assert out.startswith("⚠️ Invalid start time 'not-a-date'")
    fetch.assert_not_awaited()


async def test_hourly_valid_start_is_parsed_and_passed() -> None:
    fetch = AsyncMock(return_value=SAMPLE_ASSEMBLED)
    with patch.object(weather, "async_fetch_hourly_forecast", fetch):
        await get_hourly_forecast(LAT, LON, start="2026-07-15T14:00")
    passed = fetch.await_args.kwargs["start"]
    assert passed == datetime(2026, 7, 15, 14, 0, tzinfo=UTC)


# --- get_daily_forecast ---


async def test_daily_happy_path() -> None:
    om = AsyncMock(return_value=SAMPLE_OPENMETEO_DAILY)
    with patch.object(openmeteo_api, "async_get_daily", om):
        out = await get_daily_forecast(LAT, LON, days=7)
    assert "# 7-Day Forecast for 48.2208, 16.3738" in out
    assert "Sat 2026-07-25: 18–27°C — partlycloudy, 2.1 mm (40% chance)" in out


async def test_daily_clamps_days_to_16() -> None:
    om = AsyncMock(return_value=SAMPLE_OPENMETEO_DAILY)
    with patch.object(openmeteo_api, "async_get_daily", om):
        await get_daily_forecast(LAT, LON, days=99)
    assert om.await_args.kwargs["days"] == 16


async def test_daily_timeout_returns_warning() -> None:
    om = AsyncMock(side_effect=TimeoutError())
    with patch.object(openmeteo_api, "async_get_daily", om):
        out = await get_daily_forecast(LAT, LON)
    assert out == "⚠️ Timeout fetching weather data"


@pytest.mark.parametrize("days", [1, 7, 16])
async def test_daily_title_uses_clamped_days(days: int) -> None:
    om = AsyncMock(return_value=SAMPLE_OPENMETEO_DAILY)
    with patch.object(openmeteo_api, "async_get_daily", om):
        out = await get_daily_forecast(LAT, LON, days=days)
    assert f"# {days}-Day Forecast" in out
