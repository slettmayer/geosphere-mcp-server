"""Tests for the Open-Meteo API client."""

from __future__ import annotations

from unittest.mock import AsyncMock, MagicMock

import aiohttp
import pytest

from geosphere_mcp_server.openmeteo_api import (
    OpenMeteoApiError,
    OpenMeteoConnectionError,
    async_get_current,
    async_get_daily,
    async_get_hourly,
)


def _make_session(
    *,
    status: int = 200,
    json_data: dict | None = None,
    raise_error: Exception | None = None,
) -> MagicMock:
    """Create a mock aiohttp session whose GET returns one JSON response."""
    session = MagicMock(spec=aiohttp.ClientSession)
    if raise_error is not None:
        session.get = AsyncMock(side_effect=raise_error)
        return session
    response = MagicMock()
    response.status = status
    response.json = AsyncMock(return_value=json_data or {})
    session.get = AsyncMock(return_value=response)
    return session


# --- Sample payloads ---

SAMPLE_CURRENT = {
    "latitude": 38.72,
    "longitude": -9.14,
    "timezone": "Europe/Lisbon",
    "current_units": {"temperature_2m": "°C"},
    "current": {
        "time": "2026-07-22T14:00",
        "temperature_2m": 24.3,
        "apparent_temperature": 25.1,
        "relative_humidity_2m": 55,
        "dew_point_2m": 14.2,
        "precipitation": 0.0,
        "weather_code": 2,
        "cloud_cover": 40,
        "pressure_msl": 1015.0,
        "wind_speed_10m": 3.1,
        "wind_direction_10m": 310,
        "wind_gusts_10m": 7.0,
        "is_day": 1,
    },
    "daily": {
        "time": ["2026-07-22"],
        "sunrise": ["2026-07-22T06:20"],
        "sunset": ["2026-07-22T20:55"],
    },
}

SAMPLE_HOURLY = {
    "latitude": 38.72,
    "longitude": -9.14,
    "timezone": "Europe/Lisbon",
    "hourly_units": {"temperature_2m": "°C"},
    "hourly": {
        "time": ["2026-07-22T14:00", "2026-07-22T15:00"],
        "temperature_2m": [24.3, 24.8],
        "precipitation_probability": [10, 20],
        "weather_code": [2, 3],
        "wind_speed_10m": [3.1, 3.4],
    },
}

SAMPLE_DAILY = {
    "latitude": 38.72,
    "longitude": -9.14,
    "timezone": "Europe/Lisbon",
    "daily_units": {"temperature_2m_max": "°C"},
    "daily": {
        "time": ["2026-07-22", "2026-07-23"],
        "weather_code": [2, 61],
        "temperature_2m_max": [27.0, 25.0],
        "temperature_2m_min": [18.0, 17.0],
    },
}

SAMPLE_ERROR = {"error": True, "reason": "Latitude must be in range of -90 to 90°."}


# --- async_get_current ---


@pytest.mark.asyncio
async def test_async_get_current_success() -> None:
    """A successful current fetch returns the raw body."""
    session = _make_session(json_data=SAMPLE_CURRENT)

    result = await async_get_current(session, 38.72, -9.14)

    assert result["current"]["temperature_2m"] == 24.3
    assert result["daily"]["sunrise"] == ["2026-07-22T06:20"]


@pytest.mark.asyncio
async def test_async_get_current_builds_params() -> None:
    """Current request asks for m/s wind, timezone=auto, and 1 forecast day."""
    session = _make_session(json_data=SAMPLE_CURRENT)

    await async_get_current(session, 38.72, -9.14)

    params = session.get.call_args.kwargs["params"]
    assert params["latitude"] == "38.72"
    assert params["longitude"] == "-9.14"
    assert params["wind_speed_unit"] == "ms"
    assert params["timezone"] == "auto"
    assert params["forecast_days"] == "1"
    assert "temperature_2m" in params["current"]
    assert "is_day" in params["current"]
    assert params["daily"] == "sunrise,sunset"


# --- async_get_hourly ---


@pytest.mark.asyncio
async def test_async_get_hourly_success() -> None:
    """A successful hourly fetch returns the raw body."""
    session = _make_session(json_data=SAMPLE_HOURLY)

    result = await async_get_hourly(session, 38.72, -9.14, hours=24)

    assert result["hourly"]["temperature_2m"] == [24.3, 24.8]


@pytest.mark.asyncio
async def test_async_get_hourly_forecast_days_from_hours() -> None:
    """forecast_days is the ceil of hours/24 (>=1)."""
    session = _make_session(json_data=SAMPLE_HOURLY)

    await async_get_hourly(session, 38.72, -9.14, hours=30)
    assert session.get.call_args.kwargs["params"]["forecast_days"] == "2"

    await async_get_hourly(session, 38.72, -9.14, hours=24)
    assert session.get.call_args.kwargs["params"]["forecast_days"] == "1"

    await async_get_hourly(session, 38.72, -9.14, hours=1)
    assert session.get.call_args.kwargs["params"]["forecast_days"] == "1"


@pytest.mark.asyncio
async def test_async_get_hourly_builds_params() -> None:
    """Hourly request includes the probability variable and m/s wind."""
    session = _make_session(json_data=SAMPLE_HOURLY)

    await async_get_hourly(session, 38.72, -9.14)

    params = session.get.call_args.kwargs["params"]
    assert "precipitation_probability" in params["hourly"]
    assert params["wind_speed_unit"] == "ms"
    assert params["timezone"] == "auto"


# --- async_get_daily ---


@pytest.mark.asyncio
async def test_async_get_daily_success() -> None:
    """A successful daily fetch returns the raw body."""
    session = _make_session(json_data=SAMPLE_DAILY)

    result = await async_get_daily(session, 38.72, -9.14, days=2)

    assert result["daily"]["weather_code"] == [2, 61]


@pytest.mark.asyncio
async def test_async_get_daily_builds_params() -> None:
    """Daily request maps days to forecast_days and includes uv_index_max."""
    session = _make_session(json_data=SAMPLE_DAILY)

    await async_get_daily(session, 38.72, -9.14, days=7)

    params = session.get.call_args.kwargs["params"]
    assert params["forecast_days"] == "7"
    assert "uv_index_max" in params["daily"]
    assert "precipitation_probability_max" in params["daily"]


@pytest.mark.asyncio
async def test_async_get_daily_date_range_replaces_forecast_days() -> None:
    """A start_date/end_date range sends those params instead of forecast_days."""
    session = _make_session(json_data=SAMPLE_DAILY)

    await async_get_daily(
        session, 38.72, -9.14, start_date="2026-07-25", end_date="2026-07-26"
    )

    params = session.get.call_args.kwargs["params"]
    assert params["start_date"] == "2026-07-25"
    assert params["end_date"] == "2026-07-26"
    assert "forecast_days" not in params


# --- Error handling ---


@pytest.mark.asyncio
async def test_async_get_current_api_error_body() -> None:
    """An {'error': true} body raises OpenMeteoApiError with the reason."""
    session = _make_session(status=400, json_data=SAMPLE_ERROR)

    with pytest.raises(OpenMeteoApiError) as err:
        await async_get_current(session, 999.0, -9.14)

    assert "Latitude" in str(err.value)


@pytest.mark.asyncio
async def test_async_get_current_http_error() -> None:
    """A >=400 status raises OpenMeteoApiError."""
    session = _make_session(status=500, json_data={})

    with pytest.raises(OpenMeteoApiError):
        await async_get_current(session, 38.72, -9.14)


@pytest.mark.asyncio
async def test_async_get_hourly_timeout() -> None:
    """A timeout surfaces as OpenMeteoConnectionError."""
    session = _make_session(raise_error=TimeoutError())

    with pytest.raises(OpenMeteoConnectionError):
        await async_get_hourly(session, 38.72, -9.14)


@pytest.mark.asyncio
async def test_async_get_daily_connection_error() -> None:
    """An aiohttp ClientError surfaces as OpenMeteoConnectionError."""
    session = _make_session(raise_error=aiohttp.ClientError("boom"))

    with pytest.raises(OpenMeteoConnectionError):
        await async_get_daily(session, 38.72, -9.14)
