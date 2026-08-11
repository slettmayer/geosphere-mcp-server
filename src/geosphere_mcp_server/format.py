"""Markdown renderers for the five weather tools.

Pure functions, no I/O. Each tool has exactly one renderer; a small
normalization step folds the two source-path shapes (the GeoSphere-path dicts
produced by :mod:`weather` and the raw Open-Meteo bodies) into one uniform dict
per tool so the renderer never has to branch on the source.

Units are metric. GeoSphere timestamps are UTC and are rendered in the Alpine
local time (``Europe/Vienna`` — GeoSphere only covers Austria and the Alps,
which share the CET/CEST zone); the Open-Meteo paths render in the timezone the
API returns for the point. Condition strings are the Home Assistant vocabulary
and are emitted verbatim (e.g. ``partlycloudy``).
"""

from __future__ import annotations

from collections.abc import Callable
from datetime import UTC, date, datetime, timedelta, timezone, tzinfo
from typing import Any
from zoneinfo import ZoneInfo, ZoneInfoNotFoundError

from geosphere_mcp_server.const import (
    AIR_QUALITY_POLLUTANTS,
    AROME_MAX_HOURS,
    GEOSPHERE_TZ,
    OUTLOOK_LONG_HORIZON_HOURS,
    OUTLOOK_SHORT_HORIZON_HOURS,
    aqi_band,
    aqi_label,
    wmo_to_condition,
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


def _negated(value: float | None) -> float | None:
    """Flip a value's sign, keeping ``None``."""
    return None if value is None else -value


def _parse_local(value: str | None) -> datetime | None:
    """Parse an Open-Meteo naive-local ISO timestamp; tolerate ``None``."""
    if not value:
        return None
    try:
        return datetime.fromisoformat(value)
    except (ValueError, TypeError):
        return None


def _to_local_naive(when: datetime, utc_offset_seconds: int) -> datetime:
    """Convert an instant to the point's naive local time.

    Aware datetimes are shifted through UTC by the point's offset; naive ones
    are assumed to already be local.
    """
    if when.tzinfo is None:
        return when
    return (when.astimezone(UTC) + timedelta(seconds=utc_offset_seconds)).replace(
        tzinfo=None
    )


# --- Current conditions ---


def normalize_current_geosphere(
    current: dict[str, Any], latitude: float, longitude: float
) -> dict[str, Any]:
    """Fold a :func:`weather.async_fetch_current_conditions` dict into the
    uniform current shape, localizing the UTC observation time to Vienna."""
    tz = ZoneInfo(GEOSPHERE_TZ)
    observed = current.get("observed_at")
    observed_local = observed.astimezone(tz) if observed is not None else None
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
        "pressure_hpa": current.get("pressure_hpa"),
        "cloud_cover_pct": current.get("cloud_cover_pct"),
        "sunrise": None,
        "sunset": None,
        "observed_at": observed_local,
        "tz_id": GEOSPHERE_TZ,
        "tz_abbr": observed_local.tzname() if observed_local is not None else None,
        "source": source,
    }


def normalize_current_openmeteo(
    body: dict[str, Any], latitude: float, longitude: float
) -> dict[str, Any]:
    """Fold a raw Open-Meteo ``current`` body into the uniform current shape."""
    current = body.get("current") or {}
    daily = body.get("daily") or {}
    is_day = current.get("is_day")
    night = is_day is not None and int(is_day) == 0
    condition = wmo_to_condition(current.get("weather_code"), night=night)

    def _first(name: str) -> str | None:
        values = daily.get(name) or []
        return values[0] if values else None

    return {
        "latitude": latitude,
        "longitude": longitude,
        "temperature_c": current.get("temperature_2m"),
        "apparent_temperature_c": current.get("apparent_temperature"),
        "condition": condition,
        "humidity_pct": current.get("relative_humidity_2m"),
        "wind_speed_ms": current.get("wind_speed_10m"),
        "wind_bearing_deg": current.get("wind_direction_10m"),
        "wind_gust_ms": current.get("wind_gusts_10m"),
        "precipitation_1h_mm": current.get("precipitation"),
        "pressure_hpa": current.get("pressure_msl"),
        "cloud_cover_pct": current.get("cloud_cover"),
        "sunrise": _parse_local(_first("sunrise")),
        "sunset": _parse_local(_first("sunset")),
        "observed_at": _parse_local(current.get("time")),
        "tz_id": body.get("timezone"),
        "tz_abbr": body.get("timezone_abbreviation"),
        "source": "Open-Meteo",
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
        lines.append(f"🌧️ Precipitation (last hour): {precip} mm")

    pressure = _round_int(data.get("pressure_hpa"))
    if pressure is not None:
        lines.append(f"📊 Pressure: {pressure} hPa")

    cloud = _round_int(data.get("cloud_cover_pct"))
    if cloud is not None:
        lines.append(f"☁️ Cloud cover: {cloud}%")

    sunrise = _hm(data.get("sunrise"))
    if sunrise is not None:
        lines.append(f"🌅 Sunrise: {sunrise}")
    sunset = _hm(data.get("sunset"))
    if sunset is not None:
        lines.append(f"🌇 Sunset: {sunset}")

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
            f"(~{AROME_MAX_HOURS} h); use get_daily_forecast for days further ahead."
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


def openmeteo_hourly_rows(body: dict[str, Any]) -> list[dict[str, Any]]:
    """Build the full hourly row list from a raw Open-Meteo hourly body.

    Rows carry naive **local** timestamps (the API is queried with
    ``timezone=auto``) and the same keys the GeoSphere path produces, so the
    hourly renderer and :mod:`outlook` can consume either source. No filtering
    or truncation happens here — the callers apply their own windows.
    """
    hourly = body.get("hourly") or {}
    times = hourly.get("time") or []

    def _col(name: str) -> list[Any]:
        return hourly.get(name) or []

    def _at(column: list[Any], index: int) -> Any:
        return column[index] if index < len(column) else None

    temps = _col("temperature_2m")
    codes = _col("weather_code")
    precips = _col("precipitation")
    probs = _col("precipitation_probability")
    winds = _col("wind_speed_10m")
    gusts = _col("wind_gusts_10m")
    capes = _col("cape")
    cins = _col("convective_inhibition")

    rows: list[dict[str, Any]] = []
    for i, raw in enumerate(times):
        when = _parse_local(raw)
        if when is None:
            continue
        rows.append(
            {
                "time": when,
                "condition": wmo_to_condition(_at(codes, i)),
                "temperature_c": _at(temps, i),
                "precipitation_mm": _at(precips, i),
                "precipitation_probability_pct": _at(probs, i),
                "wind_speed_ms": _at(winds, i),
                "wind_gust_ms": _at(gusts, i),
                "cape_jkg": _at(capes, i),
                # Open-Meteo publishes inhibition as a positive magnitude;
                # AROME publishes it negative, and `is_thunder` expects the
                # AROME sign. A missing value stays None (= uncapped).
                "cin_jkg": _negated(_at(cins, i)),
            }
        )
    return rows


def normalize_hourly_openmeteo(
    body: dict[str, Any],
    latitude: float,
    longitude: float,
    hours: int,
    now: datetime | None = None,
    start: datetime | None = None,
) -> dict[str, Any]:
    """Fold a raw Open-Meteo hourly body into the uniform hourly shape.

    Filters to whole hours at/after the point's local ``now`` (or ``start``)
    and truncates to ``hours``.
    """
    now = now or datetime.now(UTC)
    offset = int(body.get("utc_offset_seconds") or 0)

    cutoff = _to_local_naive(now, offset).replace(minute=0, second=0, microsecond=0)
    if start is not None:
        cutoff = max(cutoff, _to_local_naive(start, offset))

    entries = [row for row in openmeteo_hourly_rows(body) if row["time"] >= cutoff]
    entries = entries[: max(hours, 1)]

    return {
        "latitude": latitude,
        "longitude": longitude,
        "requested_hours": hours,
        "hours": entries,
        "model": "Open-Meteo",
        "reference_time": None,
        "tz_id": body.get("timezone"),
        "tz_abbr": body.get("timezone_abbreviation"),
        "source": "Open-Meteo",
        "note": None,
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
        # How far ahead the all-clear above actually reaches. AROME runs ~60 h;
        # the Open-Meteo fallback counts its days from local midnight, so its
        # horizon shrinks as the day wears on.
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


def _point_zone(body: dict[str, Any]) -> tzinfo:
    """The point's timezone, for attaching to Open-Meteo's naive-local stamps.

    Prefers the named zone the API returns, because only a named zone knows
    where its DST transitions fall. Falls back to the fixed offset when the
    name is missing or unknown to the system's tz database.
    """
    name = body.get("timezone")
    if name:
        try:
            return ZoneInfo(str(name))
        except (ZoneInfoNotFoundError, ValueError):
            pass
    return timezone(timedelta(seconds=int(body.get("utc_offset_seconds") or 0)))


def normalize_outlook_openmeteo(
    body: dict[str, Any],
    latitude: float,
    longitude: float,
    now: datetime | None = None,
) -> dict[str, Any]:
    """Fold a raw Open-Meteo hourly body into the uniform outlook shape.

    Open-Meteo rows are naive local, and the derivation does real duration
    arithmetic — ``now + 12 h``, and the hours left in the horizon. Doing that
    on local wall-clock makes a stated horizon wrong across a DST transition:
    the "next 12 h" would span 11 or 13 actual hours.

    Note that attaching the zone is not on its own enough — ``aware + timedelta``
    is *also* wall-clock arithmetic within that zone. The rows are therefore
    resolved to instants and converted to UTC, where a timedelta is a true
    duration, exactly like the GeoSphere path. Only the reported timestamps are
    localized back for display.
    """
    now = now or datetime.now(UTC)
    if now.tzinfo is None:
        now = now.replace(tzinfo=UTC)
    zone = _point_zone(body)
    rows = [
        {**row, "time": row["time"].replace(tzinfo=zone).astimezone(UTC)}
        for row in openmeteo_hourly_rows(body)
    ]
    data = _outlook(
        rows, now.astimezone(UTC), localize=lambda when: when.astimezone(zone)
    )
    return {
        **data,
        "latitude": latitude,
        "longitude": longitude,
        "model": "Open-Meteo",
        "reference_time": None,
        "tz_id": body.get("timezone"),
        "tz_abbr": body.get("timezone_abbreviation"),
        "source": "Open-Meteo",
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
        # Name the horizon the all-clear covers — it is ~60 h on AROME but only
        # what is left of three days from local midnight on the fallback.
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

# Display order and label for the four pollutants both sources publish, from
# the same table that builds each source's request parameters.
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
            {
                "label": label,
                "band": merged.get(f"aqi_band_{key}"),
                # GeoSphere publishes the EEA band directly and no underlying
                # numeric index, so there is never a figure to append here.
                "value": None,
            }
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


def normalize_air_quality_openmeteo(
    body: dict[str, Any],
    latitude: float,
    longitude: float,
    now: datetime | None = None,
) -> dict[str, Any]:
    """Fold a raw Open-Meteo air-quality body into the uniform shape.

    Concentrations are read at the hour nearest to the point's local ``now``.
    This API publishes no daily index, so each day's figure is the **maximum**
    of that local day's hourly European AQI — banded with the published EEA
    thresholds so it reads like the GeoSphere band index.
    """
    now = now or datetime.now(UTC)
    hourly = body.get("hourly") or {}
    offset = int(body.get("utc_offset_seconds") or 0)
    local_now = _to_local_naive(now, offset)

    times = [_parse_local(raw) for raw in hourly.get("time") or []]

    def _col(name: str) -> list[Any]:
        return hourly.get(name) or []

    index: int | None = None
    valid = [(i, when) for i, when in enumerate(times) if when is not None]
    if valid:
        index = min(valid, key=lambda pair: abs((pair[1] - local_now).total_seconds()))[
            0
        ]

    pollutants: dict[str, float | None] = {}
    for key, _ in _POLLUTANT_LABELS:
        column = _col(key)
        pollutants[key] = (
            column[index] if index is not None and index < len(column) else None
        )

    aqi_column = _col("european_aqi")
    today = local_now.date()
    per_day: dict[int, float] = {}
    for i, when in valid:
        value = aqi_column[i] if i < len(aqi_column) else None
        if value is None:
            continue
        offset_days = (when.date() - today).days
        if offset_days in per_day:
            per_day[offset_days] = max(per_day[offset_days], value)
        else:
            per_day[offset_days] = value

    observed = times[index] if index is not None else None
    return {
        "latitude": latitude,
        "longitude": longitude,
        "pollutants": pollutants,
        "days": [
            {
                "label": label,
                "band": aqi_band(per_day.get(offset_days)),
                "value": per_day.get(offset_days),
            }
            for offset_days, label in ((0, "today"), (1, "tomorrow"), (2, "in 2 days"))
        ],
        "observed_at": observed,
        "tz_id": body.get("timezone"),
        "tz_abbr": body.get("timezone_abbreviation"),
        "source": "Open-Meteo (CAMS)",
    }


def air_quality_is_empty(data: dict[str, Any]) -> bool:
    """True when a normalized air-quality dict carries nothing to report.

    The GeoSphere API answers HTTP 200 with an empty series — not an error —
    when a run is stale or incomplete for a point that *is* inside the grid, so
    "in domain" and "has data" are separate questions. Callers use this to fall
    through to the worldwide source instead of dead-ending on a location the
    server can in fact serve.
    """
    pollutants = data.get("pollutants") or {}
    if any(value is not None for value in pollutants.values()):
        return False
    return all(day.get("band") is None for day in data.get("days") or [])


def _aqi_day_text(day: dict[str, Any]) -> str | None:
    """Render one AQI day as ``2 (fair) today``, or None when unknown.

    The leading figure is always the 1-6 EEA band, so both sources read alike;
    the underlying numeric index is appended only where the source has one
    (Open-Meteo), because GeoSphere publishes the band directly.
    """
    band = day.get("band")
    label = aqi_label(band)
    if label is None:
        return None
    value = day.get("value")
    detail = f"{label}, index {round(value)}" if value is not None else label
    return f"{band} ({detail}) {day['label']}"


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


# --- Daily forecast (Open-Meteo only) ---


def normalize_daily_openmeteo(
    body: dict[str, Any],
    latitude: float,
    longitude: float,
    days: int,
) -> dict[str, Any]:
    """Fold a raw Open-Meteo daily body into the uniform daily shape."""
    daily = body.get("daily") or {}
    dates = daily.get("time") or []

    def _col(name: str) -> list[Any]:
        return daily.get(name) or []

    codes = _col("weather_code")
    tmin = _col("temperature_2m_min")
    tmax = _col("temperature_2m_max")
    precip = _col("precipitation_sum")
    prob = _col("precipitation_probability_max")
    gust = _col("wind_gusts_10m_max")
    wind = _col("wind_speed_10m_max")

    entries: list[dict[str, Any]] = []
    for i, raw in enumerate(dates[: max(days, 1)]):
        try:
            date = datetime.fromisoformat(raw).date()
        except (ValueError, TypeError):
            continue
        # Prefer gust for the "wind up to" figure, fall back to sustained max.
        wind_max = gust[i] if i < len(gust) and gust[i] is not None else None
        if wind_max is None and i < len(wind):
            wind_max = wind[i]
        entries.append(
            {
                "date": date,
                "condition": wmo_to_condition(codes[i] if i < len(codes) else None),
                "temp_min_c": tmin[i] if i < len(tmin) else None,
                "temp_max_c": tmax[i] if i < len(tmax) else None,
                "precip_sum_mm": precip[i] if i < len(precip) else None,
                "precip_prob_max_pct": prob[i] if i < len(prob) else None,
                "wind_max_ms": wind_max,
            }
        )

    return {
        "latitude": latitude,
        "longitude": longitude,
        "requested_days": days,
        "days": entries,
        "tz_id": body.get("timezone"),
        "tz_abbr": body.get("timezone_abbreviation"),
        "source": "Open-Meteo",
    }


def _daily_line(day: dict[str, Any]) -> str:
    """Render one daily entry."""
    date = day["date"]
    label = f"{date:%a} {date:%Y-%m-%d}"
    tmin = _round_int(day.get("temp_min_c"))
    tmax = _round_int(day.get("temp_max_c"))
    if tmin is not None and tmax is not None:
        head = f"{label}: {tmin}–{tmax}°C"
    elif tmax is not None:
        head = f"{label}: {tmax}°C"
    else:
        head = f"{label}: n/a"
    if day.get("condition"):
        head += f" — {day['condition']}"

    segments: list[str] = []
    precip = day.get("precip_sum_mm")
    precip_str = _mm(precip)
    if precip is not None and precip > 0 and precip_str is not None:
        seg = f"{precip_str} mm"
        prob = day.get("precip_prob_max_pct")
        if prob:
            seg += f" ({round(prob)}% chance)"
        segments.append(seg)

    wind = _round_int(day.get("wind_max_ms"))
    if wind is not None:
        segments.append(f"wind up to {wind} m/s")

    if segments:
        head += ", " + ", ".join(segments)
    return head


def render_daily(data: dict[str, Any]) -> str:
    """Render the uniform daily dict as compact emoji markdown."""
    requested = data.get("requested_days") or len(data.get("days", []))
    lines = [
        f"# {requested}-Day Forecast for "
        f"{_coords(data['latitude'], data['longitude'])}",
        "",
    ]

    tz_id = data.get("tz_id")
    source = f"Source: {data['source']}"
    if tz_id:
        source += f" ({tz_id})"
    lines.append(source)
    lines.append("")

    days = data.get("days", [])
    if days:
        lines.extend(_daily_line(day) for day in days)
    else:
        lines.append("No daily forecast available.")

    return "\n".join(lines)
