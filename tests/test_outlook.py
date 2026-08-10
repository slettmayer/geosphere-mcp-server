"""Tests for the pure forecast-outlook functions.

Cases ported from ha-geosphere-next tests/test_outlook.py, adapted to the row
dicts this server passes around instead of the HourlyForecast dataclass.
"""

from __future__ import annotations

from datetime import UTC, datetime, timedelta
from typing import Any

from geosphere_mcp_server.outlook import (
    hour_at,
    max_cape,
    max_gust,
    next_thunderstorm,
    thunderstorm_outlook,
)

NOW = datetime(2026, 7, 15, 16, 30, tzinfo=UTC)


def _hour(
    offset_hours: int,
    *,
    gust: float | None = None,
    cape: float | None = None,
    cin: float | None = None,
    precipitation: float | None = None,
    condition: str | None = None,
) -> dict[str, Any]:
    """One forecast row at the top of the hour, `offset_hours` from 16:00Z."""
    return {
        "time": datetime(2026, 7, 15, 16, 0, tzinfo=UTC)
        + timedelta(hours=offset_hours),
        "condition": condition,
        "wind_gust_ms": gust,
        "cape_jkg": cape,
        "cin_jkg": cin,
        "precipitation_mm": precipitation,
    }


def test_max_gust_includes_the_in_progress_hour() -> None:
    """NOW is 16:30 but the 16:00 hour is still in progress and must count."""
    rows = [_hour(0, gust=20.0), _hour(1, gust=5.0)]
    value, when = max_gust(rows, 1, NOW)
    assert value == 20.0
    assert when == datetime(2026, 7, 15, 16, 0, tzinfo=UTC)


def test_max_gust_respects_the_window() -> None:
    """A bigger gust beyond the horizon must not leak into a short window."""
    rows = [_hour(0, gust=5.0), _hour(1, gust=9.0), _hour(6, gust=30.0)]
    assert max_gust(rows, 1, NOW)[0] == 9.0
    assert max_gust(rows, 12, NOW)[0] == 30.0


def test_max_gust_skips_past_hours_and_none_values() -> None:
    rows = [_hour(-3, gust=40.0), _hour(0, gust=None), _hour(1, gust=7.0)]
    assert max_gust(rows, 12, NOW) == (7.0, datetime(2026, 7, 15, 17, 0, tzinfo=UTC))


def test_max_gust_empty_window_returns_none() -> None:
    assert max_gust([], 12, NOW) == (None, None)
    assert max_gust([_hour(0, gust=None)], 12, NOW) == (None, None)


def test_max_cape() -> None:
    rows = [_hour(0, cape=100.0), _hour(2, cape=1200.0), _hour(20, cape=3000.0)]
    assert max_cape(rows, 12, NOW) == 1200.0
    assert max_cape([], 12, NOW) is None


def test_next_thunderstorm_returns_first_lightning_hour_and_its_cape() -> None:
    rows = [
        _hour(0, condition="cloudy", cape=100.0),
        _hour(4, condition="lightning-rainy", cape=1800.0),
        _hour(6, condition="lightning", cape=2500.0),
    ]
    when, cape = next_thunderstorm(rows, NOW)
    assert when == datetime(2026, 7, 15, 20, 0, tzinfo=UTC)
    assert cape == 1800.0


def test_next_thunderstorm_scans_the_whole_horizon() -> None:
    """Not window-limited — a storm 40 h out is still reported."""
    rows = [_hour(0, condition="sunny"), _hour(40, condition="lightning")]
    assert next_thunderstorm(rows, NOW)[0] == datetime(2026, 7, 17, 8, 0, tzinfo=UTC)


def test_next_thunderstorm_ignores_past_hours() -> None:
    rows = [_hour(-5, condition="lightning"), _hour(3, condition="cloudy")]
    assert next_thunderstorm(rows, NOW) == (None, None)


def test_next_thunderstorm_none_when_calm() -> None:
    assert next_thunderstorm([_hour(0, condition="sunny")], NOW) == (None, None)


def test_next_thunderstorm_includes_the_in_progress_hour() -> None:
    """A storm in the hour already under way must not be skipped.

    NOW is 16:30 and the in-progress hour is stamped 16:00, so a naive
    `row["time"] < now` floor would drop it — and the returned timestamp is
    then deliberately in the past.
    """
    rows = [_hour(0, condition="lightning", cape=1600.0), _hour(3, condition="cloudy")]
    when, cape = next_thunderstorm(rows, NOW)
    assert when == datetime(2026, 7, 15, 16, 0, tzinfo=UTC)
    assert when < NOW
    assert cape == 1600.0


def test_thundersnow_is_detected_despite_the_snowy_condition() -> None:
    """`derive_condition` returns `snowy` before it ever checks thunder.

    A snow hour with ample CAPE, weak inhibition and precipitation is a
    thundersnow hour — the condition string alone would miss it.
    """
    rows = [_hour(0, condition="snowy", cape=1500.0, cin=0.0, precipitation=0.8)]
    assert next_thunderstorm(rows, NOW)[0] == datetime(2026, 7, 15, 16, 0, tzinfo=UTC)


def test_thunder_is_detected_when_cloud_cover_is_missing() -> None:
    """`derive_condition` returns None without `tcc`, hiding a real storm."""
    rows = [_hour(0, condition=None, cape=1500.0, cin=0.0, precipitation=0.5)]
    assert thunderstorm_outlook(rows, 1, NOW) is True


def test_dry_high_cape_without_precipitation_is_not_a_storm() -> None:
    """The false-positive guard: CAPE alone is a routine summer afternoon."""
    rows = [
        _hour(0, condition="partlycloudy", cape=2500.0, cin=0.0, precipitation=0.0),
        _hour(1, condition="sunny", cape=3000.0, cin=0.0, precipitation=None),
    ]
    assert thunderstorm_outlook(rows, 1, NOW) is False
    assert next_thunderstorm(rows, NOW) == (None, None)


def test_capped_cape_with_precipitation_is_not_a_storm() -> None:
    """A strong lid (cin <= -50) blocks convection even with rain falling."""
    rows = [_hour(0, condition="snowy", cape=2500.0, cin=-80.0, precipitation=1.0)]
    assert thunderstorm_outlook(rows, 1, NOW) is False


def test_missing_cin_key_reads_as_uncapped() -> None:
    """Open-Meteo rows carry no inhibition at all — thunder gates on CAPE."""
    row = {
        "time": datetime(2026, 7, 15, 16, 0, tzinfo=UTC),
        "condition": None,
        "cape_jkg": 1500.0,
        "precipitation_mm": 0.5,
    }
    assert thunderstorm_outlook([row], 1, NOW) is True


def test_thunderstorm_outlook_is_unknown_without_usable_hours() -> None:
    """An empty or undecidable window must not read as a confident "no storm"."""
    assert thunderstorm_outlook([], 1, NOW) is None
    # Hours exist but carry neither a condition nor CAPE.
    assert thunderstorm_outlook([_hour(0), _hour(1)], 1, NOW) is None
    # Only hours outside the window -> nothing to judge.
    assert thunderstorm_outlook([_hour(6, condition="sunny")], 1, NOW) is None


def test_thunderstorm_outlook_distinguishes_no_storm_from_no_data() -> None:
    rows = [_hour(0, condition="cloudy"), _hour(5, condition="lightning")]
    assert thunderstorm_outlook(rows, 1, NOW) is False
    assert thunderstorm_outlook(rows, 12, NOW) is True
    # CAPE alone (no derived condition) is still a decidable hour.
    assert thunderstorm_outlook([_hour(0, cape=10.0)], 1, NOW) is False


def test_window_rounds_up_to_whole_hourly_steps() -> None:
    """A "1 hour" window spans the in-progress hour plus the next one.

    NOW is 16:30, so the window runs 16:00-17:30 and the 17:00 stamp is in it,
    while 18:00 is not — an event up to ~2 h out can surface in a 1 h window.
    """
    rows = [_hour(0, gust=1.0), _hour(1, gust=2.0), _hour(2, gust=99.0)]
    assert max_gust(rows, 1, NOW) == (2.0, datetime(2026, 7, 15, 17, 0, tzinfo=UTC))


def test_rows_without_a_timestamp_are_skipped() -> None:
    """A malformed row must not crash the comparison."""
    rows = [{"time": None, "wind_gust_ms": 99.0}, _hour(0, gust=4.0)]
    assert max_gust(rows, 1, NOW) == (4.0, datetime(2026, 7, 15, 16, 0, tzinfo=UTC))


def test_hour_at_selects_the_in_progress_hour() -> None:
    """NOW is 16:30; the hour stamped 16:00 is the one covering it."""
    rows = [_hour(0, cape=100.0), _hour(1, cape=200.0)]
    selected = hour_at(rows, NOW)
    assert selected is not None
    assert selected["time"] == datetime(2026, 7, 15, 16, 0, tzinfo=UTC)
    assert selected["cape_jkg"] == 100.0


def test_hour_at_follows_the_clock() -> None:
    """Later in the day the same series yields the hour then in progress."""
    rows = [_hour(offset, cape=float(offset)) for offset in range(6)]
    selected = hour_at(rows, NOW + timedelta(hours=3))
    assert selected is not None
    assert selected["time"] == datetime(2026, 7, 15, 19, 0, tzinfo=UTC)
    assert selected["cape_jkg"] == 3.0


def test_hour_at_returns_none_without_a_match() -> None:
    """An empty series, one that aged out, and one not yet reached."""
    assert hour_at([], NOW) is None
    assert hour_at([_hour(-3), _hour(-2)], NOW) is None
    assert hour_at([_hour(2), _hour(3)], NOW) is None
