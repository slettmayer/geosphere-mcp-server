"""Tests for the markdown renderers + normalizers in format.py."""

from __future__ import annotations

from datetime import UTC, datetime

from geosphere_mcp_server.format import (
    normalize_current_geosphere,
    normalize_current_openmeteo,
    normalize_daily_openmeteo,
    normalize_hourly_geosphere,
    normalize_hourly_openmeteo,
    render_current,
    render_daily,
    render_hourly,
)

LAT, LON = 48.2208, 16.3738

# GeoSphere-path current dict (as produced by weather.async_fetch_current_conditions).
SAMPLE_CURRENT_GEOSPHERE = {
    "observed_at": datetime(2026, 7, 15, 12, 30, tzinfo=UTC),  # 14:30 CEST
    "temperature_c": 21.3,
    "apparent_temperature_c": 20.1,
    "dew_point_c": 12.0,
    "humidity_pct": 55.0,
    "pressure_hpa": 1013.0,
    "wind_speed_ms": 2.7,
    "wind_bearing_deg": 240.0,
    "wind_gust_ms": 6.0,
    "precipitation_1h_mm": 0.0,
    "precipitation_type": 255,
    "is_precipitating": False,
    "cloud_cover_pct": 40.0,
    "condition": "partlycloudy",
    "sources": ["INCA", "nowcast", "AROME"],
    "grid_latitude": 48.219,
    "grid_longitude": 16.362,
}

# Raw Open-Meteo current body (timezone=auto, wind in m/s).
SAMPLE_CURRENT_OPENMETEO = {
    "timezone": "Europe/Lisbon",
    "timezone_abbreviation": "WEST",
    "utc_offset_seconds": 3600,
    "current": {
        "time": "2026-07-15T15:00",
        "temperature_2m": 24.6,
        "apparent_temperature": 25.0,
        "relative_humidity_2m": 60,
        "dew_point_2m": 16.0,
        "precipitation": 0.0,
        "weather_code": 2,
        "cloud_cover": 35,
        "pressure_msl": 1015.0,
        "wind_speed_10m": 3.2,
        "wind_direction_10m": 300,
        "wind_gusts_10m": 7.0,
        "is_day": 1,
    },
    "daily": {
        "time": ["2026-07-15"],
        "sunrise": ["2026-07-15T06:20"],
        "sunset": ["2026-07-15T21:05"],
    },
}

# GeoSphere-path assembled hourly (as produced by weather.assemble_hourly_forecast).
SAMPLE_HOURLY_GEOSPHERE = {
    "reference_time": datetime(2026, 7, 15, 10, 0, tzinfo=UTC),  # 12:00 CEST
    "grid_latitude": 48.219,
    "grid_longitude": 16.362,
    "sources": ["AROME", "C-LAEF ensemble"],
    "hourly": [
        {
            "time": datetime(2026, 7, 15, 12, 0, tzinfo=UTC),  # 14:00 CEST
            "condition": "rainy",
            "temperature_c": 21.3,
            "wind_speed_ms": 3.1,
            "precipitation_mm": 1.2,
            "precipitation_probability_pct": 70,
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

# Raw Open-Meteo hourly body.
SAMPLE_HOURLY_OPENMETEO = {
    "timezone": "Europe/Lisbon",
    "timezone_abbreviation": "WEST",
    "utc_offset_seconds": 3600,
    "hourly": {
        "time": [
            "2026-07-15T13:00",
            "2026-07-15T14:00",
            "2026-07-15T15:00",
            "2026-07-15T16:00",
        ],
        "temperature_2m": [19.0, 20.0, 21.0, 22.0],
        "weather_code": [61, 3, 2, 0],
        "precipitation": [0.5, 0.0, 0.0, 0.0],
        "precipitation_probability": [80, 20, 0, 0],
        "wind_speed_10m": [4.0, 3.0, 2.0, 1.0],
    },
}

# Raw Open-Meteo daily body.
SAMPLE_DAILY_OPENMETEO = {
    "timezone": "Europe/Vienna",
    "timezone_abbreviation": "CEST",
    "utc_offset_seconds": 7200,
    "daily": {
        "time": ["2026-07-25", "2026-07-26"],
        "weather_code": [2, 61],
        "temperature_2m_min": [18.0, 16.0],
        "temperature_2m_max": [27.0, 22.0],
        "precipitation_sum": [2.1, 0.0],
        "precipitation_probability_max": [40, 10],
        "wind_speed_10m_max": [6.0, 5.0],
        "wind_gusts_10m_max": [8.0, 12.0],
    },
}

NOW = datetime(2026, 7, 15, 12, 0, tzinfo=UTC)  # 13:00 Lisbon local


# --- render_current (GeoSphere path) ---


def test_render_current_geosphere() -> None:
    data = normalize_current_geosphere(SAMPLE_CURRENT_GEOSPHERE, LAT, LON)
    out = render_current(data)
    assert out.splitlines()[0] == "# Current Weather at 48.2208, 16.3738"
    assert "🌡️ Temperature: 21.3°C (feels like 20.1°C)" in out
    assert "🌤️ Condition: partlycloudy" in out
    assert "💧 Humidity: 55%" in out
    assert "💨 Wind: 2.7 m/s from 240° (gusts 6 m/s)" in out
    assert "🌧️ Precipitation (last hour): 0 mm" in out
    assert "📊 Pressure: 1013 hPa" in out
    assert "☁️ Cloud cover: 40%" in out
    # Sunrise/sunset are omitted on the GeoSphere path.
    assert "Sunrise" not in out
    assert "Sunset" not in out
    # Vienna local time (CEST, UTC+2): observed 12:30 UTC -> 14:30 local.
    assert "🕐 Timezone: Europe/Vienna (CEST)" in out
    assert "📡 Source: GeoSphere (INCA + nowcast + AROME) — observed 14:30" in out


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


# --- render_current (Open-Meteo path) ---


def test_render_current_openmeteo() -> None:
    data = normalize_current_openmeteo(SAMPLE_CURRENT_OPENMETEO, 38.7, -9.1)
    out = render_current(data)
    assert out.splitlines()[0] == "# Current Weather at 38.7, -9.1"
    assert "🌡️ Temperature: 24.6°C (feels like 25.0°C)" in out
    assert "🌤️ Condition: partlycloudy" in out  # WMO code 2, day
    assert "💨 Wind: 3.2 m/s from 300° (gusts 7 m/s)" in out
    assert "🌅 Sunrise: 06:20" in out
    assert "🌇 Sunset: 21:05" in out
    assert "🕐 Timezone: Europe/Lisbon (WEST)" in out
    assert "📡 Source: Open-Meteo — observed 15:00" in out


def test_render_current_openmeteo_night_condition() -> None:
    body = {
        **SAMPLE_CURRENT_OPENMETEO,
        "current": {
            **SAMPLE_CURRENT_OPENMETEO["current"],
            "weather_code": 0,
            "is_day": 0,
        },
    }
    out = render_current(normalize_current_openmeteo(body, 38.7, -9.1))
    assert "🌤️ Condition: clear-night" in out


# --- render_hourly (GeoSphere path) ---


def test_render_hourly_geosphere() -> None:
    data = normalize_hourly_geosphere(SAMPLE_HOURLY_GEOSPHERE, LAT, LON, 24)
    out = render_hourly(data)
    assert out.splitlines()[0] == "# 24-Hour Forecast for 48.2208, 16.3738"
    assert "AROME model, reference 2026-07-15 12:00 CEST" in out
    assert "Source: GeoSphere (AROME + C-LAEF ensemble)" in out
    # 14:00 CEST line with precip + probability + wind.
    assert "14:00: 21.3°C — rainy, 1.2 mm (70% chance), wind 3 m/s" in out
    # 15:00 CEST line: zero precip/probability omitted, wind kept.
    assert "15:00: 20.1°C — cloudy, wind 2 m/s" in out


def test_render_hourly_geosphere_horizon_note() -> None:
    """Requesting more hours than AROME returns appends the horizon note."""
    data = normalize_hourly_geosphere(SAMPLE_HOURLY_GEOSPHERE, LAT, LON, 48)
    out = render_hourly(data)
    assert "get_daily_forecast for days further ahead" in out
    assert "AROME forecast horizon ends 2026-07-15 15:00" in out


def test_render_hourly_geosphere_no_note_when_satisfied() -> None:
    data = normalize_hourly_geosphere(SAMPLE_HOURLY_GEOSPHERE, LAT, LON, 2)
    out = render_hourly(data)
    assert "horizon" not in out


# --- render_hourly (Open-Meteo path) ---


def test_render_hourly_openmeteo_filters_to_now() -> None:
    # Lisbon local now = 13:00, cutoff drops the 13:00 past-hour boundary kept,
    # so entries start at 13:00 and onward.
    data = normalize_hourly_openmeteo(SAMPLE_HOURLY_OPENMETEO, 38.7, -9.1, 48, now=NOW)
    out = render_hourly(data)
    assert out.splitlines()[0] == "# 48-Hour Forecast for 38.7, -9.1"
    assert "Open-Meteo model · Source: Open-Meteo" in out
    assert "13:00: 19.0°C — rainy, 0.5 mm (80% chance), wind 4 m/s" in out
    assert "16:00: 22.0°C — sunny, wind 1 m/s" in out


def test_render_hourly_openmeteo_respects_start_and_hours() -> None:
    start = datetime(2026, 7, 15, 14, 0, tzinfo=UTC)  # 15:00 Lisbon local
    data = normalize_hourly_openmeteo(
        SAMPLE_HOURLY_OPENMETEO, 38.7, -9.1, 1, now=NOW, start=start
    )
    times = [h["time"].strftime("%H:%M") for h in data["hours"]]
    assert times == ["15:00"]  # start cutoff + hours=1 truncation


def test_render_hourly_empty_window() -> None:
    data = normalize_hourly_openmeteo(
        {"hourly": {"time": [], "temperature_2m": []}}, 38.7, -9.1, 24, now=NOW
    )
    out = render_hourly(data)
    assert "No forecast hours available" in out


# --- render_daily ---


def test_render_daily() -> None:
    data = normalize_daily_openmeteo(SAMPLE_DAILY_OPENMETEO, LAT, LON, 7)
    out = render_daily(data)
    assert out.splitlines()[0] == "# 7-Day Forecast for 48.2208, 16.3738"
    assert "Source: Open-Meteo (Europe/Vienna)" in out
    # Wind "up to" prefers gusts.
    assert (
        "Sat 2026-07-25: 18–27°C — partlycloudy, 2.1 mm (40% chance), "
        "wind up to 8 m/s" in out
    )
    # Zero precip omitted.
    assert "Sun 2026-07-26: 16–22°C — rainy, wind up to 12 m/s" in out


def test_render_daily_empty() -> None:
    out = render_daily(normalize_daily_openmeteo({"daily": {"time": []}}, LAT, LON, 5))
    assert "No daily forecast available" in out
