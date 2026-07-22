"""Pure processing + high-level fetch helpers for GeoSphere weather data.

The pure functions (``merge_current_conditions``, ``assemble_hourly_forecast``,
and their helpers) take already-fetched :class:`GeoSphereResponse` objects and
are fully testable without I/O. The ``async_fetch_*`` helpers orchestrate the
concurrent HTTP calls, apply the graceful-degradation rules, and track which
sources contributed.

Re-hosted (pure, no homeassistant) from the ha-geosphere-next coordinator.
"""

from __future__ import annotations

import asyncio
import logging
from datetime import UTC, datetime, timedelta
from typing import Any

import aiohttp

from geosphere_mcp_server.condition import (
    apparent_temperature,
    derive_condition,
    derive_current_condition,
    dew_point_from_t_rh,
    is_night,
    wind_from_components,
)
from geosphere_mcp_server.const import (
    AROME_PARAMETERS,
    DATASET_AROME,
    DATASET_ENSEMBLE,
    DATASET_INCA,
    DATASET_NOWCAST,
    ENSEMBLE_PARAMETERS,
    INCA_LOOKBACK_HOURS,
    INCA_PARAMETERS,
    NOWCAST_PARAMETERS,
    POP_DRY_PCT,
    POP_P10_WET_PCT,
    POP_P50_WET_PCT,
    POP_P90_WET_PCT,
    PRECIP_MIN_MM,
    PT_NO_PRECIPITATION,
)
from geosphere_mcp_server.geosphere_api import (
    GeoSphereApiError,
    GeoSphereOutOfDomainError,
    GeoSphereResponse,
    async_get_timeseries,
)

_LOGGER = logging.getLogger(__name__)


# --- Pure numeric helpers ---


def _percent(value: float | None) -> float | None:
    """Normalize AROME tcc (verified 0-1 scale) to percent."""
    if value is None:
        return None
    return round(value * 100.0, 0)


def _precipitation_probability(
    p10: float | None, p50: float | None, p90: float | None
) -> int | None:
    """Stepped probability of a wet hour (>= PRECIP_MIN_MM) from rr percentiles.

    The ensemble API only publishes p10/p50/p90 (no member fractions), so the
    wettest percentile above the threshold bounds the share of wet members and
    the midpoint of that range is reported: 95 / 70 / 30 / 0 %.
    """
    if p90 is None:
        return None
    if p10 is not None and p10 >= PRECIP_MIN_MM:
        return POP_P10_WET_PCT
    if p50 is not None and p50 >= PRECIP_MIN_MM:
        return POP_P50_WET_PCT
    if p90 >= PRECIP_MIN_MM:
        return POP_P90_WET_PCT
    return POP_DRY_PCT


def _diff(series: list[float | None], index: int) -> float | None:
    """Hourly value from a run-accumulated series: acc[i] - acc[i-1].

    Negative deltas (accumulation reset on a new model run) clamp to 0.
    Index 0 has no predecessor and returns None.
    """
    if index < 1 or index >= len(series):
        return None
    current, previous = series[index], series[index - 1]
    if current is None or previous is None:
        return None
    return max(round(current - previous, 2), 0.0)


def _nearest_index(timestamps: list[datetime], when: datetime) -> int | None:
    """Index of the timestamp closest to ``when``; None if the list is empty."""
    if not timestamps:
        return None
    return min(
        range(len(timestamps)),
        key=lambda i: abs((timestamps[i] - when).total_seconds()),
    )


def _pop_by_timestamp(
    ensemble: GeoSphereResponse | None,
) -> dict[datetime, int | None]:
    """Map each ensemble hour to its stepped precipitation probability."""
    pop: dict[datetime, int | None] = {}
    if ensemble is None:
        return pop
    for i, ts in enumerate(ensemble.timestamps):
        pop[ts] = _precipitation_probability(
            ensemble.value_at("rr_p10", i),
            ensemble.value_at("rr_p50", i),
            ensemble.value_at("rr_p90", i),
        )
    return pop


# --- Hourly forecast assembly (AROME + ensemble) ---


def assemble_hourly_forecast(
    arome: GeoSphereResponse,
    ensemble: GeoSphereResponse | None,
    latitude: float,
    longitude: float,
    now: datetime,
    hours: int | None = None,
    start: datetime | None = None,
) -> dict[str, Any]:
    """Assemble the per-hour AROME forecast (+ ensemble precip probability).

    Skips index 0 (accumulated parameters have no predecessor step) and any
    hour before the current top-of-hour. ``start`` filters to hours at/after a
    given instant; ``hours`` truncates the result length.
    """
    pop_by_ts = _pop_by_timestamp(ensemble)
    cutoff = now.replace(minute=0, second=0, microsecond=0)
    hourly: list[dict[str, Any]] = []

    for i in range(1, len(arome.timestamps)):
        ts = arome.timestamps[i]
        if ts < cutoff:
            continue
        wind_speed, wind_bearing = wind_from_components(
            arome.value_at("u10m", i), arome.value_at("v10m", i)
        )
        gust_speed, _ = wind_from_components(
            arome.value_at("ugust", i), arome.value_at("vgust", i)
        )
        precipitation = _diff(arome.series("rr_acc"), i)
        snow = _diff(arome.series("snow_acc"), i)
        cloud = _percent(arome.value_at("tcc", i))
        cape = arome.value_at("cape", i)
        temperature = arome.value_at("t2m", i)
        humidity = arome.value_at("rh2m", i)
        hourly.append(
            {
                "time": ts,
                "condition": derive_condition(
                    precipitation,
                    snow,
                    cloud,
                    cape,
                    gust_speed,
                    is_night(latitude, longitude, ts),
                ),
                "temperature_c": temperature,
                "humidity_pct": humidity,
                "dew_point_c": dew_point_from_t_rh(temperature, humidity),
                "wind_speed_ms": wind_speed,
                "wind_bearing_deg": wind_bearing,
                "wind_gust_ms": gust_speed,
                "cloud_cover_pct": cloud,
                "precipitation_mm": precipitation,
                "snow_mm": snow,
                "snow_limit_m": arome.value_at("snowlmt", i),
                "precipitation_probability_pct": pop_by_ts.get(ts),
                "cape_jkg": cape,
                "global_radiation_wm2": arome.value_at("grad", i),
            }
        )

    if start is not None:
        hourly = [h for h in hourly if h["time"] >= start]
    if hours is not None:
        hourly = hourly[: max(hours, 0)]

    return {
        "reference_time": arome.reference_time,
        "grid_latitude": arome.grid_latitude,
        "grid_longitude": arome.grid_longitude,
        "hourly": hourly,
    }


# --- Current-conditions merge (INCA -> nowcast -> AROME) ---


def _arome_current(
    arome: GeoSphereResponse | None,
    now: datetime,
) -> dict[str, Any] | None:
    """AROME "step 0" snapshot — first future hour — for the fallback chain."""
    if arome is None:
        return None
    cutoff = now.replace(minute=0, second=0, microsecond=0)
    index: int | None = None
    for i in range(1, len(arome.timestamps)):
        if arome.timestamps[i] >= cutoff:
            index = i
            break
    if index is None:
        return None
    wind_speed, wind_bearing = wind_from_components(
        arome.value_at("u10m", index), arome.value_at("v10m", index)
    )
    gust_speed, _ = wind_from_components(
        arome.value_at("ugust", index), arome.value_at("vgust", index)
    )
    return {
        "temperature": arome.value_at("t2m", index),
        "humidity": arome.value_at("rh2m", index),
        "wind_speed": wind_speed,
        "wind_bearing": wind_bearing,
        "wind_gust_speed": gust_speed,
        "cloud_coverage": _percent(arome.value_at("tcc", index)),
        "cape": arome.value_at("cape", index),
        "snow_limit": arome.value_at("snowlmt", index),
    }


def merge_current_conditions(
    nowcast: GeoSphereResponse | None,
    inca: GeoSphereResponse | None,
    arome: GeoSphereResponse | None,
    latitude: float,
    longitude: float,
    now: datetime,
) -> dict[str, Any]:
    """Merge INCA + nowcast + AROME into a current-conditions dict.

    Per-field preference (ported from GeoSphereCurrentCoordinator._merge):
    temp/humidity/wind speed+bearing = INCA -> nowcast -> AROME; dew point =
    INCA -> nowcast; gust = nowcast -> AROME; pressure (P0 Pa->hPa) and global
    radiation = INCA only; cloud/CAPE = AROME; 1-h precip = INCA RR else the
    sum of the last four 15-min nowcast rr buckets at/before now; precip type
    from nowcast pt (255 = none).
    """
    arome_current = _arome_current(arome, now)

    def now_value(name: str) -> float | None:
        if nowcast is None:
            return None
        index = _nearest_index(nowcast.timestamps, now)
        if index is None:
            return None
        return nowcast.value_at(name, index)

    def inca_latest(name: str) -> tuple[float | None, datetime | None]:
        if inca is None:
            return None, None
        data = inca.series(name)
        for i in range(len(inca.timestamps) - 1, -1, -1):
            if data[i] is not None:
                return data[i], inca.timestamps[i]
        return None, None

    def chain(*values: float | None) -> float | None:
        for value in values:
            if value is not None:
                return value
        return None

    def arome_field(name: str) -> float | None:
        return arome_current.get(name) if arome_current else None

    inca_wind_speed, inca_wind_bearing = wind_from_components(
        inca_latest("UU")[0], inca_latest("VV")[0]
    )
    # INCA (observation-anchored hourly analysis) beats the 15-min nowcast for
    # thermodynamic fields and wind: the nowcast extrapolates from an analysis
    # ~2 h behind, lagging diurnal ramps by up to ~2 °C.
    temperature = chain(
        inca_latest("T2M")[0], now_value("t2m"), arome_field("temperature")
    )
    humidity = chain(inca_latest("RH2M")[0], now_value("rh2m"), arome_field("humidity"))
    wind_speed = chain(inca_wind_speed, now_value("ff"), arome_field("wind_speed"))
    gust = chain(now_value("fx"), arome_field("wind_gust_speed"))
    cloud = arome_field("cloud_coverage")
    cape = arome_field("cape")

    p0, _ = inca_latest("P0")
    rr_1h, observed_at = inca_latest("RR")
    if rr_1h is None and nowcast is not None:
        # Sum the last four 15-min nowcast buckets at/before now.
        past = [
            value
            for ts, value in zip(nowcast.timestamps, nowcast.series("rr"), strict=True)
            if ts <= now and value is not None
        ]
        rr_1h = round(sum(past[-4:]), 2) if past else None

    pt_raw = now_value("pt")
    precipitation_type = int(pt_raw) if pt_raw is not None else None
    nowcast_rr = now_value("rr")
    rate_mm_h = nowcast_rr * 4.0 if nowcast_rr is not None else (rr_1h or 0.0)
    night = is_night(latitude, longitude, now)

    return {
        "observed_at": observed_at or now,
        "temperature_c": temperature,
        "apparent_temperature_c": apparent_temperature(
            temperature, humidity, wind_speed
        ),
        "dew_point_c": chain(inca_latest("TD2M")[0], now_value("td")),
        "humidity_pct": humidity,
        "pressure_hpa": round(p0 / 100.0, 1) if p0 is not None else None,
        "wind_speed_ms": wind_speed,
        "wind_bearing_deg": chain(
            inca_wind_bearing, now_value("dd"), arome_field("wind_bearing")
        ),
        "wind_gust_ms": gust,
        "precipitation_1h_mm": rr_1h,
        "precipitation_type": precipitation_type,
        "is_precipitating": (
            precipitation_type is not None and precipitation_type != PT_NO_PRECIPITATION
        ),
        "cloud_cover_pct": cloud,
        "global_radiation_wm2": inca_latest("GL")[0],
        "snow_limit_m": arome_field("snow_limit"),
        "cape_jkg": cape,
        "condition": derive_current_condition(
            precipitation_type=precipitation_type,
            precipitation_rate_mm_h=rate_mm_h,
            temperature=temperature,
            humidity=humidity,
            wind_speed=wind_speed,
            cloud_coverage=cloud,
            cape=cape,
            gust_speed=gust,
            night=night,
        ),
    }


# --- High-level fetch + assemble helpers (orchestration) ---


async def async_fetch_current_conditions(
    session: aiohttp.ClientSession,
    latitude: float,
    longitude: float,
    now: datetime | None = None,
) -> dict[str, Any]:
    """Fetch INCA + nowcast + AROME concurrently and merge current conditions.

    Degrades to an AROME-only snapshot when INCA/nowcast are unavailable
    (e.g. the point is inside the AROME domain but outside Austria). Raises
    :class:`GeoSphereOutOfDomainError` only when AROME itself is out of domain,
    so the caller can fall back to Open-Meteo. Other AROME errors propagate.
    The result carries a ``sources`` list of the datasets that contributed.
    """
    now = now or datetime.now(UTC)
    inca_start = now - timedelta(hours=INCA_LOOKBACK_HOURS)

    arome_res, nowcast_res, inca_res = await asyncio.gather(
        async_get_timeseries(
            session, *DATASET_AROME, AROME_PARAMETERS, latitude, longitude
        ),
        async_get_timeseries(
            session, *DATASET_NOWCAST, NOWCAST_PARAMETERS, latitude, longitude
        ),
        async_get_timeseries(
            session,
            *DATASET_INCA,
            INCA_PARAMETERS,
            latitude,
            longitude,
            start=inca_start,
            end=now,
        ),
        return_exceptions=True,
    )

    if isinstance(arome_res, BaseException):
        raise arome_res
    arome: GeoSphereResponse = arome_res

    nowcast = _optional_response(nowcast_res, "nowcast")
    inca = _optional_response(inca_res, "INCA")

    sources: list[str] = []
    if inca is not None:
        sources.append("INCA")
    if nowcast is not None:
        sources.append("nowcast")
    sources.append("AROME")

    merged = merge_current_conditions(nowcast, inca, arome, latitude, longitude, now)
    merged["sources"] = sources
    merged["grid_latitude"] = arome.grid_latitude
    merged["grid_longitude"] = arome.grid_longitude
    return merged


async def async_fetch_hourly_forecast(
    session: aiohttp.ClientSession,
    latitude: float,
    longitude: float,
    hours: int = 24,
    start: datetime | None = None,
    now: datetime | None = None,
) -> dict[str, Any]:
    """Fetch AROME (+ C-LAEF ensemble) concurrently and assemble the hourly forecast.

    Ensemble failure just omits precipitation probability. Raises
    :class:`GeoSphereOutOfDomainError` only when AROME is out of domain (caller
    falls back to Open-Meteo); other AROME errors propagate. The result carries
    a ``sources`` list.
    """
    now = now or datetime.now(UTC)

    arome_res, ensemble_res = await asyncio.gather(
        async_get_timeseries(
            session, *DATASET_AROME, AROME_PARAMETERS, latitude, longitude
        ),
        async_get_timeseries(
            session, *DATASET_ENSEMBLE, ENSEMBLE_PARAMETERS, latitude, longitude
        ),
        return_exceptions=True,
    )

    if isinstance(arome_res, BaseException):
        raise arome_res
    arome: GeoSphereResponse = arome_res

    ensemble = _optional_response(ensemble_res, "C-LAEF ensemble")

    assembled = assemble_hourly_forecast(
        arome, ensemble, latitude, longitude, now, hours=hours, start=start
    )
    sources = ["AROME"]
    if ensemble is not None:
        sources.append("C-LAEF ensemble")
    assembled["sources"] = sources
    return assembled


def _optional_response(
    result: GeoSphereResponse | BaseException,
    label: str,
) -> GeoSphereResponse | None:
    """Return a secondary-dataset response, or None if it failed.

    Out-of-domain and other GeoSphere API errors degrade gracefully (the
    dataset is simply dropped); unexpected exceptions propagate.
    """
    if isinstance(result, GeoSphereOutOfDomainError):
        _LOGGER.debug("%s outside coverage, degrading", label)
        return None
    if isinstance(result, GeoSphereApiError):
        _LOGGER.warning("%s update failed, degrading: %s", label, result)
        return None
    if isinstance(result, BaseException):
        raise result
    return result
