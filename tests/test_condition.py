"""Tests for the pure condition-derivation functions.

Table cases ported from ha-geosphere-next tests/test_condition.py.
"""

from __future__ import annotations

from datetime import UTC, datetime

import pytest

from geosphere_mcp_server.condition import (
    apparent_temperature,
    derive_condition,
    derive_current_condition,
    dew_point_from_t_rh,
    is_night,
    is_thunder,
    wind_from_components,
)
from geosphere_mcp_server.const import CAP_CIN_JKG, THUNDER_CAPE_JKG

# --- is_thunder ---


@pytest.mark.parametrize(
    ("cape", "cin", "expected"),
    [
        (None, None, False),
        (0.0, 0.0, False),
        # Below the CAPE threshold, inhibition is irrelevant.
        (THUNDER_CAPE_JKG - 1, 0.0, False),
        (THUNDER_CAPE_JKG, 0.0, True),
        # A missing cin counts as uncapped, so the gate cannot suppress it --
        # this is the Open-Meteo path, whose endpoint has no inhibition.
        (1500.0, None, True),
        # Weak inhibition passes, a strong lid suppresses.
        (1500.0, -10.0, True),
        (1500.0, -200.0, False),
        # The boundary itself is exclusive: exactly -CAP_CIN_JKG is capped.
        (1500.0, -CAP_CIN_JKG, False),
        (1500.0, -CAP_CIN_JKG + 0.1, True),
    ],
)
def test_is_thunder(cape, cin, expected) -> None:
    assert is_thunder(cape, cin) is expected


# --- derive_condition ---


@pytest.mark.parametrize(
    ("precip", "snow", "tcc", "cape", "cin", "gust", "night", "expected"),
    [
        # clear / cloud buckets
        (0.0, 0.0, 0.0, 0.0, 0.0, 0.0, False, "sunny"),
        (0.0, 0.0, 0.0, 0.0, 0.0, 0.0, True, "clear-night"),
        (0.0, 0.0, 12.5, None, None, None, False, "sunny"),
        (0.0, 0.0, 40.0, 0.0, 0.0, 0.0, False, "partlycloudy"),
        (0.0, 0.0, 62.5, 0.0, 0.0, 0.0, False, "partlycloudy"),
        (0.0, 0.0, 80.0, 0.0, 0.0, 0.0, True, "cloudy"),
        # precipitation
        (0.5, 0.0, 90.0, 0.0, 0.0, 0.0, False, "rainy"),
        (4.0, 0.0, 90.0, 0.0, 0.0, 0.0, False, "pouring"),
        (0.5, 0.0, 90.0, 1500.0, 0.0, 0.0, False, "lightning-rainy"),
        (0.5, 0.5, 90.0, 0.0, 0.0, 0.0, False, "snowy"),
        (1.0, 0.3, 90.0, 0.0, 0.0, 0.0, False, "snowy-rainy"),
        # dry thunder / wind
        (0.0, 0.0, 80.0, 1500.0, 0.0, 0.0, False, "lightning"),
        (0.0, 0.0, 30.0, 0.0, 0.0, 16.0, False, "windy"),
        (0.0, 0.0, 80.0, 0.0, 0.0, 16.0, False, "windy-variant"),
        # missing cloud data
        (0.0, 0.0, None, 0.0, 0.0, 0.0, False, None),
        # A strong cap demotes both thunder branches to their non-thunder peers.
        (0.5, 0.0, 90.0, 1500.0, -200.0, 0.0, False, "rainy"),
        (0.0, 0.0, 80.0, 1500.0, -200.0, 0.0, False, "cloudy"),
    ],
)
def test_derive_condition(precip, snow, tcc, cape, cin, gust, night, expected) -> None:
    assert derive_condition(precip, snow, tcc, cape, cin, gust, night) == expected


# --- derive_current_condition ---


def test_current_condition_pt_override_rain() -> None:
    """A precipitation-type code other than 255 forces a precipitation state."""
    assert (
        derive_current_condition(
            precipitation_type=1,
            precipitation_rate_mm_h=0.4,
            temperature=12.0,
            humidity=80.0,
            wind_speed=3.0,
            cloud_coverage=10.0,  # cloud says clear, pt wins
            cape=0.0,
            cin=0.0,
            gust_speed=5.0,
            night=False,
        )
        == "rainy"
    )


def test_current_condition_pt_override_snow_by_temperature() -> None:
    assert (
        derive_current_condition(
            precipitation_type=3,
            precipitation_rate_mm_h=1.0,
            temperature=-1.0,
            humidity=90.0,
            wind_speed=3.0,
            cloud_coverage=100.0,
            cape=0.0,
            cin=0.0,
            gust_speed=5.0,
            night=False,
        )
        == "snowy"
    )


def test_current_condition_thunder_ignores_the_forecast_cap() -> None:
    """An observed storm is not vetoed by AROME's inhibition for the hour.

    Precipitation here comes from INCA/the nowcast -- measurements -- while
    CAPE and CIN are forecast. Inhibition answers "can convection start?", and
    the observation has already settled that, so a strong modelled cap must
    not turn a thunderstorm in progress into plain `rainy`. The hourly path,
    forecast end to end, still applies the full gate.
    """
    assert (
        derive_current_condition(
            precipitation_type=1,
            precipitation_rate_mm_h=6.0,
            temperature=18.0,
            humidity=85.0,
            wind_speed=4.0,
            cloud_coverage=95.0,
            cape=1800.0,
            cin=-60.0,  # past CAP_CIN_JKG: capped, per the model
            gust_speed=8.0,
            night=False,
        )
        == "lightning-rainy"
    )


def test_current_condition_light_rain_does_not_override_the_cap() -> None:
    """Only convective-intensity rain overrides the modelled lid.

    Same capped, high-CAPE air as the test above, but the observation is
    drizzle off a frontal deck rather than a downpour. `precipitating` is true
    of any rate at all, so without the intensity qualifier this returned
    `lightning-rainy` -- and contradicted the hourly path, which keeps the full
    gate and calls the same hour `rainy`.
    """
    assert (
        derive_current_condition(
            precipitation_type=1,
            precipitation_rate_mm_h=0.4,  # below POURING_MM_PER_H
            temperature=18.0,
            humidity=85.0,
            wind_speed=4.0,
            cloud_coverage=95.0,
            cape=1800.0,
            cin=-150.0,  # strongly capped, per the model
            gust_speed=8.0,
            night=False,
        )
        == "rainy"
    )


def test_current_condition_light_rain_thunders_when_uncapped() -> None:
    """The intensity qualifier gates the *override*, not thunder itself.

    With weak inhibition the ordinary gate passes on CAPE alone, so light rain
    still reads as a thunderstorm -- the rate only matters when the model
    claims a lid.
    """
    assert (
        derive_current_condition(
            precipitation_type=1,
            precipitation_rate_mm_h=0.4,
            temperature=18.0,
            humidity=85.0,
            wind_speed=4.0,
            cloud_coverage=95.0,
            cape=1800.0,
            cin=-10.0,  # within CAP_CIN_JKG: effectively uncapped
            gust_speed=8.0,
            night=False,
        )
        == "lightning-rainy"
    )


def test_current_condition_without_precipitation_still_honours_the_cap() -> None:
    """With nothing observed, the verdict is forecast-only and the cap applies."""
    assert (
        derive_current_condition(
            precipitation_type=255,
            precipitation_rate_mm_h=0.0,
            temperature=25.0,
            humidity=40.0,
            wind_speed=3.0,
            cloud_coverage=95.0,
            cape=1800.0,
            cin=-60.0,
            gust_speed=5.0,
            night=False,
        )
        == "cloudy"
    )


def test_current_condition_no_precip_falls_through() -> None:
    assert (
        derive_current_condition(
            precipitation_type=255,
            precipitation_rate_mm_h=0.0,
            temperature=20.0,
            humidity=50.0,
            wind_speed=3.0,
            cloud_coverage=5.0,
            cape=0.0,
            cin=0.0,
            gust_speed=5.0,
            night=False,
        )
        == "sunny"
    )


def test_current_condition_fog() -> None:
    assert (
        derive_current_condition(
            precipitation_type=255,
            precipitation_rate_mm_h=0.0,
            temperature=2.0,
            humidity=99.0,
            wind_speed=0.5,
            cloud_coverage=100.0,
            cape=0.0,
            cin=0.0,
            gust_speed=1.0,
            night=False,
        )
        == "fog"
    )


def test_current_condition_rate_triggers_precip_without_pt() -> None:
    """A precipitation rate >= threshold precipitates even with pt=255."""
    assert (
        derive_current_condition(
            precipitation_type=255,
            precipitation_rate_mm_h=5.0,
            temperature=10.0,
            humidity=90.0,
            wind_speed=3.0,
            cloud_coverage=100.0,
            cape=0.0,
            cin=0.0,
            gust_speed=5.0,
            night=False,
        )
        == "pouring"
    )


# --- wind_from_components ---


def test_wind_from_components() -> None:
    speed, bearing = wind_from_components(0.0, -5.0)
    assert speed == 5.0
    assert bearing == 0.0  # wind FROM the north blows toward -v
    speed, bearing = wind_from_components(-5.0, 0.0)
    assert bearing == 90.0  # from the east
    assert wind_from_components(None, 1.0) == (None, None)


# --- is_night ---


def test_is_night_vienna() -> None:
    noon = datetime(2026, 7, 15, 12, 0, tzinfo=UTC)
    midnight = datetime(2026, 7, 15, 0, 0, tzinfo=UTC)
    assert not is_night(48.22, 16.37, noon)
    assert is_night(48.22, 16.37, midnight)


# --- apparent_temperature ---


def test_apparent_temperature() -> None:
    assert apparent_temperature(30.0, 50.0, 2.0) == pytest.approx(31.6, abs=0.3)
    assert apparent_temperature(None, 50.0, 2.0) is None


# --- dew_point_from_t_rh ---


def test_dew_point_from_t_rh() -> None:
    assert dew_point_from_t_rh(20.0, 50.0) == pytest.approx(9.3)
    # Saturated air: dew point equals the temperature.
    assert dew_point_from_t_rh(20.0, 100.0) == pytest.approx(20.0)
    # RH above 100 (model artifact) clamps instead of exceeding T.
    assert dew_point_from_t_rh(20.0, 104.0) == pytest.approx(20.0)
    assert dew_point_from_t_rh(-5.0, 80.0) == pytest.approx(-7.9, abs=0.1)
    assert dew_point_from_t_rh(None, 50.0) is None
    assert dew_point_from_t_rh(20.0, None) is None
    assert dew_point_from_t_rh(20.0, 0.0) is None
