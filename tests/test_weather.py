"""Tests for the pure processing + high-level fetch helpers in weather.py."""

from __future__ import annotations

from datetime import UTC, datetime
from unittest.mock import AsyncMock, patch

import pytest

from geosphere_mcp_server import weather
from geosphere_mcp_server.const import WMO_CONDITION_MAP
from geosphere_mcp_server.geosphere_api import (
    GeoSphereOutOfDomainError,
    GeoSphereResponse,
    ParameterSeries,
)
from geosphere_mcp_server.weather import (
    _diff,
    _nearest_index,
    _percent,
    _precipitation_probability,
    assemble_hourly_forecast,
    async_fetch_current_conditions,
    async_fetch_hourly_forecast,
    merge_current_conditions,
)

NOW = datetime(2026, 7, 15, 15, 30, tzinfo=UTC)


def _ts(*hours_minutes: tuple[int, int]) -> list[datetime]:
    """Build a list of aware UTC timestamps on 2026-07-15."""
    return [datetime(2026, 7, 15, h, m, tzinfo=UTC) for h, m in hours_minutes]


def _response(
    resource_id: str,
    timestamps: list[datetime],
    data: dict[str, list[float | None]],
    *,
    reference_time: datetime | None = None,
    latitude: float = 48.219,
    longitude: float = 16.362,
) -> GeoSphereResponse:
    """Assemble a GeoSphereResponse from raw parameter series."""
    return GeoSphereResponse(
        resource_id=resource_id,
        reference_time=reference_time,
        timestamps=timestamps,
        parameters={
            name: ParameterSeries(name=name, unit="", data=values)
            for name, values in data.items()
        },
        grid_longitude=longitude,
        grid_latitude=latitude,
    )


# --- _percent ---


def test_percent_scales_0_1_to_pct() -> None:
    assert _percent(0.5) == 50.0
    assert _percent(1.0) == 100.0
    assert _percent(None) is None


# --- _precipitation_probability ---


@pytest.mark.parametrize(
    ("p10", "p50", "p90", "expected"),
    [
        (0.2, 0.5, 0.8, 95),  # p10 wet -> highest step
        (0.0, 0.5, 0.8, 70),  # p50 wet
        (0.0, 0.0, 0.8, 30),  # only p90 wet
        (0.0, 0.0, 0.0, 0),  # all dry
        (None, None, None, None),  # no p90 -> unknown
        (0.05, 0.05, 0.05, 0),  # below threshold -> dry
    ],
)
def test_precipitation_probability(p10, p50, p90, expected) -> None:
    assert _precipitation_probability(p10, p50, p90) == expected


# --- _diff ---


def test_diff_accumulation() -> None:
    series = [0.0, 1.0, 3.0, 3.0]
    assert _diff(series, 0) is None  # no predecessor
    assert _diff(series, 1) == 1.0
    assert _diff(series, 2) == 2.0
    assert _diff(series, 3) == 0.0


def test_diff_clamps_negative_reset() -> None:
    """A model-run reset (accumulation drops) clamps to 0, not negative."""
    assert _diff([5.0, 1.0], 1) == 0.0


def test_diff_none_and_out_of_range() -> None:
    assert _diff([None, 1.0], 1) is None
    assert _diff([0.0, 1.0], 5) is None


# --- _nearest_index ---


def test_nearest_index() -> None:
    stamps = _ts((15, 0), (15, 15), (15, 30), (15, 45))
    assert _nearest_index(stamps, NOW) == 2
    assert _nearest_index([], NOW) is None


# --- WMO map completeness ---


def test_wmo_condition_map_covers_0_to_99() -> None:
    """Every integer code 0-99 maps to a valid HA condition string."""
    valid = {
        "sunny",
        "clear-night",
        "partlycloudy",
        "cloudy",
        "fog",
        "rainy",
        "pouring",
        "snowy",
        "snowy-rainy",
        "lightning",
        "lightning-rainy",
        "windy",
        "windy-variant",
    }
    assert sorted(WMO_CONDITION_MAP) == list(range(100))
    assert all(v in valid for v in WMO_CONDITION_MAP.values())


# --- assemble_hourly_forecast ---


def _arome_forecast() -> GeoSphereResponse:
    stamps = _ts((14, 0), (15, 0), (16, 0), (17, 0))
    return _response(
        "nwp-v1-1h-2500m",
        stamps,
        {
            "t2m": [10.0, 11.0, 12.0, 13.0],
            "rh2m": [80.0, 80.0, 80.0, 80.0],
            "u10m": [0.0, 0.0, 0.0, 0.0],
            "v10m": [-1.0, -2.0, -3.0, -4.0],
            "ugust": [0.0, 0.0, 0.0, 0.0],
            "vgust": [-2.0, -4.0, -6.0, -8.0],
            "tcc": [0.1, 0.2, 0.5, 0.9],
            "rr_acc": [0.0, 1.0, 3.0, 3.0],
            "snow_acc": [0.0, 0.0, 0.0, 1.0],
            "snowlmt": [2000.0, 2000.0, 2000.0, 1000.0],
            "grad": [0.0, 100.0, 200.0, 0.0],
            "cape": [0.0, 0.0, 0.0, 1500.0],
        },
        reference_time=datetime(2026, 7, 15, 12, 0, tzinfo=UTC),
    )


def _ensemble() -> GeoSphereResponse:
    stamps = _ts((15, 0), (16, 0), (17, 0))
    return _response(
        "ensemble-v1-1h-2500m",
        stamps,
        {
            "rr_p10": [0.0, 0.0, 0.5],  # 17:00 p10 wet -> 95
            "rr_p50": [0.0, 0.5, 0.8],  # 16:00 p50 wet -> 70
            "rr_p90": [0.0, 0.8, 0.9],  # 15:00 all dry -> 0
        },
    )


def test_assemble_hourly_skips_index_zero_and_past() -> None:
    """Index 0 and hours before the current top-of-hour are dropped."""
    result = assemble_hourly_forecast(
        _arome_forecast(), _ensemble(), 48.219, 16.362, NOW
    )
    times = [h["time"] for h in result["hourly"]]
    assert times == _ts((15, 0), (16, 0), (17, 0))
    assert result["reference_time"] == datetime(2026, 7, 15, 12, 0, tzinfo=UTC)
    assert result["grid_latitude"] == pytest.approx(48.219)


def test_assemble_hourly_fields_and_diffs() -> None:
    result = assemble_hourly_forecast(
        _arome_forecast(), _ensemble(), 48.219, 16.362, NOW
    )
    first = result["hourly"][0]  # 15:00
    assert first["temperature_c"] == 11.0
    assert first["precipitation_mm"] == 1.0  # 1.0 - 0.0
    assert first["cloud_cover_pct"] == 20.0
    assert first["snow_limit_m"] == 2000.0
    assert first["global_radiation_wm2"] == 100.0
    assert first["dew_point_c"] is not None

    third = result["hourly"][2]  # 17:00, snow accumulates -> snowy
    assert third["snow_mm"] == 1.0
    assert third["condition"] == "snowy"
    assert third["cape_jkg"] == 1500.0


def test_assemble_hourly_pop_matched_by_timestamp() -> None:
    """Precipitation probability is matched to the exact ensemble hour."""
    result = assemble_hourly_forecast(
        _arome_forecast(), _ensemble(), 48.219, 16.362, NOW
    )
    pop = {h["time"]: h["precipitation_probability_pct"] for h in result["hourly"]}
    assert pop[datetime(2026, 7, 15, 15, 0, tzinfo=UTC)] == 0
    assert pop[datetime(2026, 7, 15, 16, 0, tzinfo=UTC)] == 70
    assert pop[datetime(2026, 7, 15, 17, 0, tzinfo=UTC)] == 95


def test_assemble_hourly_without_ensemble_omits_pop() -> None:
    result = assemble_hourly_forecast(_arome_forecast(), None, 48.219, 16.362, NOW)
    assert all(h["precipitation_probability_pct"] is None for h in result["hourly"])


def test_assemble_hourly_start_and_hours_filters() -> None:
    start = datetime(2026, 7, 15, 16, 0, tzinfo=UTC)
    result = assemble_hourly_forecast(
        _arome_forecast(), _ensemble(), 48.219, 16.362, NOW, start=start
    )
    assert [h["time"] for h in result["hourly"]] == _ts((16, 0), (17, 0))

    limited = assemble_hourly_forecast(
        _arome_forecast(), _ensemble(), 48.219, 16.362, NOW, hours=1
    )
    assert len(limited["hourly"]) == 1
    assert limited["hourly"][0]["time"] == datetime(2026, 7, 15, 15, 0, tzinfo=UTC)


# --- merge_current_conditions ---


def _nowcast(
    stamps: list[datetime] | None = None,
    data: dict[str, list[float | None]] | None = None,
) -> GeoSphereResponse:
    stamps = stamps or _ts((15, 0), (15, 15), (15, 30), (15, 45))
    default: dict[str, list[float | None]] = {
        "t2m": [20.0, 20.5, 21.0, 21.5],
        "td": [10.0, 10.0, 10.0, 10.0],
        "rh2m": [60.0, 60.0, 60.0, 60.0],
        "rr": [0.0, 0.0, 0.0, 0.0],
        "pt": [255, 255, 255, 255],
        "dd": [180.0, 180.0, 180.0, 180.0],
        "ff": [2.0, 2.0, 2.0, 2.0],
        "fx": [5.0, 5.0, 5.0, 5.0],
    }
    return _response("nowcast-v1-15min-1km", stamps, data or default)


def _inca() -> GeoSphereResponse:
    stamps = _ts((13, 30), (14, 30))
    return _response(
        "inca-v1-1h-1km",
        stamps,
        {
            "T2M": [9.0, 9.5],
            "TD2M": [5.0, 5.5],
            "RH2M": [70.0, 72.0],
            "RR": [0.4, 0.6],
            "P0": [101300.0, 101300.0],
            "GL": [100.0, 150.0],
            "UU": [0.0, 0.0],
            "VV": [-3.0, -3.0],
        },
    )


def test_merge_prefers_inca_over_nowcast_and_arome() -> None:
    merged = merge_current_conditions(
        _nowcast(), _inca(), _arome_forecast(), 48.219, 16.362, NOW
    )
    # INCA latest (14:30) wins for temp/humidity/wind/dew/pressure.
    assert merged["temperature_c"] == 9.5
    assert merged["humidity_pct"] == 72.0
    assert merged["dew_point_c"] == 5.5
    assert merged["pressure_hpa"] == 1013.0  # 101300 Pa -> hPa
    assert merged["global_radiation_wm2"] == 150.0
    assert merged["precipitation_1h_mm"] == 0.6  # INCA RR
    assert merged["observed_at"] == datetime(2026, 7, 15, 14, 30, tzinfo=UTC)
    # Cloud/CAPE come from AROME's current-hour snapshot (15:00).
    assert merged["cloud_cover_pct"] == 20.0
    # Gust prefers nowcast fx.
    assert merged["wind_gust_ms"] == 5.0


def test_merge_arome_only_degraded() -> None:
    """With INCA and nowcast gone, values fall back to the AROME snapshot."""
    merged = merge_current_conditions(
        None, None, _arome_forecast(), 48.219, 16.362, NOW
    )
    assert merged["temperature_c"] == 11.0  # AROME 15:00
    assert merged["humidity_pct"] == 80.0
    assert merged["pressure_hpa"] is None  # INCA-only field
    assert merged["global_radiation_wm2"] is None
    assert merged["snow_limit_m"] == 2000.0
    assert merged["is_precipitating"] is False


def test_merge_nowcast_four_bucket_precip_sum() -> None:
    """Without INCA RR, 1-h precip sums the last four 15-min nowcast buckets."""
    nowcast = _nowcast(
        stamps=_ts((14, 45), (15, 0), (15, 15), (15, 30), (15, 45)),
        data={
            "t2m": [20.0, 20.0, 20.0, 20.0, 20.0],
            "td": [10.0, 10.0, 10.0, 10.0, 10.0],
            "rh2m": [60.0, 60.0, 60.0, 60.0, 60.0],
            # five buckets; only the four at/before 15:30 count (last is future)
            "rr": [0.5, 0.1, 0.2, 0.3, 9.0],
            "pt": [255, 255, 255, 255, 255],
            "dd": [180.0, 180.0, 180.0, 180.0, 180.0],
            "ff": [2.0, 2.0, 2.0, 2.0, 2.0],
            "fx": [5.0, 5.0, 5.0, 5.0, 5.0],
        },
    )
    merged = merge_current_conditions(
        nowcast, None, _arome_forecast(), 48.219, 16.362, NOW
    )
    # 0.5 + 0.1 + 0.2 + 0.3 = 1.1 (the 15:45 future bucket excluded)
    assert merged["precipitation_1h_mm"] == 1.1


def test_merge_precip_type_255_not_precipitating() -> None:
    merged = merge_current_conditions(
        _nowcast(), _inca(), _arome_forecast(), 48.219, 16.362, NOW
    )
    assert merged["precipitation_type"] == 255
    assert merged["is_precipitating"] is False


def test_merge_precip_type_signals_precipitation() -> None:
    nowcast = _nowcast(
        data={
            "t2m": [15.0, 15.0, 15.0, 15.0],
            "td": [12.0, 12.0, 12.0, 12.0],
            "rh2m": [90.0, 90.0, 90.0, 90.0],
            "rr": [0.5, 0.5, 0.5, 0.5],
            "pt": [1, 1, 1, 1],
            "dd": [180.0, 180.0, 180.0, 180.0],
            "ff": [2.0, 2.0, 2.0, 2.0],
            "fx": [5.0, 5.0, 5.0, 5.0],
        }
    )
    merged = merge_current_conditions(
        nowcast, None, _arome_forecast(), 48.219, 16.362, NOW
    )
    assert merged["precipitation_type"] == 1
    assert merged["is_precipitating"] is True
    assert merged["condition"] == "rainy"


# --- async_fetch_current_conditions ---


@pytest.mark.asyncio
async def test_async_fetch_current_all_sources() -> None:
    mock = AsyncMock(side_effect=[_arome_forecast(), _nowcast(), _inca()])
    with patch.object(weather, "async_get_timeseries", mock):
        result = await async_fetch_current_conditions(None, 48.219, 16.362, now=NOW)
    assert result["sources"] == ["INCA", "nowcast", "AROME"]
    assert result["temperature_c"] == 9.5
    assert result["grid_latitude"] == pytest.approx(48.219)


@pytest.mark.asyncio
async def test_async_fetch_current_degrades_to_arome_only() -> None:
    """INCA/nowcast out-of-domain degrade to an AROME-only snapshot."""
    mock = AsyncMock(
        side_effect=[
            _arome_forecast(),
            GeoSphereOutOfDomainError("nowcast oob"),
            GeoSphereOutOfDomainError("inca oob"),
        ]
    )
    with patch.object(weather, "async_get_timeseries", mock):
        result = await async_fetch_current_conditions(None, 47.0, 12.0, now=NOW)
    assert result["sources"] == ["AROME"]
    assert result["temperature_c"] == 11.0


@pytest.mark.asyncio
async def test_async_fetch_current_arome_out_of_domain_raises() -> None:
    """AROME out of domain propagates so the caller can fall back."""
    mock = AsyncMock(
        side_effect=[
            GeoSphereOutOfDomainError("arome oob"),
            GeoSphereOutOfDomainError("nowcast oob"),
            GeoSphereOutOfDomainError("inca oob"),
        ]
    )
    with (
        patch.object(weather, "async_get_timeseries", mock),
        pytest.raises(GeoSphereOutOfDomainError),
    ):
        await async_fetch_current_conditions(None, 38.7, -9.1, now=NOW)


# --- async_fetch_hourly_forecast ---


@pytest.mark.asyncio
async def test_async_fetch_hourly_with_ensemble() -> None:
    mock = AsyncMock(side_effect=[_arome_forecast(), _ensemble()])
    with patch.object(weather, "async_get_timeseries", mock):
        result = await async_fetch_hourly_forecast(
            None, 48.219, 16.362, hours=24, now=NOW
        )
    assert result["sources"] == ["AROME", "C-LAEF ensemble"]
    pop = {h["time"]: h["precipitation_probability_pct"] for h in result["hourly"]}
    assert pop[datetime(2026, 7, 15, 16, 0, tzinfo=UTC)] == 70


@pytest.mark.asyncio
async def test_async_fetch_hourly_ensemble_failure_omits_pop() -> None:
    mock = AsyncMock(
        side_effect=[_arome_forecast(), GeoSphereOutOfDomainError("ens oob")]
    )
    with patch.object(weather, "async_get_timeseries", mock):
        result = await async_fetch_hourly_forecast(None, 48.219, 16.362, now=NOW)
    assert result["sources"] == ["AROME"]
    assert all(h["precipitation_probability_pct"] is None for h in result["hourly"])


@pytest.mark.asyncio
async def test_async_fetch_hourly_arome_out_of_domain_raises() -> None:
    mock = AsyncMock(
        side_effect=[
            GeoSphereOutOfDomainError("arome oob"),
            GeoSphereOutOfDomainError("ens oob"),
        ]
    )
    with (
        patch.object(weather, "async_get_timeseries", mock),
        pytest.raises(GeoSphereOutOfDomainError),
    ):
        await async_fetch_hourly_forecast(None, 38.7, -9.1, now=NOW)
