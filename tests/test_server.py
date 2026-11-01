"""Tests for the MCP tool functions in server.py.

The GeoSphere fetch helpers (``weather.async_fetch_*``) are patched; the tool
functions are called directly, which works because ``@mcp.tool()`` registers
the function and returns it undecorated rather than wrapping it.
"""

from __future__ import annotations

from datetime import UTC, datetime, timedelta
from unittest.mock import AsyncMock, patch

from geosphere_mcp_server import air_quality, weather
from geosphere_mcp_server.const import (
    AROME_MAX_HOURS,
    ENSEMBLE_MAX_AGE,
    PRECIP_MIN_MM,
    THUNDER_CAPE_JKG,
)
from geosphere_mcp_server.geosphere_api import (
    GeoSphereOutOfDomainError,
    GeoSphereRateLimitError,
    GeoSphereTimeoutError,
)
from geosphere_mcp_server.server import (
    OUT_OF_DOMAIN_MESSAGE,
    get_air_quality,
    get_current_weather,
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

# --- get_current_weather ---


async def test_current_geosphere_happy_path() -> None:
    fetch = AsyncMock(return_value=SAMPLE_CURRENT)
    with patch.object(weather, "async_fetch_current_conditions", fetch):
        out = await get_current_weather(LAT, LON)
    assert "# Current Weather at 48.2208, 16.3738" in out
    assert "🌡️ Temperature: 21.3°C" in out
    assert "📡 Source: GeoSphere (INCA + AROME)" in out


async def test_current_out_of_domain_reports_no_coverage() -> None:
    """A point outside the AROME grid gets the coverage line, not a failure.

    With no worldwide source behind it, "outside coverage" is a permanent
    property of the location — the caller must be able to tell it apart from
    the transient failures below, which are worth retrying.
    """
    fetch = AsyncMock(side_effect=GeoSphereOutOfDomainError("oob"))
    with patch.object(weather, "async_fetch_current_conditions", fetch):
        out = await get_current_weather(38.7, -9.1)
    assert out == OUT_OF_DOMAIN_MESSAGE
    assert "Outside coverage" in out


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
    assert out == "⚠️ GeoSphere rate limit exceeded (retry in 30s)"
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


async def test_hourly_clamps_hours_to_60() -> None:
    fetch = AsyncMock(return_value=SAMPLE_ASSEMBLED)
    with patch.object(weather, "async_fetch_hourly_forecast", fetch):
        await get_hourly_forecast(LAT, LON, hours=200)
    assert fetch.await_args.kwargs["hours"] == 60


async def test_hourly_out_of_domain_reports_no_coverage() -> None:
    fetch = AsyncMock(side_effect=GeoSphereOutOfDomainError("oob"))
    with patch.object(weather, "async_fetch_hourly_forecast", fetch):
        out = await get_hourly_forecast(38.7, -9.1, hours=24)
    assert out == OUT_OF_DOMAIN_MESSAGE


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
    "ensemble_reference_time": None,
    "sources": ["AROME"],
    "hourly": [
        {
            "time": when,
            "weather_symbol": 26,
            "wind_gust_ms": 22.0,
            "cape_jkg": 1700.0,
        }
        for when in _now_hours()
    ],
}


async def test_storm_outlook_geosphere_happy_path() -> None:
    fetch = AsyncMock(return_value=SAMPLE_OUTLOOK_ASSEMBLED)
    with patch.object(weather, "async_fetch_hourly_forecast", fetch):
        out = await get_storm_outlook(LAT, LON)
    assert out.splitlines()[0] == "# Storm Outlook for 48.2208, 16.3738"
    assert "💨 Max gust next 1 h: 22 m/s" in out
    assert "⛈️ Thunderstorm expected next 1 h: yes (signal: symbol)" in out
    assert "Source: GeoSphere (AROME)" in out


async def test_storm_outlook_counts_a_current_ensemble() -> None:
    """The ensemble's storm signal reaches the tool, aged by the real clock."""
    hourly = [
        {
            "time": when,
            "weather_symbol": 5,
            "ensemble_cape_p90_jkg": 1500.0,
            "ensemble_precipitation_p90_mm": 1.0,
        }
        for when in _now_hours()
    ]
    assembled = {
        **SAMPLE_OUTLOOK_ASSEMBLED,
        "ensemble_reference_time": datetime.now(UTC) - timedelta(hours=6),
        "sources": ["AROME", "C-LAEF ensemble"],
        "hourly": hourly,
    }
    fetch = AsyncMock(return_value=assembled)
    with patch.object(weather, "async_fetch_hourly_forecast", fetch):
        out = await get_storm_outlook(LAT, LON)
    assert "⛈️ Thunderstorm expected next 1 h: yes (signal: ensemble)" in out
    assert fetch.await_args.kwargs["hours"] == AROME_MAX_HOURS


def test_storm_outlook_description_states_the_thresholds() -> None:
    """The tool description an agent reads must match what the code applies."""
    doc = get_storm_outlook.__doc__ or ""
    assert f"{THUNDER_CAPE_JKG:.0f} J/kg CAPE" in doc
    assert f"{PRECIP_MIN_MM} mm precipitation" in doc
    assert f"{ENSEMBLE_MAX_AGE.total_seconds() / 3600:.0f} h old" in doc


async def test_storm_outlook_out_of_domain_reports_no_coverage() -> None:
    fetch = AsyncMock(side_effect=GeoSphereOutOfDomainError("oob"))
    with patch.object(weather, "async_fetch_hourly_forecast", fetch):
        out = await get_storm_outlook(38.7, -9.1)
    assert out == OUT_OF_DOMAIN_MESSAGE


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
    "sources": ["WRF-Chem", "daily AQI"],
}


async def test_air_quality_geosphere_happy_path() -> None:
    fetch = AsyncMock(return_value=SAMPLE_AIR_QUALITY)
    with patch.object(air_quality, "async_fetch_air_quality", fetch):
        out = await get_air_quality(LAT, LON)
    assert "# Air Quality at 48.2208, 16.3738" in out
    assert "2 (fair) today · 3 (moderate) tomorrow · 2 (fair) in 2 days" in out
    assert "NO₂ 18 µg/m³" in out
    assert "📡 Source: GeoSphere (WRF-Chem + daily AQI, 3 km)" in out


async def test_air_quality_out_of_domain_reports_no_coverage() -> None:
    fetch = AsyncMock(side_effect=GeoSphereOutOfDomainError("oob"))
    with patch.object(air_quality, "async_fetch_air_quality", fetch):
        out = await get_air_quality(38.7, -9.1)
    assert out == OUT_OF_DOMAIN_MESSAGE


async def test_air_quality_rate_limit_returns_warning() -> None:
    fetch = AsyncMock(side_effect=GeoSphereRateLimitError("429", retry_after=30))
    with patch.object(air_quality, "async_fetch_air_quality", fetch):
        out = await get_air_quality(LAT, LON)
    assert out == "⚠️ GeoSphere rate limit exceeded (retry in 30s)"


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


async def test_air_quality_empty_in_domain_response_is_not_out_of_coverage() -> None:
    """A point inside the 3 km grid whose WRF-Chem run happens to have no data.

    The GeoSphere API answers 200 with an empty series rather than an error, so
    "in coverage" and "has data" are separate questions. The caller must be
    able to tell them apart: an empty run is worth retrying later, an
    uncovered location never is.
    """
    empty = AsyncMock(
        return_value={
            "observed_at": None,
            "pollutants": dict.fromkeys(("nitrogen_dioxide", "ozone", "pm10", "pm2_5")),
            "sources": ["WRF-Chem"],
        }
    )
    with patch.object(air_quality, "async_fetch_air_quality", empty):
        out = await get_air_quality(LAT, LON)
    assert "No air-quality data available" in out
    assert "📡 Source: GeoSphere (WRF-Chem, 3 km)" in out
    assert out != OUT_OF_DOMAIN_MESSAGE
