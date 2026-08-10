"""Tests for the MCP tool functions in server.py.

The GeoSphere fetch helpers (``weather.async_fetch_*``) and the Open-Meteo
clients (``openmeteo_api.async_get_*``) are patched; the tool functions are
called directly, which works because ``@mcp.tool()`` registers the function
and returns it undecorated rather than wrapping it.
"""

from __future__ import annotations

from datetime import UTC, datetime, timedelta
from unittest.mock import AsyncMock, patch

import pytest

from geosphere_mcp_server import air_quality, openmeteo_api, weather
from geosphere_mcp_server.geosphere_api import (
    GeoSphereOutOfDomainError,
    GeoSphereRateLimitError,
    GeoSphereTimeoutError,
)
from geosphere_mcp_server.openmeteo_api import OpenMeteoTimeoutError
from geosphere_mcp_server.server import (
    get_air_quality,
    get_current_weather,
    get_daily_forecast,
    get_hourly_forecast,
    get_storm_outlook,
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


async def test_current_client_timeout_returns_warning() -> None:
    """The client's wrapped timeout must reach the timeout line, not the

    generic "no data" line -- the API layer never raises a bare TimeoutError.
    """
    fetch = AsyncMock(side_effect=GeoSphereTimeoutError("timed out"))
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


async def test_daily_client_timeout_returns_warning() -> None:
    """As above for the Open-Meteo client's wrapped timeout."""
    om = AsyncMock(side_effect=OpenMeteoTimeoutError("timed out"))
    with patch.object(openmeteo_api, "async_get_daily", om):
        out = await get_daily_forecast(LAT, LON)
    assert out == "⚠️ Timeout fetching weather data"


@pytest.mark.parametrize("days", [1, 7, 16])
async def test_daily_title_uses_clamped_days(days: int) -> None:
    om = AsyncMock(return_value=SAMPLE_OPENMETEO_DAILY)
    with patch.object(openmeteo_api, "async_get_daily", om):
        out = await get_daily_forecast(LAT, LON, days=days)
    assert f"# {days}-Day Forecast" in out


async def test_daily_date_range_happy_path() -> None:
    om = AsyncMock(return_value=SAMPLE_OPENMETEO_DAILY)
    with patch.object(openmeteo_api, "async_get_daily", om):
        out = await get_daily_forecast(
            LAT, LON, start_date="2026-07-25", end_date="2026-07-26"
        )
    assert om.await_args.kwargs["start_date"] == "2026-07-25"
    assert om.await_args.kwargs["end_date"] == "2026-07-26"
    assert "days" not in om.await_args.kwargs
    # The two-day span drives the title and both days render.
    assert "# 2-Day Forecast for 48.2208, 16.3738" in out
    assert "Sat 2026-07-25:" in out
    assert "Sun 2026-07-26:" in out


async def test_daily_single_bound_defaults_to_one_day() -> None:
    om = AsyncMock(return_value=SAMPLE_OPENMETEO_DAILY)
    with patch.object(openmeteo_api, "async_get_daily", om):
        out = await get_daily_forecast(LAT, LON, start_date="2026-07-25")
    assert om.await_args.kwargs["start_date"] == "2026-07-25"
    assert om.await_args.kwargs["end_date"] == "2026-07-25"
    assert "# 1-Day Forecast" in out


async def test_daily_range_clamped_to_16_days() -> None:
    om = AsyncMock(return_value=SAMPLE_OPENMETEO_DAILY)
    with patch.object(openmeteo_api, "async_get_daily", om):
        await get_daily_forecast(
            LAT, LON, start_date="2026-07-01", end_date="2026-08-01"
        )
    # Inclusive 16-day cap: 2026-07-01 + 15 days.
    assert om.await_args.kwargs["end_date"] == "2026-07-16"


async def test_daily_invalid_start_date_returns_error() -> None:
    om = AsyncMock(return_value=SAMPLE_OPENMETEO_DAILY)
    with patch.object(openmeteo_api, "async_get_daily", om):
        out = await get_daily_forecast(LAT, LON, start_date="not-a-date")
    assert out.startswith("⚠️ Invalid start_date")
    om.assert_not_awaited()


async def test_daily_end_before_start_returns_error() -> None:
    om = AsyncMock(return_value=SAMPLE_OPENMETEO_DAILY)
    with patch.object(openmeteo_api, "async_get_daily", om):
        out = await get_daily_forecast(
            LAT, LON, start_date="2026-07-26", end_date="2026-07-25"
        )
    assert out.startswith("⚠️ end_date")
    om.assert_not_awaited()


# --- get_storm_outlook ---


# The tools call the outlook with the real clock, so the fixtures are anchored
# to it. Two identical consecutive hours are used rather than one, so an hour
# rolling over mid-test still leaves a matching hour inside the window.
def _now_hours(count: int = 2) -> list[datetime]:
    """The current top of the hour and the ``count - 1`` hours after it, UTC."""
    top = datetime.now(UTC).replace(minute=0, second=0, microsecond=0)
    return [top + timedelta(hours=offset) for offset in range(count)]


SAMPLE_OUTLOOK_ASSEMBLED = {
    "reference_time": datetime(2026, 7, 15, 10, 0, tzinfo=UTC),
    "sources": ["AROME"],
    "hourly": [
        {
            "time": when,
            "condition": "lightning-rainy",
            "wind_gust_ms": 22.0,
            "cape_jkg": 1700.0,
            "cin_jkg": -5.0,
            "precipitation_mm": 3.0,
        }
        for when in _now_hours()
    ],
}

SAMPLE_OPENMETEO_OUTLOOK_HOURLY = {
    "timezone": "UTC",
    "timezone_abbreviation": "UTC",
    "utc_offset_seconds": 0,
    "hourly": {
        # utc_offset_seconds is 0, so these naive local stamps are UTC.
        "time": [when.isoformat()[:16] for when in _now_hours()],
        "weather_code": [95, 95],
        "wind_gusts_10m": [19.0, 19.0],
        "cape": [2100.0, 2100.0],
        "convective_inhibition": [5.0, 5.0],
        "precipitation": [4.0, 4.0],
    },
}


async def test_storm_outlook_geosphere_happy_path() -> None:
    fetch = AsyncMock(return_value=SAMPLE_OUTLOOK_ASSEMBLED)
    with patch.object(weather, "async_fetch_hourly_forecast", fetch):
        out = await get_storm_outlook(LAT, LON)
    assert out.splitlines()[0] == "# Storm Outlook for 48.2208, 16.3738"
    assert "💨 Max gust next 1 h: 22 m/s" in out
    assert "⛈️ Thunderstorm expected next 1 h: yes" in out
    assert "Source: GeoSphere (AROME)" in out


async def test_storm_outlook_skips_the_ensemble_request() -> None:
    """The outlook reports no probability, so the second dataset is not fetched."""
    fetch = AsyncMock(return_value=SAMPLE_OUTLOOK_ASSEMBLED)
    with patch.object(weather, "async_fetch_hourly_forecast", fetch):
        await get_storm_outlook(LAT, LON)
    assert fetch.await_args.kwargs["include_ensemble"] is False


async def test_storm_outlook_out_of_domain_falls_back_to_openmeteo() -> None:
    fetch = AsyncMock(side_effect=GeoSphereOutOfDomainError("oob"))
    om = AsyncMock(return_value=SAMPLE_OPENMETEO_OUTLOOK_HOURLY)
    with (
        patch.object(weather, "async_fetch_hourly_forecast", fetch),
        patch.object(openmeteo_api, "async_get_hourly", om),
    ):
        out = await get_storm_outlook(38.7, -9.1)
    assert "Source: Open-Meteo" in out
    assert "💨 Max gust next 1 h: 19 m/s" in out
    assert "⛈️ Thunderstorm expected next 1 h: yes" in out
    om.assert_awaited_once()


async def test_storm_outlook_timeout_returns_warning() -> None:
    fetch = AsyncMock(side_effect=GeoSphereTimeoutError("timed out"))
    with patch.object(weather, "async_fetch_hourly_forecast", fetch):
        out = await get_storm_outlook(LAT, LON)
    assert out == "⚠️ Timeout fetching weather data"


async def test_storm_outlook_rate_limit_returns_warning() -> None:
    fetch = AsyncMock(side_effect=GeoSphereRateLimitError("429", retry_after=30))
    with patch.object(weather, "async_fetch_hourly_forecast", fetch):
        out = await get_storm_outlook(LAT, LON)
    assert "⚠️ GeoSphere rate limit exceeded (retry in 30s)" in out


# --- get_air_quality ---

SAMPLE_AIR_QUALITY = {
    "observed_at": datetime(2026, 7, 15, 14, 0, tzinfo=UTC),
    "pollutants": {
        "nitrogen_dioxide": 18.0,
        "ozone": 92.0,
        "pm10": 21.0,
        "pm2_5": 12.0,
    },
    "aqi_band_today": 2,
    "aqi_band_tomorrow": 3,
    "aqi_band_in_2_days": 2,
    "aqi_value_today": None,
    "aqi_value_tomorrow": None,
    "aqi_value_in_2_days": None,
    "sources": ["WRF-Chem", "daily AQI"],
}

SAMPLE_OPENMETEO_AIR_QUALITY = {
    "timezone": "UTC",
    "timezone_abbreviation": "UTC",
    "utc_offset_seconds": 0,
    "hourly": {
        # Anchored to the real clock so "today" resolves to the current day.
        "time": [when.isoformat()[:16] for when in _now_hours(1)],
        "european_aqi": [44],
        "nitrogen_dioxide": [14.0],
        "ozone": [70.0],
        "pm10": [21.0],
        "pm2_5": [11.0],
    },
}


async def test_air_quality_geosphere_happy_path() -> None:
    fetch = AsyncMock(return_value=SAMPLE_AIR_QUALITY)
    with patch.object(air_quality, "async_fetch_air_quality", fetch):
        out = await get_air_quality(LAT, LON)
    assert "# Air Quality at 48.2208, 16.3738" in out
    assert "2 (fair) today · 3 (moderate) tomorrow · 2 (fair) in 2 days" in out
    assert "NO₂ 18 µg/m³" in out
    assert "📡 Source: GeoSphere (WRF-Chem + daily AQI, 3 km)" in out


async def test_air_quality_out_of_domain_falls_back_to_openmeteo() -> None:
    fetch = AsyncMock(side_effect=GeoSphereOutOfDomainError("oob"))
    om = AsyncMock(return_value=SAMPLE_OPENMETEO_AIR_QUALITY)
    with (
        patch.object(air_quality, "async_fetch_air_quality", fetch),
        patch.object(openmeteo_api, "async_get_air_quality", om),
    ):
        out = await get_air_quality(38.7, -9.1)
    assert "📡 Source: Open-Meteo (CAMS)" in out
    assert "3 (moderate, index 44) today" in out
    om.assert_awaited_once()


async def test_air_quality_timeout_returns_warning() -> None:
    fetch = AsyncMock(side_effect=GeoSphereTimeoutError("timed out"))
    with patch.object(air_quality, "async_fetch_air_quality", fetch):
        out = await get_air_quality(LAT, LON)
    assert out == "⚠️ Timeout fetching weather data"


async def test_air_quality_unexpected_error_returns_warning() -> None:
    fetch = AsyncMock(side_effect=ValueError("boom"))
    with patch.object(air_quality, "async_fetch_air_quality", fetch):
        out = await get_air_quality(LAT, LON)
    assert out == "⚠️ No weather data available"
