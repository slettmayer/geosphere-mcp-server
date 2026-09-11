"""Markdown renderers for the four weather tools.

Pure functions, no I/O. Each tool has exactly one renderer, fed by a
normalization step that folds the dicts :mod:`weather` and :mod:`air_quality`
produce into the uniform shape the renderer reads.

Units are metric. Timestamps arrive UTC and are rendered in the Alpine local
time (``Europe/Vienna`` — GeoSphere only covers Austria and the Alps, which
share the CET/CEST zone). Condition strings are the Home Assistant vocabulary
and are emitted verbatim (e.g. ``partlycloudy``).
"""

from __future__ import annotations

from collections.abc import Callable
from datetime import UTC, date, datetime
from typing import Any
from zoneinfo import ZoneInfo

from geosphere_mcp_server.const import (
    AIR_QUALITY_POLLUTANTS,
    AROME_MAX_HOURS,
    GEOSPHERE_TZ,
    OUTLOOK_LONG_HORIZON_HOURS,
    OUTLOOK_SHORT_HORIZON_HOURS,
    aqi_label,
)
from geosphere_mcp_server.outlook import (
    horizon_hours,
    max_cape,
    max_gust,
    scan_thunderstorm,
    thunderstorm_outlook,
    window,
)

# --- Number / value formatting helpers ---


def _coords(latitude: float, longitude: float) -> str:
    """Render a coordinate pair compactly (trailing zeros trimmed)."""
    return f"{latitude:g}, {longitude:g}"


def _temp(value: float | None) -> str | None:
    """One-decimal temperature, e.g. ``21.3``."""
    return None if value is None else f"{value:.1f}"


def _round_int(value: float | None) -> int | None:
    """Round to a whole number, keeping ``None``."""
    return None if value is None else round(value)


def _mm(value: float | None) -> str | None:
    """Precipitation amount: integer when whole, else one decimal."""
    if value is None:
        return None
    rounded = round(value, 1)
    if rounded == int(rounded):
        return str(int(rounded))
    return f"{rounded:.1f}"


def _hm(when: datetime | None) -> str | None:
    """Local ``HH:MM`` for a (already localized) datetime."""
    return None if when is None else when.strftime("%H:%M")


def _tz_line(tz_id: str | None, tz_abbr: str | None) -> str | None:
    """Render the ``🕐 Timezone`` line, or None when unknown."""
    if not tz_id and not tz_abbr:
        return None
    if tz_id and tz_abbr:
        return f"🕐 Timezone: {tz_id} ({tz_abbr})"
    return f"🕐 Timezone: {tz_id or tz_abbr}"


# --- Current conditions ---


def normalize_current_geosphere(
    current: dict[str, Any], latitude: float, longitude: float
) -> dict[str, Any]:
    """Fold a :func:`weather.async_fetch_current_conditions` dict into the
    uniform current shape, localizing the UTC observation time to Vienna."""
    tz = ZoneInfo(GEOSPHERE_TZ)
    observed = current.get("observed_at")
    observed_local = observed.astimezone(tz) if observed is not None else None
    precip_at = current.get("precipitation_1h_at")
    sources = current.get("sources") or []
    source = f"GeoSphere ({' + '.join(sources)})" if sources else "GeoSphere"
    return {
        "latitude": latitude,
        "longitude": longitude,
        "temperature_c": current.get("temperature_c"),
        "apparent_temperature_c": current.get("apparent_temperature_c"),
        "condition": current.get("condition"),
        "humidity_pct": current.get("humidity_pct"),
        "wind_speed_ms": current.get("wind_speed_ms"),
        "wind_bearing_deg": current.get("wind_bearing_deg"),
        "wind_gust_ms": current.get("wind_gust_ms"),
        "precipitation_1h_mm": current.get("precipitation_1h_mm"),
        "precipitation_1h_at": (
            precip_at.astimezone(tz) if precip_at is not None else None
        ),
        "pressure_hpa": current.get("pressure_hpa"),
        "cloud_cover_pct": current.get("cloud_cover_pct"),
        "observed_at": observed_local,
        "tz_id": GEOSPHERE_TZ,
        "tz_abbr": observed_local.tzname() if observed_local is not None else None,
        "source": source,
    }


def render_current(data: dict[str, Any]) -> str:
    """Render the uniform current-conditions dict as compact emoji markdown."""
    lines = [f"# Current Weather at {_coords(data['latitude'], data['longitude'])}", ""]

    temp = _temp(data.get("temperature_c"))
    if temp is not None:
        feels = _temp(data.get("apparent_temperature_c"))
        suffix = f" (feels like {feels}°C)" if feels is not None else ""
        lines.append(f"🌡️ Temperature: {temp}°C{suffix}")

    if data.get("condition"):
        lines.append(f"🌤️ Condition: {data['condition']}")

    humidity = _round_int(data.get("humidity_pct"))
    if humidity is not None:
        lines.append(f"💧 Humidity: {humidity}%")

    wind = _temp(data.get("wind_speed_ms"))
    if wind is not None:
        bearing = _round_int(data.get("wind_bearing_deg"))
        gust = _round_int(data.get("wind_gust_ms"))
        line = f"💨 Wind: {wind} m/s"
        if bearing is not None:
            line += f" from {bearing}°"
        if gust is not None:
            line += f" (gusts {gust} m/s)"
        lines.append(line)

    precip = _mm(data.get("precipitation_1h_mm"))
    if precip is not None:
        # Dated by its own stamp, not by the source line's observation time.
        # The two come from the same INCA slice but not the same row, so a
        # slice with a current temperature and an hours-old `RR` would
        # otherwise read as an hour of rain that has just fallen.
        covers = _hm(data.get("precipitation_1h_at"))
        window = f"hour to {covers}" if covers is not None else "last hour"
        lines.append(f"🌧️ Precipitation ({window}): {precip} mm")

    pressure = _round_int(data.get("pressure_hpa"))
    if pressure is not None:
        lines.append(f"📊 Pressure: {pressure} hPa")

    cloud = _round_int(data.get("cloud_cover_pct"))
    if cloud is not None:
        lines.append(f"☁️ Cloud cover: {cloud}%")

    tz_line = _tz_line(data.get("tz_id"), data.get("tz_abbr"))
    if tz_line is not None:
        lines.append(tz_line)

    observed = _hm(data.get("observed_at"))
    source_line = f"📡 Source: {data['source']}"
    if observed is not None:
        source_line += f" — observed {observed}"
    lines.append(source_line)

    return "\n".join(lines)


# --- Hourly forecast ---


def _hourly_line(hour: dict[str, Any]) -> str:
    """Render one hourly entry, omitting zero/None precip and probability."""
    tstr = _hm(hour.get("time")) or "??:??"
    temp = _temp(hour.get("temperature_c"))
    head = f"{tstr}: {temp}°C" if temp is not None else f"{tstr}: n/a"
    if hour.get("condition"):
        head += f" — {hour['condition']}"

    segments: list[str] = []
    precip = hour.get("precipitation_mm")
    precip_str = _mm(precip)
    if precip is not None and precip > 0 and precip_str is not None:
        seg = f"{precip_str} mm"
        prob = hour.get("precipitation_probability_pct")
        if prob:
            seg += f" ({round(prob)}% chance)"
        segments.append(seg)

    wind = _round_int(hour.get("wind_speed_ms"))
    if wind is not None:
        segments.append(f"wind {wind} m/s")

    if segments:
        head += ", " + ", ".join(segments)
    return head


def normalize_hourly_geosphere(
    assembled: dict[str, Any],
    latitude: float,
    longitude: float,
    requested_hours: int,
) -> dict[str, Any]:
    """Fold an :func:`weather.assemble_hourly_forecast` result into the uniform
    hourly shape, localizing times to Vienna and computing the horizon note."""
    tz = ZoneInfo(GEOSPHERE_TZ)
    hours = [
        {**hour, "time": hour["time"].astimezone(tz)}
        for hour in assembled.get("hourly", [])
    ]
    reference = assembled.get("reference_time")
    reference_local = reference.astimezone(tz) if reference is not None else None
    sources = assembled.get("sources") or ["AROME"]

    note = None
    if hours and len(hours) < requested_hours:
        last = hours[-1]["time"]
        note = (
            f"Note: AROME forecast horizon ends {last:%Y-%m-%d %H:%M} "
            f"(~{AROME_MAX_HOURS} h). This server publishes nothing beyond it."
        )

    return {
        "latitude": latitude,
        "longitude": longitude,
        "requested_hours": requested_hours,
        "hours": hours,
        "model": "AROME",
        "reference_time": reference_local,
        "tz_id": GEOSPHERE_TZ,
        "tz_abbr": reference_local.tzname() if reference_local is not None else None,
        "source": f"GeoSphere ({' + '.join(sources)})",
        "note": note,
    }


def render_hourly(data: dict[str, Any]) -> str:
    """Render the uniform hourly dict as compact emoji markdown."""
    requested = data.get("requested_hours") or len(data.get("hours", []))
    lines = [
        f"# {requested}-Hour Forecast for "
        f"{_coords(data['latitude'], data['longitude'])}",
        "",
    ]

    header = f"{data['model']} model"
    reference = data.get("reference_time")
    if reference is not None:
        header += f", reference {reference:%Y-%m-%d %H:%M}"
        if data.get("tz_abbr"):
            header += f" {data['tz_abbr']}"
    header += f" · Source: {data['source']}"
    lines.append(header)
    lines.append("")

    hours = data.get("hours", [])
    if hours:
        current_day: date | None = None
        for hour in hours:
            when = hour.get("time")
            day = when.date() if when is not None else None
            if day is not None and day != current_day:
                lines.append(f"{when:%a} {when:%Y-%m-%d}")
                current_day = day
            indent = "  " if current_day is not None else ""
            lines.append(f"{indent}{_hourly_line(hour)}")
    else:
        lines.append("No forecast hours available for the requested window.")

    if data.get("note"):
        lines.append("")
        lines.append(data["note"])

    return "\n".join(lines)


# --- Storm outlook ---


def _outlook(
    rows: list[dict[str, Any]],
    now: datetime,
    localize: Callable[[datetime], datetime] | None = None,
) -> dict[str, Any]:
    """Run every outlook derivation over one hourly series.

    ``rows`` and ``now`` must share a timezone convention (both aware, or both
    naive local). ``localize`` optionally maps the resulting timestamps into the
    zone the renderer prints.

    Each horizon is windowed once and read from, rather than every derivation
    re-walking the series for itself.
    """
    short_window = window(rows, OUTLOOK_SHORT_HORIZON_HOURS, now)
    long_window = window(rows, OUTLOOK_LONG_HORIZON_HOURS, now)
    gust_short, gust_short_at = max_gust(short_window)
    gust_long, gust_long_at = max_gust(long_window)
    # The third element separates "no storm ahead" from "nothing readable
    # here"; without it the renderer would print a confident all-clear over an
    # unreadable series.
    storm_at, storm_cape, storm_decidable = scan_thunderstorm(rows, now)

    def _when(value: datetime | None) -> datetime | None:
        if value is None or localize is None:
            return value
        return localize(value)

    return {
        "short_horizon_hours": OUTLOOK_SHORT_HORIZON_HOURS,
        "long_horizon_hours": OUTLOOK_LONG_HORIZON_HOURS,
        "max_gust_short_ms": gust_short,
        "max_gust_short_at": _when(gust_short_at),
        "max_gust_long_ms": gust_long,
        "max_gust_long_at": _when(gust_long_at),
        "thunderstorm_short": thunderstorm_outlook(short_window),
        "next_thunderstorm_at": _when(storm_at),
        "next_thunderstorm_cape_jkg": storm_cape,
        "next_thunderstorm_decidable": storm_decidable,
        "max_cape_long_jkg": max_cape(long_window),
        "hours_available": len(rows),
        # How far ahead the all-clear above actually reaches. Nominally ~60 h,
        # but a run that is stale or was truncated hands over fewer, and the
        # all-clear must name the span it was actually scanned over.
        "scanned_horizon_hours": horizon_hours(rows, now),
    }


def normalize_outlook_geosphere(
    assembled: dict[str, Any],
    latitude: float,
    longitude: float,
    now: datetime | None = None,
) -> dict[str, Any]:
    """Fold an assembled GeoSphere hourly forecast into the uniform outlook shape.

    The derivation runs on the aware UTC rows; only the reported timestamps are
    localized to Vienna, so no comparison ever crosses a zone.
    """
    now = now or datetime.now(UTC)
    tz = ZoneInfo(GEOSPHERE_TZ)
    rows = assembled.get("hourly", [])
    data = _outlook(rows, now, localize=lambda when: when.astimezone(tz))
    sources = assembled.get("sources") or ["AROME"]
    reference = assembled.get("reference_time")
    reference_local = reference.astimezone(tz) if reference is not None else None
    return {
        **data,
        "latitude": latitude,
        "longitude": longitude,
        "model": "AROME",
        "reference_time": reference_local,
        "tz_id": GEOSPHERE_TZ,
        "tz_abbr": reference_local.tzname() if reference_local is not None else None,
        "source": f"GeoSphere ({' + '.join(sources)})",
    }


def _stamp(when: datetime | None) -> str | None:
    """Render a timestamp as ``Day YYYY-MM-DD HH:MM`` for outlook lines."""
    return None if when is None else f"{when:%a %Y-%m-%d %H:%M}"


def _tristate(value: bool | None) -> str:
    """Render the tri-state thunderstorm outlook."""
    if value is None:
        return "unknown (no usable forecast hours)"
    return "yes" if value else "no"


def render_outlook(data: dict[str, Any]) -> str:
    """Render the uniform storm-outlook dict as compact emoji markdown."""
    short = data["short_horizon_hours"]
    long = data["long_horizon_hours"]
    lines = [
        f"# Storm Outlook for {_coords(data['latitude'], data['longitude'])}",
        "",
    ]

    header = f"{data['model']} model"
    reference = data.get("reference_time")
    if reference is not None:
        header += f", reference {reference:%Y-%m-%d %H:%M}"
        if data.get("tz_abbr"):
            header += f" {data['tz_abbr']}"
    header += f" · Source: {data['source']}"
    lines.append(header)
    lines.append("")

    if not data.get("hours_available"):
        lines.append("No forecast hours available for the outlook window.")
        return "\n".join(lines)

    for label, value_key, time_key in (
        (f"next {short} h", "max_gust_short_ms", "max_gust_short_at"),
        (f"next {long} h", "max_gust_long_ms", "max_gust_long_at"),
    ):
        gust = _round_int(data.get(value_key))
        if gust is None:
            lines.append(f"💨 Max gust {label}: unknown")
            continue
        at = _stamp(data.get(time_key))
        suffix = f" (at {at})" if at is not None else ""
        lines.append(f"💨 Max gust {label}: {gust} m/s{suffix}")

    lines.append(
        f"⛈️ Thunderstorm expected next {short} h: "
        f"{_tristate(data.get('thunderstorm_short'))}"
    )

    storm_at = _stamp(data.get("next_thunderstorm_at"))
    scanned = data.get("scanned_horizon_hours")
    if storm_at is None and not data.get("next_thunderstorm_decidable"):
        lines.append("⚡ Next thunderstorm: unknown (no usable forecast hours)")
    elif storm_at is None:
        # Name the horizon the all-clear covers rather than implying it runs to
        # the nominal ~60 h: a stale or truncated AROME run reaches less far.
        span = f"next {scanned} h" if scanned else "forecast horizon"
        lines.append(f"⚡ Next thunderstorm: none in the {span}")
    else:
        cape = _round_int(data.get("next_thunderstorm_cape_jkg"))
        suffix = f" (CAPE {cape} J/kg)" if cape is not None else ""
        lines.append(f"⚡ Next thunderstorm: {storm_at}{suffix}")

    cape_max = _round_int(data.get("max_cape_long_jkg"))
    if cape_max is not None:
        lines.append(f"🌡️ Max CAPE next {long} h: {cape_max} J/kg")

    tz_line = _tz_line(data.get("tz_id"), data.get("tz_abbr"))
    if tz_line is not None:
        lines.append(tz_line)

    lines.append("")
    lines.append(
        f'Horizons round up to whole hours: the "{short} h" window covers the '
        "hour already under way plus the next one. A thunderstorm timestamp at "
        "or before now means one is already in progress."
    )

    return "\n".join(lines)


# --- Air quality ---

# Display order and label for the four pollutants, from the same table that
# builds the WRF-Chem request parameters.
_POLLUTANT_LABELS = tuple((key, label) for key, label, _ in AIR_QUALITY_POLLUTANTS)


def normalize_air_quality_geosphere(
    merged: dict[str, Any],
    latitude: float,
    longitude: float,
) -> dict[str, Any]:
    """Fold an :func:`air_quality.merge_air_quality` dict into the uniform shape."""
    tz = ZoneInfo(GEOSPHERE_TZ)
    observed = merged.get("observed_at")
    observed_local = observed.astimezone(tz) if observed is not None else None
    sources = merged.get("sources") or ["WRF-Chem"]
    return {
        "latitude": latitude,
        "longitude": longitude,
        "pollutants": merged.get("pollutants") or {},
        "days": [
            {"label": label, "band": merged.get(f"aqi_band_{key}")}
            for key, label in (
                ("today", "today"),
                ("tomorrow", "tomorrow"),
                ("in_2_days", "in 2 days"),
            )
        ],
        "observed_at": observed_local,
        "tz_id": GEOSPHERE_TZ,
        "tz_abbr": observed_local.tzname() if observed_local is not None else None,
        "source": f"GeoSphere ({' + '.join(sources)}, 3 km)",
    }


def _aqi_day_text(day: dict[str, Any]) -> str | None:
    """Render one AQI day as ``2 (fair) today``, or None when unknown.

    GeoSphere publishes the 1-6 EEA band directly and no underlying numeric
    index, so the band is the whole figure.
    """
    band = day.get("band")
    label = aqi_label(band)
    if label is None:
        return None
    return f"{band} ({label}) {day['label']}"


def render_air_quality(data: dict[str, Any]) -> str:
    """Render the uniform air-quality dict as compact emoji markdown."""
    lines = [
        f"# Air Quality at {_coords(data['latitude'], data['longitude'])}",
        "",
    ]

    days = [text for text in map(_aqi_day_text, data.get("days", [])) if text]
    if days:
        lines.append(f"🏷️ European AQI: {' · '.join(days)}")

    concentrations = [
        f"{label} {round(value)} µg/m³"
        for key, label in _POLLUTANT_LABELS
        if (value := (data.get("pollutants") or {}).get(key)) is not None
    ]
    if concentrations:
        observed = _hm(data.get("observed_at"))
        heading = "🌫️ Concentrations"
        if observed is not None:
            heading += f" ({observed})"
        lines.append(f"{heading}: {' · '.join(concentrations)}")

    empty = not days and not concentrations
    if empty:
        lines.append("No air-quality data available for this location.")
    else:
        tz_line = _tz_line(data.get("tz_id"), data.get("tz_abbr"))
        if tz_line is not None:
            lines.append(tz_line)

    # Named even when nothing came back: which source drew the blank is what
    # tells the caller whether retrying or asking elsewhere is worth anything.
    lines.append(f"📡 Source: {data['source']}")

    if not empty:
        lines.append("")
        lines.append(
            "AQI bands are the European (EEA) scale: 1 good, 2 fair, 3 moderate, "
            "4 poor, 5 very poor, 6 extremely poor. These are model forecasts, "
            "not station measurements."
        )
    return "\n".join(lines)
