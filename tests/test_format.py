"""Tests for the markdown renderers + normalizers in format.py."""

from __future__ import annotations

from datetime import UTC, datetime, timedelta

import pytest

from geosphere_mcp_server.format import (
    normalize_air_quality_geosphere,
    normalize_current_geosphere,
    normalize_hourly_geosphere,
    normalize_outlook_geosphere,
    render_air_quality,
    render_current,
    render_hourly,
    render_outlook,
)

LAT, LON = 48.2208, 16.3738

# GeoSphere-path current dict (as produced by weather.async_fetch_current_conditions).
SAMPLE_CURRENT_GEOSPHERE = {
    "observed_at": datetime(2026, 7, 15, 12, 30, tzinfo=UTC),  # 14:30 CEST
    "temperature_c": 21.3,
    "apparent_temperature_c": 20.1,
    "humidity_pct": 55.0,
    "pressure_hpa": 1013.0,
    "wind_speed_ms": 2.7,
    "wind_bearing_deg": 240.0,
    "wind_gust_ms": 6.0,
    "precipitation_1h_mm": 0.0,
    "precipitation_1h_at": datetime(2026, 7, 15, 12, 0, tzinfo=UTC),  # 14:00 CEST
    "precipitation_type": 255,
    "precipitation_kind": "no_precipitation",
    "cloud_cover_pct": 40.0,
    "condition": "partlycloudy",
    "sources": ["INCA", "nowcast", "AROME"],
}

# GeoSphere-path assembled hourly (as produced by weather.assemble_hourly_forecast).
SAMPLE_HOURLY_GEOSPHERE = {
    "reference_time": datetime(2026, 7, 15, 10, 0, tzinfo=UTC),  # 12:00 CEST
    "sources": ["AROME", "C-LAEF ensemble"],
    "hourly": [
        {
            "time": datetime(2026, 7, 15, 12, 0, tzinfo=UTC),  # 14:00 CEST
            "condition": "rainy",
            "temperature_c": 21.3,
            "wind_speed_ms": 3.1,
            "precipitation_mm": 1.2,
            "precipitation_probability_pct": 70,
            "snow_limit_m": 2986.4,
        },
        {
            "time": datetime(2026, 7, 15, 13, 0, tzinfo=UTC),  # 15:00 CEST
            "condition": "cloudy",
            "temperature_c": 20.1,
            "wind_speed_ms": 2.4,
            "precipitation_mm": 0.0,
            "precipitation_probability_pct": 0,
        },
    ],
}

NOW = datetime(2026, 7, 15, 12, 0, tzinfo=UTC)  # 13:00 Lisbon local


# --- render_current (GeoSphere path) ---


def test_render_current_dates_precipitation_by_its_own_stamp() -> None:
    """A stale `RR` must not borrow a current observation time.

    `inca_latest` scans each parameter back independently, so a slice with a
    fresh `T2M` and an hours-old `RR` pairs the two. Naming the hour the total
    covers keeps the source line's time from speaking for it.
    """
    data = normalize_current_geosphere(
        SAMPLE_CURRENT_GEOSPHERE
        | {
            "precipitation_1h_mm": 5.0,
            "precipitation_1h_at": datetime(2026, 7, 15, 9, 0, tzinfo=UTC),
        },
        LAT,
        LON,
    )
    out = render_current(data)
    assert "🌧️ Precipitation (hour to 11:00): 5 mm" in out
    assert "— observed 14:30" in out


def test_render_current_precipitation_without_a_stamp() -> None:
    """No stamp falls back to the undated wording rather than dropping the value."""
    data = normalize_current_geosphere(
        SAMPLE_CURRENT_GEOSPHERE | {"precipitation_1h_at": None}, LAT, LON
    )
    assert "🌧️ Precipitation (last hour): 0 mm" in render_current(data)


def test_render_current_geosphere() -> None:
    data = normalize_current_geosphere(SAMPLE_CURRENT_GEOSPHERE, LAT, LON)
    out = render_current(data)
    assert out.splitlines()[0] == "# Current Weather at 48.2208, 16.3738"
    assert "🌡️ Temperature: 21.3°C (feels like 20.1°C)" in out
    assert "🌤️ Condition: partlycloudy" in out
    assert "💧 Humidity: 55%" in out
    assert "💨 Wind: 2.7 m/s from 240° (gusts 6 m/s)" in out
    # Dated by the `RR` stamp, not by the source line's 14:30 observation time.
    assert "🌧️ Precipitation (hour to 14:00): 0 mm" in out
    assert "📊 Pressure: 1013 hPa" in out
    assert "☁️ Cloud cover: 40%" in out
    # Sunrise/sunset are omitted on the GeoSphere path.
    assert "Sunrise" not in out
    assert "Sunset" not in out
    # Vienna local time (CEST, UTC+2): observed 12:30 UTC -> 14:30 local.
    assert "🕐 Timezone: Europe/Vienna (CEST)" in out
    assert "📡 Source: GeoSphere (INCA + nowcast + AROME) — observed 14:30" in out


@pytest.mark.parametrize(
    ("kind", "line"),
    [
        ("rain", "🌧️ Falling now: rain"),
        ("freezing_rain", "🧊 Falling now: freezing rain"),
        ("snow", "🌨️ Falling now: snow"),
        ("rain_and_snow", "🌨️ Falling now: rain and snow"),
    ],
)
def test_render_current_names_what_is_falling(kind: str, line: str) -> None:
    current = {**SAMPLE_CURRENT_GEOSPHERE, "precipitation_kind": kind}
    assert line in render_current(normalize_current_geosphere(current, LAT, LON))


@pytest.mark.parametrize("kind", ["no_precipitation", None])
def test_render_current_omits_the_falling_line_without_a_kind(
    kind: str | None,
) -> None:
    """Dry, or a kind nothing could name: no line, like a zero precipitation."""
    current = {**SAMPLE_CURRENT_GEOSPHERE, "precipitation_kind": kind}
    out = render_current(normalize_current_geosphere(current, LAT, LON))
    assert "Falling now" not in out


def test_render_current_geosphere_omits_none_fields() -> None:
    sparse = {
        "observed_at": datetime(2026, 7, 15, 12, 30, tzinfo=UTC),
        "temperature_c": 10.0,
        "apparent_temperature_c": None,
        "humidity_pct": None,
        "pressure_hpa": None,
        "wind_speed_ms": None,
        "wind_bearing_deg": None,
        "wind_gust_ms": None,
        "precipitation_1h_mm": None,
        "cloud_cover_pct": None,
        "condition": None,
        "sources": ["AROME"],
    }
    out = render_current(normalize_current_geosphere(sparse, LAT, LON))
    assert "🌡️ Temperature: 10.0°C" in out
    assert "feels like" not in out
    assert "Humidity" not in out
    assert "Pressure" not in out
    assert "💨 Wind" not in out
    assert "Cloud cover" not in out
    assert "Condition" not in out
    assert "📡 Source: GeoSphere (AROME)" in out


# --- render_hourly (GeoSphere path) ---


def test_render_hourly_geosphere() -> None:
    data = normalize_hourly_geosphere(SAMPLE_HOURLY_GEOSPHERE, LAT, LON, 24)
    out = render_hourly(data)
    assert out.splitlines()[0] == "# 24-Hour Forecast for 48.2208, 16.3738"
    assert "AROME model, reference 2026-07-15 12:00 CEST" in out
    assert "Source: GeoSphere (AROME + C-LAEF ensemble)" in out
    # A day-divider header precedes the hours, which are indented beneath it.
    assert "Wed 2026-07-15" in out
    # 14:00 CEST line with precip + probability + wind + snow limit (indented
    # under the day).
    assert (
        "  14:00: 21.3°C — rainy, 1.2 mm (70% chance), wind 3 m/s, snow limit 2986 m"
    ) in out
    # 15:00 CEST line: zero precip/probability and the absent snow limit
    # omitted, wind kept.
    assert "  15:00: 20.1°C — cloudy, wind 2 m/s" in out.splitlines()


def test_render_hourly_day_divider_on_rollover() -> None:
    """A new day-divider header is emitted each time the local date changes."""
    sample = {
        **SAMPLE_HOURLY_GEOSPHERE,
        "hourly": [
            {
                "time": datetime(2026, 7, 15, 21, 0, tzinfo=UTC),  # 23:00 CEST
                "condition": "clear-night",
                "temperature_c": 18.0,
                "wind_speed_ms": 2.0,
            },
            {
                "time": datetime(2026, 7, 15, 22, 0, tzinfo=UTC),  # 00:00 CEST +1
                "condition": "clear-night",
                "temperature_c": 17.0,
                "wind_speed_ms": 2.0,
            },
        ],
    }
    data = normalize_hourly_geosphere(sample, LAT, LON, 24)
    lines = render_hourly(data).splitlines()
    # Both local days appear as headers, each followed by its indented hour.
    assert "Wed 2026-07-15" in lines
    assert "Thu 2026-07-16" in lines
    assert lines.index("Wed 2026-07-15") < lines.index("Thu 2026-07-16")
    assert "  23:00: 18.0°C — clear-night, wind 2 m/s" in lines
    assert "  00:00: 17.0°C — clear-night, wind 2 m/s" in lines


def test_render_hourly_geosphere_horizon_note() -> None:
    """Requesting more hours than AROME returns appends the horizon note."""
    data = normalize_hourly_geosphere(SAMPLE_HOURLY_GEOSPHERE, LAT, LON, 48)
    out = render_hourly(data)
    assert "This server publishes nothing beyond it" in out
    assert "AROME forecast horizon ends 2026-07-15 15:00" in out


def test_render_hourly_geosphere_empty_window() -> None:
    """A `start` past the AROME horizon leaves nothing to render.

    Reachable on the only path there is, so the branch needs its own case: the
    hourly tool clamps `hours` but not `start`, and a series filtered down to
    nothing must say so rather than render an empty forecast body.
    """
    data = normalize_hourly_geosphere(
        {**SAMPLE_HOURLY_GEOSPHERE, "hourly": []}, LAT, LON, 24
    )
    out = render_hourly(data)
    assert "No forecast hours available for the requested window." in out
    # No horizon note either: nothing came back to name an end for.
    assert "horizon" not in out


def test_render_hourly_geosphere_no_note_when_satisfied() -> None:
    data = normalize_hourly_geosphere(SAMPLE_HOURLY_GEOSPHERE, LAT, LON, 2)
    out = render_hourly(data)
    assert "horizon" not in out


def test_render_hourly_labels_the_chance_as_an_estimate() -> None:
    """GeoSphere publishes no probability; the chance shown is derived here."""
    out = render_hourly(
        normalize_hourly_geosphere(SAMPLE_HOURLY_GEOSPHERE, LAT, LON, 2)
    )
    assert out.splitlines()[-1] == (
        "Precipitation chance is an estimate derived from the C-LAEF ensemble "
        "percentiles (0/30/70/95 %), not a probability GeoSphere publishes."
    )


def test_render_hourly_omits_the_estimate_note_without_a_chance() -> None:
    dry = {
        **SAMPLE_HOURLY_GEOSPHERE,
        "hourly": SAMPLE_HOURLY_GEOSPHERE["hourly"][1:],
    }
    out = render_hourly(normalize_hourly_geosphere(dry, LAT, LON, 1))
    assert "estimate" not in out


def test_render_hourly_treats_a_trace_as_dry() -> None:
    """A trace that rounds to 0 mm shows neither an amount nor a chance."""
    trace = {
        **SAMPLE_HOURLY_GEOSPHERE,
        "hourly": [
            {
                "time": datetime(2026, 7, 15, 12, 0, tzinfo=UTC),
                "condition": "cloudy",
                "temperature_c": 12.0,
                "wind_speed_ms": 1.0,
                "precipitation_mm": 0.04,
                "precipitation_probability_pct": 30,
            }
        ],
    }
    out = render_hourly(normalize_hourly_geosphere(trace, LAT, LON, 1))
    assert "  14:00: 12.0°C — cloudy, wind 1 m/s" in out
    assert "estimate" not in out


# --- Storm outlook ---

NOW_OUTLOOK = datetime(2026, 7, 15, 14, 30, tzinfo=UTC)  # 16:30 CEST
CURRENT_ENSEMBLE_RUN = datetime(2026, 7, 15, 6, 0, tzinfo=UTC)


def _geosphere_hour(offset_hours: int, **fields) -> dict:
    """One assembled GeoSphere hour, stamped in UTC like weather.py emits."""
    return {
        "time": datetime(2026, 7, 15, 14, 0, tzinfo=UTC)
        + timedelta(hours=offset_hours),
        "weather_symbol": fields.get("symbol"),
        "wind_gust_ms": fields.get("gust"),
        "cape_jkg": fields.get("cape"),
        "ensemble_cape_p90_jkg": fields.get("cape_p90"),
        "ensemble_precipitation_p90_mm": fields.get("precipitation_p90"),
    }


SAMPLE_OUTLOOK_ASSEMBLED = {
    "reference_time": datetime(2026, 7, 15, 12, 0, tzinfo=UTC),
    "ensemble_reference_time": CURRENT_ENSEMBLE_RUN,
    "sources": ["AROME", "C-LAEF ensemble"],
    "hourly": [
        _geosphere_hour(0, symbol=3, gust=8.0, cape=200.0),
        _geosphere_hour(1, symbol=5, gust=12.0, cape=400.0),
        _geosphere_hour(4, symbol=26, gust=24.0, cape=1800.0),
    ],
}


def test_normalize_outlook_geosphere_localizes_only_the_output() -> None:
    """Derivation runs on UTC rows; reported stamps come back in Vienna time."""
    data = normalize_outlook_geosphere(
        SAMPLE_OUTLOOK_ASSEMBLED, LAT, LON, now=NOW_OUTLOOK
    )
    assert data["max_gust_short_ms"] == 12.0
    assert data["max_gust_short_at"].hour == 17  # 15:00Z -> 17:00 CEST
    assert data["max_gust_long_ms"] == 24.0
    assert data["thunderstorm_short"] is False
    assert data["thunderstorm_short_source"] is None
    assert data["next_thunderstorm_at"].hour == 20  # 18:00Z -> 20:00 CEST
    assert data["next_thunderstorm_source"] == "symbol"
    assert data["ensemble_current"] is True
    assert data["max_cape_long_jkg"] == 1800.0


def test_render_outlook_geosphere() -> None:
    out = render_outlook(
        normalize_outlook_geosphere(SAMPLE_OUTLOOK_ASSEMBLED, LAT, LON, now=NOW_OUTLOOK)
    )
    assert out.splitlines()[0] == "# Storm Outlook for 48.2208, 16.3738"
    assert "Source: GeoSphere (AROME + C-LAEF ensemble)" in out
    assert "💨 Max gust next 1 h: 12 m/s (at Wed 2026-07-15 17:00)" in out
    assert "💨 Max gust next 12 h: 24 m/s (at Wed 2026-07-15 20:00)" in out
    assert "⛈️ Thunderstorm expected next 1 h: no" in out
    assert "⚡ Next thunderstorm: Wed 2026-07-15 20:00 (signal: symbol)" in out
    assert "🌡️ Max CAPE next 12 h: 1800 J/kg" in out
    assert "Ensemble signal" not in out
    assert out.splitlines()[-1] == (
        "A thunderstorm hour is one whose GeoSphere weather symbol is a "
        "thunderstorm, or where the C-LAEF ensemble's 90th percentiles reach "
        "1000 J/kg CAPE and 0.1 mm precipitation; `signal` names which fired "
        "(symbol, ensemble or both). Max CAPE is a diagnostic and decides nothing."
    )


def test_render_outlook_names_the_signal_of_an_expected_storm() -> None:
    assembled = {
        **SAMPLE_OUTLOOK_ASSEMBLED,
        "hourly": [
            _geosphere_hour(0, symbol=5),
            _geosphere_hour(1, symbol=27, cape_p90=1500.0, precipitation_p90=2.0),
        ],
    }
    out = render_outlook(
        normalize_outlook_geosphere(assembled, LAT, LON, now=NOW_OUTLOOK)
    )
    assert "⛈️ Thunderstorm expected next 1 h: yes (signal: both)" in out
    assert "⚡ Next thunderstorm: Wed 2026-07-15 17:00 (signal: both)" in out


def test_render_outlook_says_when_the_ensemble_could_not_vote() -> None:
    """A run older than 12 h, or none at all, leaves the symbol alone."""
    for run in (datetime(2026, 7, 15, 2, 0, tzinfo=UTC), None):
        assembled = {
            **SAMPLE_OUTLOOK_ASSEMBLED,
            "ensemble_reference_time": run,
            "hourly": [
                _geosphere_hour(0, symbol=5, cape_p90=1500.0, precipitation_p90=2.0)
            ],
        }
        out = render_outlook(
            normalize_outlook_geosphere(assembled, LAT, LON, now=NOW_OUTLOOK)
        )
        assert "⛈️ Thunderstorm expected next 1 h: no" in out
        assert (
            "ℹ️ Ensemble signal: unavailable (the C-LAEF request failed or its "
            "run is 12 h old or more); thunderstorms from the weather symbol only"
        ) in out


def test_render_outlook_reports_unknowns_distinctly() -> None:
    """No gust data reads unknown; an hour with no symbol is no storm."""
    data = normalize_outlook_geosphere(
        {"sources": ["AROME"], "hourly": [_geosphere_hour(0)]},
        LAT,
        LON,
        now=NOW_OUTLOOK,
    )
    out = render_outlook(data)
    assert "💨 Max gust next 1 h: unknown" in out
    assert "⛈️ Thunderstorm expected next 1 h: no" in out
    assert "⚡ Next thunderstorm: none in the forecast horizon" in out


def test_render_outlook_is_unknown_only_without_an_hour_in_the_window() -> None:
    data = normalize_outlook_geosphere(
        {"sources": ["AROME"], "hourly": [_geosphere_hour(6, symbol=5)]},
        LAT,
        LON,
        now=NOW_OUTLOOK,
    )
    out = render_outlook(data)
    assert (
        "⛈️ Thunderstorm expected next 1 h: unknown (no forecast hours in the window)"
    ) in out


def test_render_outlook_names_the_horizon_an_all_clear_covers() -> None:
    """ "No storm" over 4 h and over 60 h are different claims.

    The forecast nominally runs ~60 h, but publication lag and a stale or
    truncated run hand over fewer hours, so the span has to be stated rather
    than implied.
    """
    data = normalize_outlook_geosphere(
        {
            "sources": ["AROME"],
            "hourly": [
                _geosphere_hour(0, symbol=5, cape=100.0),
                _geosphere_hour(40, symbol=5, cape=100.0),
            ],
        },
        LAT,
        LON,
        now=NOW_OUTLOOK,
    )
    # NOW_OUTLOOK is 14:30 and the series ends at 06:00 the next day: 39.5 h.
    assert data["scanned_horizon_hours"] == 40
    assert "⚡ Next thunderstorm: none in the next 40 h" in render_outlook(data)


def test_render_outlook_without_any_hours() -> None:
    out = render_outlook(
        normalize_outlook_geosphere({"hourly": []}, LAT, LON, now=NOW_OUTLOOK)
    )
    assert "No forecast hours available for the outlook window." in out


# --- Air quality ---

SAMPLE_AIR_QUALITY_GEOSPHERE = {
    "observed_at": datetime(2026, 7, 15, 14, 0, tzinfo=UTC),  # 16:00 CEST
    "pollutants": {
        "nitrogen_dioxide": 18.4,
        "ozone": 92.0,
        "pm10": 21.2,
        "pm2_5": 12.0,
    },
    "aqi_band_today": 2,
    "aqi_band_tomorrow": 3,
    "aqi_band_in_2_days": None,
    "sources": ["WRF-Chem", "daily AQI"],
}


def test_render_air_quality_geosphere() -> None:
    data = normalize_air_quality_geosphere(SAMPLE_AIR_QUALITY_GEOSPHERE, LAT, LON)
    out = render_air_quality(data)
    assert out.splitlines()[0] == "# Air Quality at 48.2208, 16.3738"
    # GeoSphere publishes the band, so no numeric index is appended, and the
    # unknown third day is omitted rather than rendered as a gap.
    assert "🏷️ European AQI: 2 (fair) today · 3 (moderate) tomorrow" in out
    assert "in 2 days" not in out
    assert "🌫️ Concentrations (16:00): NO₂ 18 µg/m³ · O₃ 92 µg/m³ · PM10 21 µg/m³" in out
    assert "Source: GeoSphere (WRF-Chem + daily AQI, 3 km)" in out


def test_render_air_quality_without_any_data_still_names_the_source() -> None:
    """Which source drew the blank decides whether asking elsewhere is worth it."""
    data = normalize_air_quality_geosphere(
        {"pollutants": {}, "sources": ["WRF-Chem"]}, LAT, LON
    )
    out = render_air_quality(data)
    assert "No air-quality data available" in out
    assert "📡 Source: GeoSphere (WRF-Chem, 3 km)" in out
    # The band legend explains figures that are not there — leave it out.
    assert "EEA" not in out
