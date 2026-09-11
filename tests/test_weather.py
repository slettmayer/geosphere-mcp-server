"""Tests for the pure processing + high-level fetch helpers in weather.py."""

from __future__ import annotations

from datetime import UTC, datetime, timedelta
from unittest.mock import AsyncMock, patch

import pytest

from geosphere_mcp_server import weather
from geosphere_mcp_server.const import NOWCAST_LOOKBACK
from geosphere_mcp_server.geosphere_api import (
    GeoSphereOutOfDomainError,
    GeoSphereResponse,
    ParameterSeries,
)
from geosphere_mcp_server.weather import (
    _diff,
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


# --- GeoSphereResponse.nearest_index ---


def test_nearest_index() -> None:
    stamps = _ts((15, 0), (15, 15), (15, 30), (15, 45))
    assert _response("nowcast", stamps, {}).nearest_index(NOW) == 2
    assert _response("nowcast", [], {}).nearest_index(NOW) is None


def test_nearest_index_can_select_a_future_stamp() -> None:
    """Nearest in either direction, so callers must clamp their own output."""
    stamps = _ts((15, 15), (15, 30), (15, 45))
    when = datetime(2026, 7, 15, 15, 38, tzinfo=UTC)
    assert _response("nowcast", stamps, {}).nearest_index(when) == 2


# --- assemble_hourly_forecast ---


def _arome_forecast() -> GeoSphereResponse:
    """Five stamps, 14:00-18:00. NOW is 15:30, so 15:00/16:00/17:00 are emitted.

    The series runs one stamp past the last emitted hour on purpose: interval
    parameters (`ugust`/`vgust`, the `rr_acc`/`snow_acc` accumulations) describe
    the interval *ending* at their stamp, so the hour beginning at 17:00 reads
    them from 18:00. The accumulations are laid out so the hour beginning
    15:00 catches 1 mm of rain and the one beginning 17:00 catches 1 mm of
    snow.
    """
    stamps = _ts((14, 0), (15, 0), (16, 0), (17, 0), (18, 0))
    return _response(
        "nwp-v1-1h-2500m",
        stamps,
        {
            "t2m": [10.0, 11.0, 12.0, 13.0, 14.0],
            "rh2m": [80.0, 80.0, 80.0, 80.0, 80.0],
            "u10m": [0.0, 0.0, 0.0, 0.0, 0.0],
            "v10m": [-1.0, -2.0, -3.0, -4.0, -5.0],
            "ugust": [0.0, 0.0, 0.0, 0.0, 0.0],
            "vgust": [-2.0, -4.0, -6.0, -8.0, -10.0],
            "tcc": [0.1, 0.2, 0.5, 0.9, 0.9],
            "rr_acc": [0.0, 0.0, 1.0, 3.0, 3.0],
            "snow_acc": [0.0, 0.0, 0.0, 0.0, 1.0],
            "snowlmt": [2000.0, 2000.0, 2000.0, 1000.0, 1000.0],
            "grad": [0.0, 100.0, 200.0, 0.0, 0.0],
            "cape": [0.0, 0.0, 0.0, 1500.0, 1500.0],
        },
        reference_time=datetime(2026, 7, 15, 12, 0, tzinfo=UTC),
    )


def _ensemble() -> GeoSphereResponse:
    # Percentiles cover the hour *ending* at their stamp, so each one belongs
    # to the forecast row an hour earlier: 16:00 -> row 15:00, and so on.
    stamps = _ts((15, 0), (16, 0), (17, 0), (18, 0))
    return _response(
        "ensemble-v1-1h-2500m",
        stamps,
        {
            "rr_p10": [0.0, 0.0, 0.0, 0.5],  # 18:00 p10 wet -> row 17:00, 95
            "rr_p50": [0.0, 0.0, 0.5, 0.8],  # 17:00 p50 wet -> row 16:00, 70
            "rr_p90": [0.0, 0.0, 0.8, 0.9],  # 16:00 all dry -> row 15:00, 0
        },
    )


def test_assemble_hourly_drops_past_hours_and_the_successorless_last() -> None:
    """Hours before the current top-of-hour go, and so does the final stamp.

    The final stamp has no successor to read its interval fields from, so it
    cannot be emitted; 14:00 precedes the cutoff.
    """
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
    assert first["temperature_c"] == 11.0  # instantaneous at the stamp
    # Interval field, so it comes from the following stamp: 1.0 - 0.0.
    assert first["precipitation_mm"] == 1.0
    # Same for the gust: |vgust| at 16:00, not at 15:00.
    assert first["wind_gust_ms"] == 6.0
    assert first["cloud_cover_pct"] == 20.0
    assert first["snow_limit_m"] == 2000.0
    assert first["global_radiation_wm2"] == 100.0
    assert first["dew_point_c"] is not None

    third = result["hourly"][2]  # 17:00, snow accumulates by 18:00 -> snowy
    assert third["snow_mm"] == 1.0
    assert third["condition"] == "snowy"
    assert third["cape_jkg"] == 1500.0


def test_assemble_hourly_pop_comes_from_the_next_ensemble_stamp() -> None:
    """Probability is read one stamp on, so it describes the row's own hour.

    The ensemble percentiles are interval values like AROME's accumulations,
    covering the hour that *ends* at their stamp. Matching them to the row of
    the same stamp would report the probability of the hour already gone --
    and pair it with an amount from the hour the row is actually for.
    """
    result = assemble_hourly_forecast(
        _arome_forecast(), _ensemble(), 48.219, 16.362, NOW
    )
    pop = {h["time"]: h["precipitation_probability_pct"] for h in result["hourly"]}
    assert pop[datetime(2026, 7, 15, 15, 0, tzinfo=UTC)] == 0
    assert pop[datetime(2026, 7, 15, 16, 0, tzinfo=UTC)] == 70
    assert pop[datetime(2026, 7, 15, 17, 0, tzinfo=UTC)] == 95


def test_assemble_hourly_pop_survives_a_coarsening_ensemble() -> None:
    """The percentile's period comes from the series, not a fixed 1 h step.

    Ensembles commonly coarsen along their horizon, and C-LAEF may yet do so.
    Subtracting a hardcoded step from every stamp would then miss every AROME
    row past the break and blank the probability across the whole forecast,
    silently -- no warning, just `None` everywhere. Keying on the preceding
    stamp is right at any cadence.

    Here the series goes 2-hourly after 16:00, so the 18:00 percentile
    describes the period beginning 16:00 and must land on that row.
    """
    ensemble = _response(
        "ensemble-v1-1h-2500m",
        _ts((15, 0), (16, 0), (18, 0)),
        {
            "rr_p10": [0.0, 0.0, 5.0],
            "rr_p50": [0.0, 0.0, 5.0],
            "rr_p90": [0.0, 0.0, 5.0],
        },
    )
    result = assemble_hourly_forecast(_arome_forecast(), ensemble, 48.219, 16.362, NOW)
    pop = {h["time"]: h["precipitation_probability_pct"] for h in result["hourly"]}
    assert pop[datetime(2026, 7, 15, 16, 0, tzinfo=UTC)] == 95
    # The regular part of the series still lands where it did.
    assert pop[datetime(2026, 7, 15, 15, 0, tzinfo=UTC)] == 0


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
    # Nothing observed precipitation, so neither "wet" nor a confident "dry".
    assert merged["is_precipitating"] is None
    assert merged["precipitation_1h_mm"] is None
    # AROME rows are stamped at the top of their hour, so at 15:30 these values
    # are half an hour old. Claiming `now` would hide that.
    assert merged["observed_at"] == datetime(2026, 7, 15, 15, 0, tzinfo=UTC)


def test_merge_observed_at_follows_the_temperature_not_precipitation() -> None:
    """An INCA slice with temperature but no RR must not claim to be current.

    `observed_at` used to come from the RR series alone, so an analysis whose
    precipitation was absent reported `now` while the temperature on display
    was an hour old -- the one case the timestamp exists to catch.
    """
    inca = _response(
        "inca-v1-1h-1km",
        _ts((13, 30), (14, 30)),
        {"T2M": [9.0, 9.5], "RH2M": [70.0, 72.0], "RR": [None, None]},
    )
    merged = merge_current_conditions(
        _nowcast(), inca, _arome_forecast(), 48.219, 16.362, NOW
    )
    assert merged["temperature_c"] == 9.5  # still the 14:30 analysis
    assert merged["observed_at"] == datetime(2026, 7, 15, 14, 30, tzinfo=UTC)


def test_merge_observed_at_takes_the_nowcast_bucket_that_was_matched() -> None:
    """With the nowcast supplying the temperature, its bucket stamp is reported.

    NOW sits exactly on a bucket here, so the stamp and the clock coincide;
    `test_merge_observed_at_reports_the_bucket_not_the_clock` is what
    distinguishes them.
    """
    merged = merge_current_conditions(
        _nowcast(), None, _arome_forecast(), 48.219, 16.362, NOW
    )
    assert merged["temperature_c"] == 21.0  # nowcast at 15:30, not AROME
    assert merged["observed_at"] == datetime(2026, 7, 15, 15, 30, tzinfo=UTC)


def test_merge_observed_at_reports_the_bucket_not_the_clock() -> None:
    """Off the 15-min grid, the matched bucket's stamp is the honest answer.

    `now` is not: no source ever states it, and this timestamp exists to show
    how far behind real time a reading is.
    """
    merged = merge_current_conditions(
        _nowcast(),
        None,
        _arome_forecast(),
        48.219,
        16.362,
        datetime(2026, 7, 15, 15, 37, tzinfo=UTC),
    )
    assert merged["temperature_c"] == 21.0  # 15:30 is nearest 15:37, by a minute
    assert merged["observed_at"] == datetime(2026, 7, 15, 15, 30, tzinfo=UTC)


def test_merge_observed_at_is_never_in_the_future() -> None:
    """The bucket match is nearest, not nearest-in-the-past.

    At 15:38 the 15:45 bucket is closer than 15:30, so the stamp would be
    reported seven minutes ahead of the clock -- an "observation" time later
    than the present, describing a value that is really a short forecast.
    """
    now = datetime(2026, 7, 15, 15, 38, tzinfo=UTC)
    merged = merge_current_conditions(
        _nowcast(), None, _arome_forecast(), 48.219, 16.362, now
    )
    assert merged["observed_at"] <= now


def test_merge_observed_at_ignores_an_analysis_without_temperature() -> None:
    """An INCA slice carrying RR but no T2M must not date the temperature.

    The temperature then falls to the nowcast and is current, so reporting the
    analysis stamp would claim a staleness the displayed value does not have --
    the mirror image of the case above, and equally wrong.
    """
    inca = _response(
        "inca-v1-1h-1km",
        _ts((13, 30), (14, 30)),
        {"T2M": [None, None], "RH2M": [70.0, 72.0], "RR": [0.4, 0.6]},
    )
    merged = merge_current_conditions(
        _nowcast(), inca, _arome_forecast(), 48.219, 16.362, NOW
    )
    assert merged["temperature_c"] == 21.0  # the nowcast, not the analysis
    assert merged["observed_at"] == datetime(2026, 7, 15, 15, 30, tzinfo=UTC)


def _capped_storm_arome() -> GeoSphereResponse:
    """CAPE past the thunder threshold with the lid on.

    Only the observed rate can carry this to `lightning-rainy`.
    """
    return _response(
        "nwp-v1-1h-2500m",
        _ts((14, 0), (15, 0), (16, 0)),
        {
            "t2m": [10.0, 11.0, 12.0],
            "tcc": [0.9, 0.9, 0.9],
            "cape": [1500.0, 1500.0, 1500.0],
            "cin": [-80.0, -80.0, -80.0],
            "rr_acc": [0.0, 0.0, 0.0],
            "snow_acc": [0.0, 0.0, 0.0],
        },
        reference_time=datetime(2026, 7, 15, 12, 0, tzinfo=UTC),
    )


def _storm_nowcast(rr: list[float | None]) -> GeoSphereResponse:
    """Nowcast buckets 15:00-15:45 with `pt` precipitating and `rr` as given.

    NOW is 15:30, so the matched bucket is 15:30 and `RATE_LOOKBACK` reaches
    back to 15:00 -- the first three buckets.
    """
    return _nowcast(
        data={"t2m": [20.0, 20.5, 21.0, 21.5], "rr": rr, "pt": [1, 1, 1, 1]}
    )


def test_merge_dry_bucket_does_not_hide_the_cell_that_just_passed() -> None:
    """The lull between cells of an active storm still reads as a storm.

    The matched bucket (15:30) reads 0.0 while 15:15 caught 2 mm (8 mm/h).
    Taking the matched bucket alone reports 0 mm/h, which starves the downpour
    override that lets observed rain overrule a modelled CIN lid.
    """
    merged = merge_current_conditions(
        _storm_nowcast([0.0, 2.0, 0.0, 0.0]),
        None,
        _capped_storm_arome(),
        48.219,
        16.362,
        NOW,
    )
    assert merged["condition"] == "lightning-rainy"


def test_merge_rate_lookback_reaches_the_edge_of_its_window() -> None:
    """The peak spans the full RATE_LOOKBACK, inclusive of its far edge.

    Only reachable since the nowcast request gained an anchored `start`:
    unbounded, the series held a single bucket and this window collapsed to the
    matched one. 15:00 is exactly RATE_LOOKBACK behind NOW, so its 2 mm bucket
    (8 mm/h) still drives the downpour override.
    """
    merged = merge_current_conditions(
        _storm_nowcast([2.0, 0.0, 0.0, 0.0]),
        None,
        _capped_storm_arome(),
        48.219,
        16.362,
        NOW,
    )
    assert merged["condition"] == "lightning-rainy"


def test_merge_a_shower_that_already_ended_does_not_derive_a_storm() -> None:
    """Rain that stopped must not keep driving the condition.

    INCA's `RR` is a total over the whole past hour, so 6 mm that fell early in
    it and stopped is still on the books while only drizzle continues -- enough
    to keep `pt` non-zero. Reading that total as an instantaneous rate would
    clear POURING_MM_PER_H and derive a thunderstorm from a capped, drizzling
    sky. Every bucket inside RATE_LOOKBACK is dry, so it must not.
    """
    inca = _response(
        "inca-v1-1h-1km",
        _ts((13, 30), (14, 30)),
        {"T2M": [9.0, 9.5], "RR": [6.0, 6.0]},
    )
    merged = merge_current_conditions(
        _storm_nowcast([0.0, 0.0, 0.0, 0.0]),
        inca,
        _capped_storm_arome(),
        48.219,
        16.362,
        NOW,
    )
    assert merged["condition"] == "rainy"


def test_merge_without_inca_rr_reports_no_hourly_accumulation() -> None:
    """Nowcast buckets are never summed into the hourly total.

    The endpoint serves one model run clamped to its own t0, so the buckets on
    hand cover 15-45 min; summing them published a quarter-hour of rain as an
    hour. With no INCA `RR` the field is simply absent.
    """
    nowcast = _nowcast(
        stamps=_ts((14, 45), (15, 0), (15, 15), (15, 30), (15, 45)),
        data={
            "t2m": [20.0, 20.0, 20.0, 20.0, 20.0],
            "td": [10.0, 10.0, 10.0, 10.0, 10.0],
            "rh2m": [60.0, 60.0, 60.0, 60.0, 60.0],
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
    assert merged["precipitation_1h_mm"] is None


def test_merge_is_precipitating_unknown_without_a_nowcast() -> None:
    """INCA `RR` never answers "is it raining now" -- that is an hour total."""
    merged = merge_current_conditions(
        None, _inca(), _arome_forecast(), 48.219, 16.362, NOW
    )
    assert merged["precipitation_1h_mm"] == 0.6  # the accumulation still stands
    assert merged["is_precipitating"] is None


def test_merge_stale_inca_rr_does_not_drive_the_condition() -> None:
    """An INCA slice that stopped updating must not hold the sky on `rainy`."""
    stale = _response(
        "inca-v1-1h-1km",
        _ts(
            (12, 0),
        ),
        {
            "T2M": [20.0],
            "TD2M": [10.0],
            "RH2M": [60.0],
            "RR": [5.0],
            "P0": [101300.0],
            "GL": [150.0],
            "UU": [0.0],
            "VV": [-3.0],
        },
    )
    merged = merge_current_conditions(
        None, stale, _arome_forecast(), 48.219, 16.362, NOW
    )
    # The accumulation is still reported -- it is a real measurement of a past
    # hour -- but 3.5 h past its stamp it no longer derives the condition,
    # which falls through to AROME's 20% cloud cover.
    #
    # Pinned to the exact cloud-derived value, not `!= "rainy"`: an ungated
    # read of RR=5.0 clears POURING_MM_PER_H and derives `pouring`, so the
    # looser assertion passed with the gate removed and tested nothing.
    assert merged["precipitation_1h_mm"] == 5.0
    assert merged["condition"] == "partlycloudy"


def test_merge_future_dated_inca_rr_does_not_drive_the_condition() -> None:
    """A negative age is not freshness.

    `age <= INCA_RR_MAX_AGE_SECONDS` alone is satisfied by a future stamp, so
    an hour that has not happened yet would read as the freshest reading there
    is. The fetch path bounds INCA at `end=now`, but this merge takes its own
    `now` and nothing in the signature ties the two together.
    """
    future = _response(
        "inca-v1-1h-1km",
        _ts(
            (17, 30),
        ),
        {
            "T2M": [20.0],
            "TD2M": [10.0],
            "RH2M": [60.0],
            "RR": [5.0],
            "P0": [101300.0],
            "GL": [150.0],
            "UU": [0.0],
            "VV": [-3.0],
        },
    )
    merged = merge_current_conditions(
        None, future, _arome_forecast(), 48.219, 16.362, NOW
    )
    assert merged["precipitation_1h_mm"] == 5.0
    assert merged["condition"] == "partlycloudy"


def test_merge_fresh_inca_rr_still_drives_the_condition() -> None:
    """Inside INCA_RR_MAX_AGE_SECONDS the accumulation is still evidence."""
    fresh = _response(
        "inca-v1-1h-1km",
        _ts(
            (14, 30),
        ),
        {
            "T2M": [20.0],
            "TD2M": [10.0],
            "RH2M": [60.0],
            "RR": [5.0],
            "P0": [101300.0],
            "GL": [150.0],
            "UU": [0.0],
            "VV": [-3.0],
        },
    )
    merged = merge_current_conditions(
        None, fresh, _arome_forecast(), 48.219, 16.362, NOW
    )
    assert merged["condition"] == "pouring"


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
async def test_async_fetch_current_anchors_the_arome_request() -> None:
    """The AROME call names a `start`, or the hour in progress is not in it.

    An unbounded request begins well after the current hour (measured
    2026-08-12 05:54Z: first stamp 07:00), so `_arome_current` would take the
    first row it finds -- a future one -- and present its cloud, CAPE and CIN
    as current. CIN gates the current condition's thunder verdict, so this is
    not cosmetic. The anchor is the top of the hour, not `now`, because a
    mid-hour `start` rounds up to the next stamp.
    """
    mock = AsyncMock(side_effect=[_arome_forecast(), _nowcast(), _inca()])
    with patch.object(weather, "async_get_timeseries", mock):
        await async_fetch_current_conditions(None, 48.219, 16.362, now=NOW)

    arome_call = mock.await_args_list[0]
    expected = NOW.replace(minute=0, second=0, microsecond=0) - timedelta(hours=1)
    assert arome_call.kwargs["start"] == expected


@pytest.mark.asyncio
async def test_async_fetch_current_anchors_the_nowcast_request() -> None:
    """The nowcast call names a `start` floored to the 15-min grid.

    Unbounded, the endpoint begins at the bucket covering `now`, leaving one
    stamp at/before it -- which silently reduces the `RATE_LOOKBACK` peak to
    the matched bucket it exists to widen. A mid-interval `start` rounds *up*
    to the next stamp, so the anchor is the bucket boundary, not `now`.
    """
    mock = AsyncMock(side_effect=[_arome_forecast(), _nowcast(), _inca()])
    now = datetime(2026, 7, 15, 15, 38, tzinfo=UTC)
    with patch.object(weather, "async_get_timeseries", mock):
        await async_fetch_current_conditions(None, 48.219, 16.362, now=now)

    nowcast_call = mock.await_args_list[1]
    bucket = datetime(2026, 7, 15, 15, 30, tzinfo=UTC)
    assert nowcast_call.kwargs["start"] == bucket - NOWCAST_LOOKBACK


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


@pytest.mark.asyncio
async def test_async_fetch_hourly_requests_an_hour_of_history() -> None:
    """Both requests ask for one hour before the current top of the hour.

    An unbounded request begins well after the current hour, so `start` has to
    be named or the hour already under way is dropped -- which breaks the storm
    outlook's "first entry is the in-progress hour" contract.

    The anchor is the top of the hour, not `now`: the API honours a `start`
    that lands exactly on a stamp and rounds a mid-hour one *up* to the next,
    so anchoring to `now` at 15:30 with no lookback would come back at 16:00
    and lose the hour under way. The lookback is margin on top of the anchor.
    """
    mock = AsyncMock(side_effect=[_arome_forecast(), _ensemble()])
    with patch.object(weather, "async_get_timeseries", mock):
        await async_fetch_hourly_forecast(None, 48.219, 16.362, now=NOW)

    expected = NOW.replace(minute=0, second=0, microsecond=0) - timedelta(hours=1)
    assert mock.await_count == 2
    for call in mock.await_args_list:
        assert call.kwargs["start"] == expected


@pytest.mark.asyncio
async def test_async_fetch_hourly_keeps_the_in_progress_hour() -> None:
    """With the predecessor present, the series starts at the current hour."""
    mock = AsyncMock(side_effect=[_arome_forecast(), _ensemble()])
    with patch.object(weather, "async_get_timeseries", mock):
        result = await async_fetch_hourly_forecast(None, 48.219, 16.362, now=NOW)

    top_of_hour = NOW.replace(minute=0, second=0, microsecond=0)
    assert result["hourly"][0]["time"] == top_of_hour
    # And it carries a real accumulation delta rather than a None hole.
    assert result["hourly"][0]["precipitation_mm"] is not None


def test_arome_current_reads_the_hour_in_progress() -> None:
    """The snapshot must not skip index 0 when the API trims to the current hour.

    The live forecast endpoint starts the series at the current top-of-hour, so
    a `range(1, ...)` scan lands on the hour *after* now. Every field read here
    is instantaneous -- unlike the accumulation deltas that force the hourly
    assembly to skip its first step -- and CIN from the wrong hour flips the
    current condition's thunder verdict.
    """
    trimmed = _response(
        "nwp-v1-1h-2500m",
        _ts((15, 0), (16, 0)),  # NOW is 15:30, so index 0 is the hour under way
        {
            "t2m": [20.0, 30.0],
            "tcc": [0.9, 0.1],
            "cape": [1800.0, 50.0],
            "cin": [-5.0, -400.0],
        },
    )
    current = weather._arome_current(trimmed, NOW)
    assert current is not None
    assert current["temperature"] == 20.0
    assert current["cape"] == 1800.0
    assert current["cin"] == -5.0


def test_merge_current_gates_thunder_on_the_current_hour() -> None:
    """A storm under way is not cancelled by the next hour being capped."""
    trimmed = _response(
        "nwp-v1-1h-2500m",
        _ts((15, 0), (16, 0)),
        {
            "t2m": [20.0, 20.0],
            "tcc": [0.9, 0.9],
            "cape": [1800.0, 1800.0],
            "cin": [-5.0, -400.0],  # uncapped now, strongly capped next hour
        },
    )
    merged = merge_current_conditions(None, None, trimmed, 48.219, 16.362, NOW)
    assert merged["cin_jkg"] == -5.0
    assert merged["condition"] == "lightning"


@pytest.mark.asyncio
async def test_async_fetch_hourly_bounds_the_request_to_the_window() -> None:
    """`hours=N` must not pull the whole ~60 h horizon and discard most of it."""
    mock = AsyncMock(side_effect=[_arome_forecast(), _ensemble()])
    with patch.object(weather, "async_get_timeseries", mock):
        await async_fetch_hourly_forecast(None, 48.219, 16.362, hours=6, now=NOW)

    top_of_hour = NOW.replace(minute=0, second=0, microsecond=0)
    for call in mock.await_args_list:
        assert call.kwargs["start"] == top_of_hour - timedelta(hours=1)
        # Two hours of slack past the requested window: one because the last
        # hour's interval fields (gust, precipitation) live on the following
        # stamp, and one so rounding at the boundary cannot clip that
        # successor.
        assert call.kwargs["end"] == top_of_hour + timedelta(hours=8)


@pytest.mark.asyncio
async def test_async_fetch_hourly_bounds_from_an_explicit_start() -> None:
    """A `start` in the future moves the whole fetched window with it."""
    mock = AsyncMock(side_effect=[_arome_forecast(), _ensemble()])
    later = NOW.replace(minute=0, second=0, microsecond=0) + timedelta(hours=10)
    with patch.object(weather, "async_get_timeseries", mock):
        await async_fetch_hourly_forecast(
            None, 48.219, 16.362, hours=4, start=later, now=NOW
        )

    for call in mock.await_args_list:
        assert call.kwargs["start"] == later - timedelta(hours=1)
        assert call.kwargs["end"] == later + timedelta(hours=6)


@pytest.mark.asyncio
async def test_async_fetch_hourly_start_in_the_past_is_ignored_for_bounds() -> None:
    """A `start` before now must not drag the window backwards."""
    mock = AsyncMock(side_effect=[_arome_forecast(), _ensemble()])
    earlier = NOW - timedelta(hours=8)
    with patch.object(weather, "async_get_timeseries", mock):
        await async_fetch_hourly_forecast(
            None, 48.219, 16.362, hours=3, start=earlier, now=NOW
        )

    top_of_hour = NOW.replace(minute=0, second=0, microsecond=0)
    for call in mock.await_args_list:
        assert call.kwargs["start"] == top_of_hour - timedelta(hours=1)


def test_assemble_hourly_interval_fields_come_from_the_next_stamp() -> None:
    """Rain that fell before the stamp must not be reported at it.

    AROME's `rr_acc` is accumulated since the run start and `ugust`/`vgust` are
    the maximum "in the last forecast intervall", so both describe the interval
    *ending* at their stamp. A row stamped T covers T..T+1h and has to read
    them one step on; reading at T reports the hour that already ended.
    """
    arome = _response(
        "nwp-v1-1h-2500m",
        _ts((15, 0), (16, 0), (17, 0)),
        {
            "t2m": [20.0, 20.0, 20.0],
            "tcc": [0.1, 0.1, 0.1],
            # 5 mm fell between 14:00 and 15:00; nothing after.
            "rr_acc": [5.0, 5.0, 5.0],
            "snow_acc": [0.0, 0.0, 0.0],
            "ugust": [0.0, 0.0, 0.0],
            "vgust": [-30.0, -2.0, -2.0],
        },
    )
    first = assemble_hourly_forecast(arome, None, 48.219, 16.362, NOW)["hourly"][0]
    assert first["time"] == datetime(2026, 7, 15, 15, 0, tzinfo=UTC)
    # Reading rr_acc at index 1 would have differenced against the 14:00 total
    # and reported 5 mm of rain for an hour that is dry.
    assert first["precipitation_mm"] == 0.0
    assert first["condition"] == "sunny"
    # Likewise the 30 m/s gust belongs to 14:00-15:00, not to this hour.
    assert first["wind_gust_ms"] == 2.0


def test_arome_current_gust_covers_the_hour_in_progress() -> None:
    """The gust is the one interval field in the current snapshot."""
    trimmed = _response(
        "nwp-v1-1h-2500m",
        _ts((15, 0), (16, 0)),  # NOW is 15:30, so 15:00 is the hour under way
        {
            "t2m": [20.0, 20.0],
            # The 15:00 stamp's gust peaked over 14:00-15:00 and is history;
            # the hour in progress is the one ending at 16:00.
            "ugust": [0.0, 0.0],
            "vgust": [-30.0, -9.0],
        },
    )
    current = weather._arome_current(trimmed, NOW)
    assert current is not None
    assert current["wind_gust_speed"] == 9.0
