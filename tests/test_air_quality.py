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
    assert merged["pollutants"] == {
        "nitrogen_dioxide": 18.0,
        "ozone": 92.0,
        "pm10": 21.0,
        "pm2_5": 12.0,
    }
    # Only the nearest hour is kept — nothing downstream reads a series, and
    # keeping one meant zipping columns of unverified equal length.
    assert "forecast" not in merged


def test_merge_never_reports_a_future_observation_time() -> None:
    """The nearest hour to 15:40 is 16:00, which has not happened yet.

    The concentrations still come from that hour -- it is the closest the
    dataset has -- but `observed_at` is what the caller is told the reading
    describes, and it is rendered as the time the values were observed.
    """
    merged = merge_air_quality(_chem(), _aqi(), NOW)
    assert merged["observed_at"] == NOW


def test_merge_reports_a_past_stamp_unclamped() -> None:
    """The clamp must not flatten genuine staleness into `now`."""
    merged = merge_air_quality(
        _chem(), _aqi(), datetime(2026, 7, 15, 16, 20, tzinfo=UTC)
    )
    assert merged["observed_at"] == datetime(2026, 7, 15, 16, 0, tzinfo=UTC)


def test_merge_matches_the_daily_aqi_by_local_calendar_day() -> None:
    merged = merge_air_quality(_chem(), _aqi(), NOW)
    assert merged["aqi_band_today"] == 2
    assert merged["aqi_band_tomorrow"] == 3
    assert merged["aqi_band_in_2_days"] == 2
    # GeoSphere publishes the band directly, so there is no numeric index key
    # to carry; the renderer supplies the None.
    assert "aqi_value_today" not in merged


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
