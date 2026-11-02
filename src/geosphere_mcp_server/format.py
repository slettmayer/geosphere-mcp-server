"""Markdown renderers for the four weather tools.

Pure functions, no I/O. Each tool has exactly one renderer, fed by a
normalization step that localizes the dicts :mod:`weather` and
:mod:`air_quality` produce and adds what the renderer prints alongside them.

Units are metric. Timestamps arrive UTC and are rendered in the Alpine local
time (``Europe/Vienna`` — GeoSphere only covers Austria and the Alps, which
share the CET/CEST zone). Condition strings are the Home Assistant vocabulary
and are emitted verbatim (e.g. ``partlycloudy``).
"""

from __future__ import annotations

from datetime import UTC, date, datetime
from typing import Any
from zoneinfo import ZoneInfo

from geosphere_mcp_server.const import (
    AIR_QUALITY_POLLUTANTS,
    AROME_MAX_HOURS,
    ENSEMBLE_MAX_AGE,
    FALLING_NOW_LABELS,
    GEOSPHERE_TZ,
    OUTLOOK_LONG_HORIZON_HOURS,
    OUTLOOK_SHORT_HORIZON_HOURS,
    POP_DRY_PCT,
    POP_P10_WET_PCT,
    POP_P50_WET_PCT,
    POP_P90_WET_PCT,
    PRECIP_MIN_MM,
    THUNDER_CAPE_JKG,
    aqi_label,
)
from geosphere_mcp_server.outlook import (
    ensemble_is_current,
    horizon_hours,
    max_cape,
    max_gust,
    next_thunderstorm,
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


def _tz_line(tz_abbr: str | None) -> str:
    """Render the ``🕐 Timezone`` line, with the abbreviation when known."""
    suffix = f" ({tz_abbr})" if tz_abbr else ""
    return f"🕐 Timezone: {GEOSPHERE_TZ}{suffix}"


def _source(sources: list[str], suffix: str = "") -> str:
    """Render the datasets behind an answer, e.g. ``GeoSphere (AROME)``."""
    return f"GeoSphere ({' + '.join(sources)}{suffix})"


def _model_header(data: dict[str, Any]) -> str:
    """The ``AROME model, reference … · Source: …`` line of the forecast tools."""
    header = "AROME model"
    reference = data.get("reference_time")
    if reference is not None:
        header += f", reference {reference:%Y-%m-%d %H:%M}"
        if data.get("tz_abbr"):
            header += f" {data['tz_abbr']}"
    return f"{header} · Source: {data['source']}"


# --- Current conditions ---


def normalize_current(
    current: dict[str, Any], latitude: float, longitude: float
) -> dict[str, Any]:
    """Shape a :func:`weather.async_fetch_current_conditions` dict for
    rendering, localizing the UTC observation time to Vienna."""
    tz = ZoneInfo(GEOSPHERE_TZ)
    observed = current.get("observed_at")
    observed_local = observed.astimezone(tz) if observed is not None else None
    precip_at = current.get("precipitation_1h_at")
    return {
        "latitude": latitude,
        "longitude": longitude,
        "temperature_c": current.get("temperature_c"),
        "apparent_temperature_c": current.get("apparent_temperature_c"),
        "condition": current.get("condition"),
        "precipitation_kind": current.get("precipitation_kind"),
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
        "tz_abbr": observed_local.tzname() if observed_local is not None else None,
        "source": _source(current["sources"]),
    }


def render_current(data: dict[str, Any]) -> str:
    """Render the normalized current-conditions dict as compact emoji markdown."""
    lines = [f"# Current Weather at {_coords(data['latitude'], data['longitude'])}", ""]

    temp = _temp(data.get("temperature_c"))
    if temp is not None:
        feels = _temp(data.get("apparent_temperature_c"))
        suffix = f" (feels like {feels}°C)" if feels is not None else ""
        lines.append(f"🌡️ Temperature: {temp}°C{suffix}")

    if data.get("condition"):
        lines.append(f"🌤️ Condition: {data['condition']}")

    # Only when something is falling and the nowcast named it: the condition
    # cannot say "freezing rain" (it renders as snowy-rainy), this line can.
    falling = FALLING_NOW_LABELS.get(data.get("precipitation_kind") or "")
    if falling is not None:
        emoji, label = falling
        lines.append(f"{emoji} Falling now: {label}")

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

    lines.append(_tz_line(data.get("tz_abbr")))

    observed = _hm(data.get("observed_at"))
    source_line = f"📡 Source: {data['source']}"
    if observed is not None:
        source_line += f" — observed {observed}"
    lines.append(source_line)

    return "\n".join(lines)


# --- Hourly forecast ---

# GeoSphere publishes no probability, so the chance shown is labelled as the
# estimate it is wherever it appears.
_CHANCE_NOTE = (
    "Precipitation chance is an estimate derived from the C-LAEF ensemble "
    f"percentiles ({POP_DRY_PCT}/{POP_P90_WET_PCT}/{POP_P50_WET_PCT}/"
    f"{POP_P10_WET_PCT} %), not a probability GeoSphere publishes."
)


def _hourly_line(hour: dict[str, Any]) -> str:
    """Render one hourly entry, omitting zero/None precip and probability.

    An amount that rounds to ``0`` counts as zero: the forecast publishes
    traces to a thousandth of a millimetre, and "0 mm" beside a chance of
    rain reads as a contradiction.
    """
    tstr = f"{hour['time']:%H:%M}"
    temp = _temp(hour.get("temperature_c"))
    head = f"{tstr}: {temp}°C" if temp is not None else f"{tstr}: n/a"
    if hour.get("condition"):
        head += f" — {hour['condition']}"

    segments: list[str] = []
    precip_str = _mm(hour.get("precipitation_mm"))
    if precip_str is not None and precip_str != "0":
        seg = f"{precip_str} mm"
        prob = hour.get("precipitation_probability_pct")
        if prob:
            seg += f" ({round(prob)}% chance)"
        segments.append(seg)

    wind = _round_int(hour.get("wind_speed_ms"))
    if wind is not None:
        segments.append(f"wind {wind} m/s")

    snow_limit = _round_int(hour.get("snow_limit_m"))
    if snow_limit is not None:
        segments.append(f"snow limit {snow_limit} m")

    if segments:
        head += ", " + ", ".join(segments)
    return head


def normalize_hourly(
    assembled: dict[str, Any],
    latitude: float,
    longitude: float,
    requested_hours: int,
) -> dict[str, Any]:
    """Shape an :func:`weather.async_fetch_hourly_forecast` result for
    rendering, localizing times to Vienna and computing the horizon note."""
    tz = ZoneInfo(GEOSPHERE_TZ)
    hours = [
        {**hour, "time": hour["time"].astimezone(tz)} for hour in assembled["hourly"]
    ]
    reference = assembled.get("reference_time")
    reference_local = reference.astimezone(tz) if reference is not None else None

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
        "reference_time": reference_local,
        "tz_abbr": reference_local.tzname() if reference_local is not None else None,
        "source": _source(assembled["sources"]),
        "note": note,
    }


def render_hourly(data: dict[str, Any]) -> str:
    """Render the normalized hourly dict as compact emoji markdown."""
    lines = [
        f"# {data['requested_hours']}-Hour Forecast for "
        f"{_coords(data['latitude'], data['longitude'])}",
        "",
        _model_header(data),
        "",
    ]

    hours = data["hours"]
    shows_chance = False
    if hours:
        current_day: date | None = None
        for hour in hours:
            when = hour["time"]
            if when.date() != current_day:
                lines.append(f"{when:%a %Y-%m-%d}")
                current_day = when.date()
            line = _hourly_line(hour)
            shows_chance = shows_chance or "% chance)" in line
            lines.append(f"  {line}")
    else:
        lines.append("No forecast hours available for the requested window.")

    notes = [data["note"]] if data.get("note") else []
    if shows_chance:
        notes.append(_CHANCE_NOTE)
    if notes:
        lines.append("")
        lines.extend(notes)

    return "\n".join(lines)


# --- Storm outlook ---


def normalize_outlook(
    assembled: dict[str, Any],
    latitude: float,
    longitude: float,
    now: datetime | None = None,
) -> dict[str, Any]:
    """Run every outlook derivation over an assembled hourly forecast.

    The derivation runs on the aware UTC rows; only the reported timestamps are
    localized to Vienna, so no comparison ever crosses a zone. Each horizon is
    windowed once and read from, and whether the ensemble may vote is decided
    once, so the windowed answer and the full scan cannot disagree on it.
    """
    now = now or datetime.now(UTC)
    tz = ZoneInfo(GEOSPHERE_TZ)
    rows = assembled["hourly"]
    ensemble = ensemble_is_current(assembled.get("ensemble_reference_time"), now)
    short_window = window(rows, OUTLOOK_SHORT_HORIZON_HOURS, now)
    long_window = window(rows, OUTLOOK_LONG_HORIZON_HOURS, now)
    gust_short, gust_short_at = max_gust(short_window)
    gust_long, gust_long_at = max_gust(long_window)
    storm_short, storm_short_source = thunderstorm_outlook(short_window, ensemble)
    storm_at, storm_source = next_thunderstorm(rows, now, ensemble)
    reference = assembled.get("reference_time")
    reference_local = reference.astimezone(tz) if reference is not None else None

    def local(when: datetime | None) -> datetime | None:
        return when.astimezone(tz) if when is not None else None

    return {
        "latitude": latitude,
        "longitude": longitude,
        "short_horizon_hours": OUTLOOK_SHORT_HORIZON_HOURS,
        "long_horizon_hours": OUTLOOK_LONG_HORIZON_HOURS,
        "max_gust_short_ms": gust_short,
        "max_gust_short_at": local(gust_short_at),
        "max_gust_long_ms": gust_long,
        "max_gust_long_at": local(gust_long_at),
        "thunderstorm_short": storm_short,
        "thunderstorm_short_source": storm_short_source,
        "next_thunderstorm_at": local(storm_at),
        "next_thunderstorm_source": storm_source,
        "ensemble_current": ensemble,
        "max_cape_long_jkg": max_cape(long_window),
        "hours_available": len(rows),
        # How far ahead an all-clear actually reaches: nominally ~60 h, but
        # publication lag and a stale or truncated run hand over fewer.
        "scanned_horizon_hours": horizon_hours(rows, now),
        "reference_time": reference_local,
        "tz_abbr": reference_local.tzname() if reference_local is not None else None,
        "source": _source(assembled["sources"]),
    }


def _stamp(when: datetime | None) -> str | None:
    """Render a timestamp as ``Day YYYY-MM-DD HH:MM`` for outlook lines."""
    return None if when is None else f"{when:%a %Y-%m-%d %H:%M}"


def _tristate(value: bool | None, source: str | None) -> str:
    """Render the tri-state thunderstorm outlook and the signal behind a yes."""
    if value is None:
        return "unknown (no forecast hours in the window)"
    if not value:
        return "no"
    return f"yes (signal: {source})" if source else "yes"


_STORM_NOTE = (
    "A thunderstorm hour is one whose GeoSphere weather symbol is a "
    "thunderstorm, or where the C-LAEF ensemble's 90th percentiles reach "
    f"{THUNDER_CAPE_JKG:.0f} J/kg CAPE and {PRECIP_MIN_MM} mm precipitation; "
    "`signal` names which fired (symbol, ensemble or both). Max CAPE is a "
    "diagnostic and decides nothing."
)
_NO_ENSEMBLE_LINE = (
    "ℹ️ Ensemble signal: unavailable (the C-LAEF request failed or its run "
    f"is {ENSEMBLE_MAX_AGE.total_seconds() / 3600:.0f} h old or more); "
    "thunderstorms from the weather symbol only"
)


def render_outlook(data: dict[str, Any]) -> str:
    """Render the normalized storm-outlook dict as compact emoji markdown."""
    short = data["short_horizon_hours"]
    long = data["long_horizon_hours"]
    lines = [
        f"# Storm Outlook for {_coords(data['latitude'], data['longitude'])}",
        "",
    ]

    lines.append(_model_header(data))
    lines.append("")

    if not data["hours_available"]:
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

    expected = _tristate(
        data.get("thunderstorm_short"), data.get("thunderstorm_short_source")
    )
    lines.append(f"⛈️ Thunderstorm expected next {short} h: {expected}")

    storm_at = _stamp(data.get("next_thunderstorm_at"))
    if storm_at is None:
        # Name the horizon the all-clear covers rather than implying it runs to
        # the nominal ~60 h: publication lag and a stale or truncated run
        # reach less far.
        scanned = data.get("scanned_horizon_hours")
        span = f"next {scanned} h" if scanned else "forecast horizon"
        lines.append(f"⚡ Next thunderstorm: none in the {span}")
    else:
        source = data.get("next_thunderstorm_source")
        suffix = f" (signal: {source})" if source else ""
        lines.append(f"⚡ Next thunderstorm: {storm_at}{suffix}")

    if not data.get("ensemble_current"):
        lines.append(_NO_ENSEMBLE_LINE)

    cape_max = _round_int(data.get("max_cape_long_jkg"))
    if cape_max is not None:
        lines.append(f"🌡️ Max CAPE next {long} h: {cape_max} J/kg")

    lines.append(_tz_line(data.get("tz_abbr")))

    lines.append("")
    lines.append(
        f'Horizons round up to whole hours: the "{short} h" window covers the '
        "hour already under way plus the next one. A thunderstorm timestamp at "
        "or before now means one is already in progress."
    )
    lines.append(_STORM_NOTE)

    return "\n".join(lines)


# --- Air quality ---

# Display order and label for the four pollutants, from the same table that
# builds the WRF-Chem request parameters.
_POLLUTANT_LABELS = tuple((key, label) for key, label, _ in AIR_QUALITY_POLLUTANTS)


def normalize_air_quality(
    merged: dict[str, Any],
    latitude: float,
    longitude: float,
) -> dict[str, Any]:
    """Shape an :func:`air_quality.async_fetch_air_quality` dict for rendering."""
    tz = ZoneInfo(GEOSPHERE_TZ)
    observed = merged.get("observed_at")
    observed_local = observed.astimezone(tz) if observed is not None else None
    return {
        "latitude": latitude,
        "longitude": longitude,
        "pollutants": merged["pollutants"],
        "days": [
            {"label": label, "band": merged.get(f"aqi_band_{key}")}
            for key, label in (
                ("today", "today"),
                ("tomorrow", "tomorrow"),
                ("in_2_days", "in 2 days"),
            )
        ],
        "observed_at": observed_local,
        "tz_abbr": observed_local.tzname() if observed_local is not None else None,
        "source": _source(merged["sources"], ", 3 km"),
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
    """Render the normalized air-quality dict as compact emoji markdown."""
    lines = [
        f"# Air Quality at {_coords(data['latitude'], data['longitude'])}",
        "",
    ]

    days = [text for text in map(_aqi_day_text, data["days"]) if text]
    if days:
        lines.append(f"🏷️ European AQI: {' · '.join(days)}")

    concentrations = [
        f"{label} {round(value)} µg/m³"
        for key, label in _POLLUTANT_LABELS
        if (value := data["pollutants"].get(key)) is not None
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
        lines.append(_tz_line(data.get("tz_abbr")))

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
