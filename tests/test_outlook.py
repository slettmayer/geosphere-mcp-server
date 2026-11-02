"""Tests for the pure forecast-outlook functions."""

from __future__ import annotations

from datetime import UTC, datetime, timedelta
from typing import Any

from geosphere_mcp_server.outlook import (
    ensemble_is_current,
    horizon_hours,
    max_cape,
    max_gust,
    next_thunderstorm,
    thunderstorm_outlook,
    window,
)

NOW = datetime(2026, 7, 15, 16, 30, tzinfo=UTC)
CURRENT_RUN = NOW - timedelta(hours=7)
STALE_RUN = NOW - timedelta(hours=13)
# The ensemble's storm signal, both 90th percentiles at their thresholds.
STORM_PAIR = {"cape_p90": 1500.0, "precipitation_p90": 0.5}


def _hour(
    offset_hours: int,
    *,
    gust: float | None = None,
    cape: float | None = None,
    symbol: int | None = None,
    cape_p90: float | None = None,
    precipitation_p90: float | None = None,
) -> dict[str, Any]:
    """One forecast row at the top of the hour, `offset_hours` from 16:00Z."""
    return {
        "time": _at(offset_hours),
        "weather_symbol": symbol,
        "wind_gust_ms": gust,
        "cape_jkg": cape,
        "ensemble_cape_p90_jkg": cape_p90,
        "ensemble_precipitation_p90_mm": precipitation_p90,
    }


def _at(offset_hours: int) -> datetime:
    return datetime(2026, 7, 15, 16, 0, tzinfo=UTC) + timedelta(hours=offset_hours)


def _outlook(
    rows: list[dict[str, Any]], hours: int, run: datetime | None
) -> tuple[bool | None, str | None]:
    return thunderstorm_outlook(window(rows, hours, NOW), ensemble_is_current(run, NOW))


def _next(
    rows: list[dict[str, Any]], run: datetime | None
) -> tuple[datetime | None, str | None]:
    return next_thunderstorm(rows, NOW, ensemble_is_current(run, NOW))


def test_max_gust_includes_the_in_progress_hour() -> None:
    """NOW is 16:30 but the 16:00 hour is still in progress and must count."""
    rows = [_hour(0, gust=20.0), _hour(1, gust=5.0)]
    value, when = max_gust(window(rows, 1, NOW))
    assert value == 20.0
    assert when == datetime(2026, 7, 15, 16, 0, tzinfo=UTC)


def test_max_gust_respects_the_window() -> None:
    """A bigger gust beyond the horizon must not leak into a short window."""
    rows = [_hour(0, gust=5.0), _hour(1, gust=9.0), _hour(6, gust=30.0)]
    assert max_gust(window(rows, 1, NOW))[0] == 9.0
    assert max_gust(window(rows, 12, NOW))[0] == 30.0


def test_max_gust_skips_past_hours_and_none_values() -> None:
    rows = [_hour(-3, gust=40.0), _hour(0, gust=None), _hour(1, gust=7.0)]
    assert max_gust(window(rows, 12, NOW)) == (
        7.0,
        datetime(2026, 7, 15, 17, 0, tzinfo=UTC),
    )


def test_max_gust_empty_window_returns_none() -> None:
    assert max_gust(window([], 12, NOW)) == (None, None)
    assert max_gust(window([_hour(0, gust=None)], 12, NOW)) == (None, None)


def test_max_cape() -> None:
    rows = [_hour(0, cape=100.0), _hour(2, cape=1200.0), _hour(20, cape=3000.0)]
    assert max_cape(window(rows, 12, NOW)) == 1200.0
    assert max_cape(window([], 12, NOW)) is None


def test_ensemble_is_current_within_its_age_bound() -> None:
    assert ensemble_is_current(CURRENT_RUN, NOW)
    assert ensemble_is_current(NOW - timedelta(hours=12) + timedelta(seconds=1), NOW)
    assert not ensemble_is_current(NOW - timedelta(hours=12), NOW)
    assert not ensemble_is_current(STALE_RUN, NOW)
    assert not ensemble_is_current(None, NOW)


def test_storm_signals_symbol_only() -> None:
    rows = [_hour(0, symbol=5), _hour(1, symbol=26)]
    assert _outlook(rows, 1, CURRENT_RUN) == (True, "symbol")
    assert _next(rows, CURRENT_RUN) == (_at(1), "symbol")


def test_storm_signals_ensemble_only() -> None:
    """Some members develop a storm the deterministic run does not."""
    rows = [_hour(0, symbol=5), _hour(1, symbol=8, **STORM_PAIR)]
    assert _outlook(rows, 1, CURRENT_RUN) == (True, "ensemble")
    assert _next(rows, CURRENT_RUN) == (_at(1), "ensemble")


def test_storm_signals_both() -> None:
    """In the same hour, or in different hours of the window."""
    same_hour = [_hour(0, symbol=26, **STORM_PAIR)]
    assert _outlook(same_hour, 1, CURRENT_RUN) == (True, "both")
    assert _next(same_hour, CURRENT_RUN) == (_at(0), "both")
    different_hours = [_hour(0, symbol=27), _hour(1, symbol=5, **STORM_PAIR)]
    assert _outlook(different_hours, 1, CURRENT_RUN) == (True, "both")
    # `next_thunderstorm` names the signals of its own hour.
    assert _next(different_hours, CURRENT_RUN) == (_at(0), "symbol")


def test_storm_signals_none() -> None:
    rows = [_hour(0, symbol=1, cape_p90=20.0, precipitation_p90=0.0)]
    assert _outlook(rows, 1, CURRENT_RUN) == (False, None)
    assert _next(rows, CURRENT_RUN) == (None, None)


def test_storm_signals_ignore_an_ensemble_13_hours_old() -> None:
    rows = [_hour(0, symbol=26), _hour(1, symbol=5, **STORM_PAIR)]
    assert _outlook(rows, 1, STALE_RUN) == (True, "symbol")
    ensemble_only = [_hour(0, symbol=5, **STORM_PAIR)]
    assert _outlook(ensemble_only, 1, STALE_RUN) == (False, None)
    assert _next(ensemble_only, STALE_RUN) == (None, None)


def test_storm_signals_without_an_ensemble_leave_the_symbol() -> None:
    rows = [_hour(0, symbol=5, **STORM_PAIR), _hour(3, symbol=28)]
    assert _outlook(rows, 1, None) == (False, None)
    assert _next(rows, None) == (_at(3), "symbol")


def test_thunderstorm_outlook_is_unknown_without_hours() -> None:
    """Unknown only when no forecast hour falls in the window."""
    assert _outlook([], 1, CURRENT_RUN) == (None, None)
    assert _outlook([_hour(6, symbol=26)], 1, CURRENT_RUN) == (None, None)
    assert _next([], CURRENT_RUN) == (None, None)


def test_thunderstorm_outlook_missing_or_unknown_symbol_is_no_storm() -> None:
    """A symbol gap reads as no storm, never as unknown."""
    rows = [_hour(0), _hour(1, symbol=99)]
    assert _outlook(rows, 1, CURRENT_RUN) == (False, None)


def test_thunderstorm_outlook_counts_every_thunder_code() -> None:
    """26-32 all count, the sleet and snow thunderstorms (29-32) included."""
    for symbol in range(26, 33):
        assert _outlook([_hour(0, symbol=symbol)], 1, None) == (True, "symbol"), symbol


def test_storm_signals_heavy_rain_and_snow_are_not_storms() -> None:
    """The heaviest non-thunder codes stay "no storm", whatever the CAPE."""
    rows = [_hour(0, symbol=10, cape=2500.0), _hour(1, symbol=25, cape=2500.0)]
    assert _outlook(rows, 1, None) == (False, None)
    assert _next(rows, None) == (None, None)


def test_thunderstorm_outlook_ensemble_needs_cape_and_precipitation() -> None:
    """CAPE alone is a routine dry summer afternoon, not a storm."""
    rows = [
        _hour(0, symbol=1, cape_p90=3000.0, precipitation_p90=0.0),
        _hour(1, symbol=3, cape_p90=999.9, precipitation_p90=5.0),
        _hour(2, symbol=3, cape_p90=3000.0, precipitation_p90=None),
    ]
    assert _outlook(rows, 2, CURRENT_RUN) == (False, None)


def test_thunderstorm_outlook_ensemble_thresholds_are_inclusive() -> None:
    rows = [_hour(0, symbol=5, cape_p90=1000.0, precipitation_p90=0.1)]
    assert _outlook(rows, 1, CURRENT_RUN) == (True, "ensemble")


def test_storm_signals_ignore_deterministic_cape() -> None:
    """The deterministic CAPE is a diagnostic; it raises no storm by itself."""
    rows = [_hour(0, symbol=8, cape=3000.0)]
    assert _outlook(rows, 1, CURRENT_RUN) == (False, None)
    assert _next(rows, CURRENT_RUN) == (None, None)


def test_thunderstorm_outlook_ignores_a_storm_beyond_the_window() -> None:
    rows = [_hour(0, symbol=5), _hour(5, symbol=26)]
    assert _outlook(rows, 1, None) == (False, None)
    assert _outlook(rows, 12, None) == (True, "symbol")


def test_next_thunderstorm_scans_the_whole_horizon() -> None:
    """Not window-limited — a storm 40 h out is still reported."""
    rows = [_hour(0, symbol=1), _hour(40, symbol=27)]
    assert _next(rows, None)[0] == datetime(2026, 7, 17, 8, 0, tzinfo=UTC)


def test_next_thunderstorm_ignores_past_hours() -> None:
    rows = [_hour(-5, symbol=26), _hour(3, symbol=5)]
    assert _next(rows, None) == (None, None)


def test_next_thunderstorm_includes_the_in_progress_hour() -> None:
    """A storm in the hour already under way must not be skipped.

    NOW is 16:30 and the in-progress hour is stamped 16:00, so a naive
    `row["time"] < now` floor would drop it — and the returned timestamp is
    then deliberately in the past.
    """
    when, source = _next([_hour(0, symbol=26), _hour(3, symbol=5)], None)
    assert (when, source) == (_at(0), "symbol")
    assert when < NOW


def test_next_thunderstorm_lies_inside_a_positive_window() -> None:
    """Both answers share one per-row test, so they cannot disagree."""
    rows = [_hour(0, symbol=5), _hour(1, symbol=8, **STORM_PAIR)]
    expected, _ = _outlook(rows, 1, CURRENT_RUN)
    when, _ = _next(rows, CURRENT_RUN)
    assert expected is True
    assert when in [row["time"] for row in window(rows, 1, NOW)]


def test_window_rounds_up_to_whole_hourly_steps() -> None:
    """A "1 hour" window spans the in-progress hour plus the next one.

    NOW is 16:30, so the window runs 16:00-17:30 and the 17:00 stamp is in it,
    while 18:00 is not — an event up to ~2 h out can surface in a 1 h window.
    """
    rows = [_hour(0, gust=1.0), _hour(1, gust=2.0), _hour(2, gust=99.0)]
    assert max_gust(window(rows, 1, NOW)) == (
        2.0,
        datetime(2026, 7, 15, 17, 0, tzinfo=UTC),
    )


def test_horizon_hours_measures_what_an_all_clear_covers() -> None:
    """NOW is 16:30, so a series ending at 20:00 leaves 3.5 h -> 4."""
    assert horizon_hours([_hour(0), _hour(4)], NOW) == 4
    # A series already behind us has no horizon left, never a negative one.
    assert horizon_hours([_hour(-5)], NOW) == 0
    assert horizon_hours([], NOW) is None
