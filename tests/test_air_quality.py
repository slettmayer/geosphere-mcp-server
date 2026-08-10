"""Tests for the air-quality merge and its fetch orchestration."""

from __future__ import annotations

from datetime import UTC, datetime
from unittest.mock import AsyncMock, patch

import pytest

from geosphere_mcp_server.air_quality import (
    async_fetch_air_quality,
    merge_air_quality,
)
from geosphere_mcp_server.geosphere_api import (
    GeoSphereApiError,
    GeoSphereOutOfDomainError,
    GeoSphereResponse,
    ParameterSeries,
)

NOW = datetime(2026, 7, 15, 15, 40, tzinfo=UTC)


def _response(
    resource_id: str,
    timestamps: list[datetime],
    data: dict[str, list[float | None]],
) -> GeoSphereResponse:
    return GeoSphereResponse(
        resource_id=resource_id,
        reference_time=datetime(2026, 7, 15, 0, 0, tzinfo=UTC),
        timestamps=timestamps,
        parameters={
            name: ParameterSeries(name=name, unit="ug m-3", data=values)
            for name, values in data.items()
        },
        grid_longitude=16.362,
        grid_latitude=48.219,
    )


def _chem() -> GeoSphereResponse:
    """Three hourly steps around NOW; 16:00 is the nearest to 15:40."""
    return _response(
        "chem-v2-1h-3km",
        [
            datetime(2026, 7, 15, 14, 0, tzinfo=UTC),
            datetime(2026, 7, 15, 15, 0, tzinfo=UTC),
            datetime(2026, 7, 15, 16, 0, tzinfo=UTC),
        ],
        {
            "no2surf": [10.0, 15.0, 18.0],
            "o3surf": [80.0, 88.0, 92.0],
            "pm10surf": [19.0, 20.0, 21.0],
            "pm25surf": [11.0, 11.5, 12.0],
        },
    )


def _aqi() -> GeoSphereResponse:
    """Daily bands stamped 00:00 UTC for three consecutive days."""
    return _response(
        "chem_aqi-v1-1d-3km",
        [
            datetime(2026, 7, 15, 0, 0, tzinfo=UTC),
            datetime(2026, 7, 16, 0, 0, tzinfo=UTC),
            datetime(2026, 7, 17, 0, 0, tzinfo=UTC),
        ],
        {"aqi": [2.0, 3.0, 2.0]},
    )


def test_merge_reads_pollutants_at_the_nearest_hour() -> None:
    merged = merge_air_quality(_chem(), _aqi(), NOW)
    assert merged["observed_at"] == datetime(2026, 7, 15, 16, 0, tzinfo=UTC)
    assert merged["pollutants"] == {
        "nitrogen_dioxide": 18.0,
        "ozone": 92.0,
        "pm10": 21.0,
        "pm2_5": 12.0,
    }
    # The full series is kept for each pollutant.
    assert len(merged["forecast"]["ozone"]) == 3


def test_merge_matches_the_daily_aqi_by_local_calendar_day() -> None:
    merged = merge_air_quality(_chem(), _aqi(), NOW)
    assert merged["aqi_band_today"] == 2
    assert merged["aqi_band_tomorrow"] == 3
    assert merged["aqi_band_in_2_days"] == 2
    # GeoSphere publishes the band directly, not an underlying numeric index.
    assert merged["aqi_value_today"] is None


def test_merge_local_day_boundary() -> None:
    """A 00:00 UTC stamp is 02:00 local in July: still the same calendar day.

    At 23:30 UTC on the 15th it is already the 16th in Vienna, so the day that
    was "today" at 15:40 must have become "yesterday" and drop out.
    """
    late = datetime(2026, 7, 15, 23, 30, tzinfo=UTC)
    merged = merge_air_quality(_chem(), _aqi(), late)
    assert merged["aqi_band_today"] == 3
    assert merged["aqi_band_tomorrow"] == 2
    assert merged["aqi_band_in_2_days"] is None


def test_merge_without_aqi_keeps_pollutants() -> None:
    merged = merge_air_quality(_chem(), None, NOW)
    assert merged["pollutants"]["pm10"] == 21.0
    assert merged["aqi_band_today"] is None


def test_merge_skips_missing_aqi_values() -> None:
    aqi = _response(
        "chem_aqi-v1-1d-3km",
        [
            datetime(2026, 7, 15, 0, 0, tzinfo=UTC),
            datetime(2026, 7, 16, 0, 0, tzinfo=UTC),
        ],
        {"aqi": [None, 4.0]},
    )
    merged = merge_air_quality(_chem(), aqi, NOW)
    assert merged["aqi_band_today"] is None
    assert merged["aqi_band_tomorrow"] == 4


def test_merge_empty_chem_series() -> None:
    empty = _response("chem-v2-1h-3km", [], {"no2surf": []})
    merged = merge_air_quality(empty, None, NOW)
    assert merged["observed_at"] is None
    assert merged["pollutants"] == {
        "nitrogen_dioxide": None,
        "ozone": None,
        "pm10": None,
        "pm2_5": None,
    }


@pytest.mark.asyncio
async def test_fetch_reports_both_sources() -> None:
    with patch(
        "geosphere_mcp_server.air_quality.async_get_timeseries",
        new=AsyncMock(side_effect=[_chem(), _aqi()]),
    ):
        merged = await async_fetch_air_quality(None, 48.22, 16.37, now=NOW)
    assert merged["sources"] == ["WRF-Chem", "daily AQI"]


@pytest.mark.asyncio
async def test_fetch_degrades_when_the_aqi_fails() -> None:
    """A secondary failure keeps the concentrations alive."""
    with patch(
        "geosphere_mcp_server.air_quality.async_get_timeseries",
        new=AsyncMock(side_effect=[_chem(), GeoSphereApiError("boom")]),
    ):
        merged = await async_fetch_air_quality(None, 48.22, 16.37, now=NOW)
    assert merged["sources"] == ["WRF-Chem"]
    assert merged["pollutants"]["ozone"] == 92.0
    assert merged["aqi_band_today"] is None


@pytest.mark.asyncio
async def test_fetch_propagates_a_chem_failure() -> None:
    """The primary dataset failing is the caller's problem, not a degradation."""
    with (
        patch(
            "geosphere_mcp_server.air_quality.async_get_timeseries",
            new=AsyncMock(side_effect=[GeoSphereOutOfDomainError("outside"), _aqi()]),
        ),
        pytest.raises(GeoSphereOutOfDomainError),
    ):
        await async_fetch_air_quality(None, 48.22, 16.37, now=NOW)
