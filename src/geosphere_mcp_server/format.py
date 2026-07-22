"""Markdown renderers for the three weather tools.

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

from datetime import UTC, datetime, timedelta
from typing import Any
from zoneinfo import ZoneInfo

from geosphere_mcp_server.const import AROME_MAX_HOURS, wmo_to_condition

# GeoSphere covers Austria + the Alpine region, all within CET/CEST.
GEOSPHERE_TZ = "Europe/Vienna"


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
    hourly = body.get("hourly") or {}
    times = hourly.get("time") or []
    offset = int(body.get("utc_offset_seconds") or 0)

    cutoff = _to_local_naive(now, offset).replace(minute=0, second=0, microsecond=0)
    if start is not None:
        cutoff = max(cutoff, _to_local_naive(start, offset))

    def _col(name: str) -> list[Any]:
        return hourly.get(name) or []

    temps = _col("temperature_2m")
    codes = _col("weather_code")
    precips = _col("precipitation")
    probs = _col("precipitation_probability")
    winds = _col("wind_speed_10m")

    entries: list[dict[str, Any]] = []
    for i, raw in enumerate(times):
        when = _parse_local(raw)
        if when is None or when < cutoff:
            continue
        code = codes[i] if i < len(codes) else None
        entries.append(
            {
                "time": when,
                "condition": wmo_to_condition(code),
                "temperature_c": temps[i] if i < len(temps) else None,
                "precipitation_mm": precips[i] if i < len(precips) else None,
                "precipitation_probability_pct": (probs[i] if i < len(probs) else None),
                "wind_speed_ms": winds[i] if i < len(winds) else None,
            }
        )
        if len(entries) >= max(hours, 1):
            break

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
        lines.extend(_hourly_line(hour) for hour in hours)
    else:
        lines.append("No forecast hours available for the requested window.")

    if data.get("note"):
        lines.append("")
        lines.append(data["note"])

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
