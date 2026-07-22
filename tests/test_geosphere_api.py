"""Tests for the GeoSphere Dataset API client."""

from __future__ import annotations

from datetime import UTC, datetime
from unittest.mock import AsyncMock, MagicMock

import aiohttp
import pytest

from geosphere_mcp_server.geosphere_api import (
    GeoSphereApiError,
    GeoSphereConnectionError,
    GeoSphereOutOfDomainError,
    GeoSphereRateLimitError,
    _parse_geojson,
    async_get_timeseries,
)


def _make_session(
    *,
    status: int = 200,
    json_data: dict | None = None,
    headers: dict[str, str] | None = None,
    raise_error: Exception | None = None,
) -> MagicMock:
    """Create a mock aiohttp session whose GET returns one response.

    GeoSphere uses ``resp = await session.get(...)`` (not an async context
    manager), so ``session.get`` is an AsyncMock returning the response.
    """
    session = MagicMock(spec=aiohttp.ClientSession)
    if raise_error is not None:
        session.get = AsyncMock(side_effect=raise_error)
        return session
    response = MagicMock()
    response.status = status
    response.headers = headers or {}
    response.json = AsyncMock(return_value=json_data or {})
    session.get = AsyncMock(return_value=response)
    return session


# --- Sample GeoJSON payloads ---

SAMPLE_AROME_GEOJSON = {
    "reference_time": "2026-07-15T12:00+00:00",
    "timestamps": [
        "2026-07-15T15:00+00:00",
        "2026-07-15T16:00+00:00",
        "2026-07-15T17:00+00:00",
    ],
    "features": [
        {
            "type": "Feature",
            "geometry": {"type": "Point", "coordinates": [16.362, 48.219]},
            "properties": {
                "parameters": {
                    "t2m": {
                        "name": "2m temperature",
                        "unit": "degree Celsius",
                        "data": [31.1, 28.6, 28.1],
                    },
                    "tcc": {
                        "name": "total cloud cover",
                        "unit": "1",
                        "data": [0.2, 0.5, None],
                    },
                }
            },
        }
    ],
}

SAMPLE_OUT_OF_DOMAIN = {
    "detail": "Requested point 52.52,13.405 is outside of dataset bounds!"
}

SAMPLE_BAD_REQUEST = {"detail": "unknown parameter 'foo'"}


# --- URL / parameter building ---


@pytest.mark.asyncio
async def test_async_get_timeseries_builds_url_and_params() -> None:
    """The URL, comma-joined parameters, and lat_lon are built correctly."""
    session = _make_session(json_data=SAMPLE_AROME_GEOJSON)

    await async_get_timeseries(
        session,
        "forecast",
        "nwp-v1-1h-2500m",
        ("t2m", "tcc"),
        48.2208,
        16.3738,
    )

    call = session.get.call_args
    url = call.args[0]
    params = call.kwargs["params"]
    assert url == (
        "https://dataset.api.hub.geosphere.at/v1/timeseries/forecast/nwp-v1-1h-2500m"
    )
    assert params["parameters"] == "t2m,tcc"
    assert params["lat_lon"] == "48.2208,16.3738"
    assert params["output_format"] == "geojson"
    assert "start" not in params
    assert "end" not in params


@pytest.mark.asyncio
async def test_async_get_timeseries_formats_start_end() -> None:
    """Optional start/end are formatted as %Y-%m-%dT%H:%M."""
    session = _make_session(json_data=SAMPLE_AROME_GEOJSON)

    await async_get_timeseries(
        session,
        "historical",
        "inca-v1-1h-1km",
        ("T2M",),
        48.0,
        16.0,
        start=datetime(2026, 7, 15, 9, 30, tzinfo=UTC),
        end=datetime(2026, 7, 15, 12, 0, tzinfo=UTC),
    )

    params = session.get.call_args.kwargs["params"]
    assert params["start"] == "2026-07-15T09:30"
    assert params["end"] == "2026-07-15T12:00"


# --- GeoJSON parsing ---


@pytest.mark.asyncio
async def test_async_get_timeseries_parses_geojson() -> None:
    """A valid GeoJSON body parses into a typed response."""
    session = _make_session(json_data=SAMPLE_AROME_GEOJSON)

    response = await async_get_timeseries(
        session, "forecast", "nwp-v1-1h-2500m", ("t2m", "tcc"), 48.22, 16.37
    )

    assert response.reference_time is not None
    assert response.reference_time.isoformat() == "2026-07-15T12:00:00+00:00"
    assert len(response.timestamps) == 3
    assert response.grid_latitude == pytest.approx(48.219)
    assert response.grid_longitude == pytest.approx(16.362)
    assert response.value_at("t2m", 0) == 31.1
    assert response.parameters["tcc"].unit == "1"
    # Missing value in a series is preserved as None.
    assert response.value_at("tcc", 2) is None
    # Unknown parameter degrades to an all-None series, not a KeyError.
    assert response.value_at("nonexistent", 0) is None
    assert response.series("nonexistent") == [None, None, None]


def test_parse_geojson_unexpected_shape_raises() -> None:
    """An empty feature list raises GeoSphereApiError."""
    with pytest.raises(GeoSphereApiError):
        _parse_geojson("nwp-v1-1h-2500m", {"features": []})


@pytest.mark.asyncio
async def test_async_get_timeseries_unexpected_shape_raises() -> None:
    """A malformed body surfaces as GeoSphereApiError."""
    session = _make_session(json_data={"features": []})
    with pytest.raises(GeoSphereApiError):
        await async_get_timeseries(
            session, "forecast", "nwp-v1-1h-2500m", ("t2m",), 48.0, 16.0
        )


# --- Error taxonomy ---


@pytest.mark.asyncio
async def test_async_get_timeseries_rate_limit_with_retry_after() -> None:
    """HTTP 429 raises GeoSphereRateLimitError carrying retry_after."""
    session = _make_session(status=429, headers={"Retry-After": "120"})

    with pytest.raises(GeoSphereRateLimitError) as err:
        await async_get_timeseries(
            session, "forecast", "nwp-v1-1h-2500m", ("t2m",), 48.0, 16.0
        )

    assert err.value.retry_after == 120.0


@pytest.mark.asyncio
async def test_async_get_timeseries_rate_limit_without_header() -> None:
    """HTTP 429 without a Retry-After header leaves retry_after None."""
    session = _make_session(status=429)

    with pytest.raises(GeoSphereRateLimitError) as err:
        await async_get_timeseries(
            session, "forecast", "nwp-v1-1h-2500m", ("t2m",), 48.0, 16.0
        )

    assert err.value.retry_after is None


@pytest.mark.asyncio
async def test_async_get_timeseries_out_of_domain() -> None:
    """HTTP 400 with the bounds detail raises GeoSphereOutOfDomainError."""
    session = _make_session(status=400, json_data=SAMPLE_OUT_OF_DOMAIN)

    with pytest.raises(GeoSphereOutOfDomainError):
        await async_get_timeseries(
            session, "forecast", "nwp-v1-1h-2500m", ("t2m",), 52.52, 13.405
        )


@pytest.mark.asyncio
async def test_async_get_timeseries_generic_400() -> None:
    """A non-bounds HTTP 400 raises the generic GeoSphereApiError."""
    session = _make_session(status=400, json_data=SAMPLE_BAD_REQUEST)

    with pytest.raises(GeoSphereApiError) as err:
        await async_get_timeseries(
            session, "forecast", "nwp-v1-1h-2500m", ("foo",), 48.0, 16.0
        )

    assert not isinstance(err.value, GeoSphereOutOfDomainError)


@pytest.mark.asyncio
async def test_async_get_timeseries_server_error() -> None:
    """A 5xx status raises GeoSphereApiError."""
    session = _make_session(status=503)

    with pytest.raises(GeoSphereApiError):
        await async_get_timeseries(
            session, "forecast", "nwp-v1-1h-2500m", ("t2m",), 48.0, 16.0
        )


@pytest.mark.asyncio
async def test_async_get_timeseries_timeout() -> None:
    """A timeout surfaces as GeoSphereConnectionError."""
    session = _make_session(raise_error=TimeoutError())

    with pytest.raises(GeoSphereConnectionError):
        await async_get_timeseries(
            session, "forecast", "nwp-v1-1h-2500m", ("t2m",), 48.0, 16.0
        )


@pytest.mark.asyncio
async def test_async_get_timeseries_connection_error() -> None:
    """An aiohttp ClientError surfaces as GeoSphereConnectionError."""
    session = _make_session(raise_error=aiohttp.ClientError("boom"))

    with pytest.raises(GeoSphereConnectionError):
        await async_get_timeseries(
            session, "forecast", "nwp-v1-1h-2500m", ("t2m",), 48.0, 16.0
        )
