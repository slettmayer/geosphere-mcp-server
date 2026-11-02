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
    derive_condition_from_symbol,
    derive_current_condition,
    derive_precipitation_type,
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
    FORECAST_ROW_MIDPOINT,
    HOURLY_LOOKBACK_HOURS,
    INCA_LOOKBACK_HOURS,
    INCA_PARAMETERS,
    INCA_RR_MAX_AGE_SECONDS,
    NOWCAST_BUCKETS_PER_HOUR,
    NOWCAST_LOOKBACK,
    NOWCAST_PARAMETERS,
    POP_DRY_PCT,
    POP_P10_WET_PCT,
    POP_P50_WET_PCT,
    POP_P90_WET_PCT,
    PRECIP_MIN_MM,
    PRECIPITATION_TYPES,
    PT_NO_PRECIPITATION,
    RATE_LOOKBACK,
    SY_CODES,
)
from geosphere_mcp_server.geosphere_api import (
    GeoSphereApiError,
    GeoSphereOutOfDomainError,
    GeoSphereResponse,
    async_get_timeseries,
)

_LOGGER = logging.getLogger(__name__)

# Codes outside GeoSphere's tables already reported by this process, so a new
# one warns once rather than on every call for as long as it is served.
_UNKNOWN_PT_CODES: set[int] = set()
_UNKNOWN_SY_CODES: set[int] = set()


def _warn_unknown_pt_code(precipitation_type: int | None) -> None:
    """Log a `pt` code outside GeoSphere's table, once per code per process.

    Kept out of :func:`merge_current_conditions` so the merge stays pure; the
    async fetch calls this on what the merge decoded.
    """
    if (
        precipitation_type is None
        or precipitation_type in PRECIPITATION_TYPES
        or precipitation_type in _UNKNOWN_PT_CODES
    ):
        return
    _UNKNOWN_PT_CODES.add(precipitation_type)
    _LOGGER.warning(
        "Nowcast reported precipitation-type code %s, which is not in "
        "GeoSphere's published table; reporting no kind for it",
        precipitation_type,
    )


def _warn_unknown_sy_code(symbol: int | None) -> None:
    """Log a `sy` code outside GeoSphere's table, once per code per process.

    Kept out of :func:`assemble_hourly_forecast` so the assembly stays pure;
    the async fetches call this on the symbols it read.
    """
    if symbol is None or symbol in SY_CODES or symbol in _UNKNOWN_SY_CODES:
        return
    _UNKNOWN_SY_CODES.add(symbol)
    _LOGGER.warning(
        "Forecast reported weather-symbol code %s, which is not in "
        "GeoSphere's published table; its hours' condition reads unknown",
        symbol,
    )


# --- Pure numeric helpers ---


def _precipitation_probability(
    p10: float | None, p50: float | None, p90: float | None
) -> int | None:
    """Stepped chance of a wet hour (>= PRECIP_MIN_MM) from tp percentiles.

    An estimate derived here, not a probability GeoSphere publishes: the
    ensemble API only exposes p10/p50/p90 (no member fractions), so the
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


def _ensemble_by_timestamp(
    ensemble: GeoSphereResponse | None,
) -> dict[datetime, dict[str, Any]]:
    """Map each forecast hour to the ensemble values that describe it.

    The tp percentiles are interval values, like the deterministic `tp`: "in
    the last forecast interval". A percentile stamped `ts` therefore belongs to
    the period *ending* at `ts`, so the probability and `tp_p90` are keyed by
    that period's start -- simply the preceding stamp, the row that reports
    the matching amount. `cape_p90` is instantaneous, like `cape`, so it stays
    on its own stamp.

    The cadence comes from the series itself rather than a fixed step: if
    C-LAEF ever coarsens along its horizon (3-hourly tails are common for
    ensembles) or drops a stamp, a fixed step would miss every key and silently
    blank the probability across the whole forecast with nothing logged. The
    predecessor is right at any cadence. Index 0 has no predecessor, so the
    interval values stamped there have no row to land on.
    """
    by_ts: dict[datetime, dict[str, Any]] = {}
    if ensemble is None:
        return by_ts
    for i, ts in enumerate(ensemble.timestamps):
        by_ts.setdefault(ts, {})["cape_p90"] = ensemble.value_at("cape_p90", i)
        if i == 0:
            continue
        start = by_ts.setdefault(ensemble.timestamps[i - 1], {})
        start["pop"] = _precipitation_probability(
            ensemble.value_at("tp_p10", i),
            ensemble.value_at("tp_p50", i),
            ensemble.value_at("tp_p90", i),
        )
        start["tp_p90"] = ensemble.value_at("tp_p90", i)
    return by_ts


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
    """Assemble the per-hour AROME forecast (+ ensemble values per hour).

    A row stamped ``ts`` describes the hour *beginning* at ``ts``. The dataset
    mixes two stampings (see ``AROME_PARAMETERS`` for how each was classified):

    * instantaneous AT the stamp — ``2t``, ``2r``, ``10u``/``10v``, ``tcc``,
      ``cape``, ``snowlmt``;
    * the interval ENDING at the stamp — ``10fg`` and ``tp`` ("... in the
      last forecast interval"), each already a per-hour value.

    The interval fields covering the hour that *starts* at ``ts`` therefore
    live one step later, at ``nxt``. Reading them at ``i`` would report the
    hour that already ended — rain that has stopped, the previous hour's gust
    peak, and a thunderstorm the outlook would then place in the future.

    ``sy`` is read at ``nxt`` too. Its precipitation classes follow ``tp`` at
    their own stamp, so only there does the symbol agree with the row's
    precipitation: at ``i`` a row would show rain for the hour after it
    stopped and none for the hour it started. Its cloud classes bin ``tcc`` at
    that same later stamp, i.e. the sky at the end of the row's hour. Night is
    judged at the middle of the row's hour.

    The final stamp has no successor and so cannot be emitted. Hours before
    the current top-of-hour are dropped; ``start`` filters to hours at/after a
    given instant, and ``hours`` truncates the result length.
    """
    ensemble_by_ts = _ensemble_by_timestamp(ensemble)
    cutoff = now.replace(minute=0, second=0, microsecond=0)
    hourly: list[dict[str, Any]] = []

    for i in range(len(arome.timestamps) - 1):
        ts = arome.timestamps[i]
        if ts < cutoff:
            continue
        nxt = i + 1
        wind_speed, wind_bearing = wind_from_components(
            arome.value_at("10u", i), arome.value_at("10v", i)
        )
        temperature = arome.value_at("2t", i)
        humidity = arome.value_at("2r", i)
        raw_symbol = arome.value_at("sy", nxt)
        symbol = int(raw_symbol) if raw_symbol is not None else None
        members = ensemble_by_ts.get(ts, {})
        hourly.append(
            {
                "time": ts,
                "weather_symbol": symbol,
                "condition": derive_condition_from_symbol(
                    symbol,
                    is_night(latitude, longitude, ts + FORECAST_ROW_MIDPOINT),
                ),
                "temperature_c": temperature,
                "humidity_pct": humidity,
                "wind_speed_ms": wind_speed,
                "wind_bearing_deg": wind_bearing,
                "wind_gust_ms": arome.value_at("10fg", nxt),
                "cloud_cover_pct": arome.value_at("tcc", i),
                "precipitation_mm": arome.value_at("tp", nxt),
                "snow_limit_m": arome.value_at("snowlmt", i),
                "precipitation_probability_pct": members.get("pop"),
                "cape_jkg": arome.value_at("cape", i),
                # C-LAEF 90th percentiles behind the ensemble thunderstorm
                # signal: CAPE at the row's stamp, precipitation over its hour.
                "ensemble_cape_p90_jkg": members.get("cape_p90"),
                "ensemble_precipitation_p90_mm": members.get("tp_p90"),
            }
        )

    if start is not None:
        hourly = [h for h in hourly if h["time"] >= start]
    if hours is not None:
        hourly = hourly[: max(hours, 0)]

    return {
        "reference_time": arome.reference_time,
        "ensemble_reference_time": (
            ensemble.reference_time if ensemble is not None else None
        ),
        "hourly": hourly,
    }


# --- Current-conditions merge (INCA -> nowcast -> AROME) ---


def _arome_current(
    arome: GeoSphereResponse | None,
    latitude: float,
    longitude: float,
    now: datetime,
) -> dict[str, Any] | None:
    """The assembled AROME row for the hour in progress, for the fallback chain.

    Taken from :func:`assemble_hourly_forecast`, so every field follows the
    same stamping as the hourly forecast: the gust and the weather symbol are
    the ones covering the hour under way, read one stamp on.

    That hour has to be present in the series, and it is not there for free:
    an unbounded request begins well after the current hour, so the caller
    anchors an explicit ``start`` (see :func:`async_fetch_current_conditions`).
    Without it the first row is a future one, and this function would present
    its cloud, CAPE and symbol as current -- the symbol feeds the current
    condition.

    ``None`` when the run has no row at or after the current hour, which
    includes a run whose final stamp is the current hour: that stamp has no
    successor to read the interval fields from.
    """
    if arome is None:
        return None
    rows = assemble_hourly_forecast(arome, None, latitude, longitude, now, hours=1)
    return rows["hourly"][0] if rows["hourly"] else None


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
    temp/humidity/wind speed+bearing = INCA -> nowcast -> AROME; gust =
    nowcast -> AROME; pressure (P0 Pa->hPa) = INCA only; cloud/weather symbol
    = AROME; 1-h precip = INCA RR only; precip type from nowcast pt (255 =
    none).
    """
    arome_current = _arome_current(arome, latitude, longitude, now)

    # One scan, reused by every nowcast field below and by `observed_at`: the
    # bucket cannot change within this call.
    nowcast_index = nowcast.nearest_index(now) if nowcast is not None else None

    def now_value(name: str) -> float | None:
        if nowcast is None or nowcast_index is None:
            return None
        return nowcast.value_at(name, nowcast_index)

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
    inca_t2m, inca_t2m_at = inca_latest("T2M")
    temperature = chain(inca_t2m, now_value("t2m"), arome_field("temperature_c"))
    humidity = chain(
        inca_latest("RH2M")[0], now_value("rh2m"), arome_field("humidity_pct")
    )
    wind_speed = chain(inca_wind_speed, now_value("ff"), arome_field("wind_speed_ms"))
    gust = chain(now_value("fx"), arome_field("wind_gust_ms"))
    symbol = arome_field("weather_symbol")

    p0, _ = inca_latest("P0")
    # INCA `RR` is the only source for the hourly accumulation. Summing nowcast
    # buckets was tried and removed: the endpoint serves a single model run,
    # clamped to that run's own t0 ~25-35 min back, so the sum covered 15-45
    # minutes and was published as an hour -- under-reporting by up to 4x on
    # exactly the degraded path it existed for. Reconstructing a true hour
    # needs the t0 bucket of four consecutive runs (`forecast_offset=0..3`),
    # four requests per call against a shared rate limit. Absent is the honest
    # answer here.
    rr_1h, rr_1h_time = inca_latest("RR")

    pt_raw = now_value("pt")
    precipitation_type = int(pt_raw) if pt_raw is not None else None
    nowcast_rr = now_value("rr")
    # `None`, not 0.0, when the nowcast observed nothing: the condition
    # derivation tells "no precipitation" apart from "no observation", which it
    # cannot do once the absence has been defaulted away.
    nowcast_rate_mm_h = (
        nowcast_rr * NOWCAST_BUCKETS_PER_HOUR if nowcast_rr is not None else None
    )
    # Without a nowcast reading the condition falls back to INCA's `RR` -- but
    # not to a slice that has stopped updating.
    # `inca_latest` returns the newest non-None value at any age, and GeoSphere
    # keeps serving the last analysis it managed to produce, so an ungated read
    # derives `rainy` under a clear sky from rain that stopped hours ago. Past
    # INCA_RR_MAX_AGE_SECONDS it no longer counts as an observation, and with
    # no nowcast either the condition is the weather symbol's alone. The bound
    # is deliberately well past INCA's own ~90 min worst-case lag so ordinary
    # publishing never trips it -- see the constant.
    #
    # `precipitation_1h_mm` keeps reporting the accumulation regardless: it is
    # a real measurement of a past hour. It is NOT dated by `observed_at`,
    # which anchors to whichever source supplied the temperature (see below)
    # and can therefore be newer than the `RR` stamp -- the two are read from
    # the same slice but not the same row. `inca_latest` scans each parameter
    # back independently, so an analysis carrying `T2M` but no `RR` pairs a
    # current `observed_at` with an hours-old total. The stamp travels with
    # the value as `precipitation_1h_at` and the renderer names the hour it
    # covers, rather than letting the source line's time speak for it.
    # Bounded at BOTH ends. A bare `age <= INCA_RR_MAX_AGE_SECONDS` is also
    # satisfied by a negative age, so a future-stamped `RR` would read as the
    # freshest reading there is and derive `pouring` from an hour that has not
    # happened yet. The fetch path cannot produce one -- the INCA request is
    # bounded `end=now` with the same `now` passed to this merge -- but this
    # is a pure function with its own `now` argument, and nothing in its
    # signature enforces that pairing. `observed_at` below already clamps
    # every rung to `now` on the same principle: an observation can never be
    # in the future.
    rr_1h_age_s = (now - rr_1h_time).total_seconds() if rr_1h_time is not None else None
    rr_1h_is_current = (
        rr_1h_age_s is not None and 0 <= rr_1h_age_s <= INCA_RR_MAX_AGE_SECONDS
    )
    # Only the nowcast decides while it reports either `pt` or `rr`: a `pt` of
    # 255 is an observed "dry" that an hour-old `RR` must not overrule. Without
    # it, only a *wet* `RR` stands in. A dry one would demote a rain or thunder
    # symbol to `cloudy`, but it describes an hour that ended up to ~90 min ago
    # in ordinary publishing, so it cannot rule out rain that has started
    # since; with no nowcast, "dry" is never observed and the symbol stands.
    nowcast_observes = precipitation_type is not None or nowcast_rate_mm_h is not None
    rr_1h_is_wet = rr_1h_is_current and rr_1h is not None and rr_1h >= PRECIP_MIN_MM
    # `None` when nothing observes precipitation, which the condition
    # derivation tells apart from an observed "dry".
    rate_mm_h = (
        nowcast_rate_mm_h if nowcast_observes else (rr_1h if rr_1h_is_wet else None)
    )
    # A single bucket can round to 0.0 in the gap between cells of an active
    # storm, reporting 0 mm/h mid-downpour and starving the `pouring` branch of
    # `condition.derive_current_condition`. So once the nowcast's own `pt` code
    # says it is precipitating, take the peak across the last RATE_LOOKBACK
    # rather than the matched bucket alone.
    #
    # INCA's hourly `RR` is deliberately not used here. It is a total over the
    # whole past hour, so substituting it for an instantaneous rate reports
    # rain that has already stopped: 6 mm falling in the first 20 minutes and
    # ending, with drizzle keeping `pt` non-zero, would read as 6 mm/h and
    # derive `pouring` from a drizzling sky.
    if (
        nowcast is not None
        and precipitation_type is not None
        and precipitation_type != PT_NO_PRECIPITATION
    ):
        recent = [
            value
            for ts, value in zip(nowcast.timestamps, nowcast.series("rr"), strict=True)
            if now - RATE_LOOKBACK <= ts <= now and value is not None
        ]
        if recent:
            rate_mm_h = max(rate_mm_h or 0.0, max(recent) * NOWCAST_BUCKETS_PER_HOUR)
    night = is_night(latitude, longitude, now)

    # When these conditions actually describe: the stamp of whichever source
    # supplied the *temperature*, the field the reading is judged by. Every
    # rung follows that one field. Anchoring to any other lets one field's
    # freshness vouch for another's -- an analysis carrying RR but no T2M
    # would otherwise report a time the temperature never had, and does so in
    # whichever direction happens to be wrong.
    #
    # INCA publishes ~30 min after the hour it analyses and the previous slice
    # is served until the next appears, so this can trail real time by ~90 min.
    # The nowcast runs every 15 min; its own bucket stamp is the honest answer
    # there, not `now` -- no source ever states `now`. Outside the nowcast grid
    # every field comes from an AROME row instead, stamped at the top of its
    # hour and up to an hour old.
    #
    # Both of those rungs are clamped to `now`, because an observation time can
    # never be in the future. The nowcast needs it as much as the AROME row
    # does: `nearest_index` matches the *nearest* bucket, not the nearest one
    # in the past, so at 15:38 it selects the 15:45 bucket.
    nowcast_t2m_index = nowcast_index
    if nowcast_t2m_index is not None and (
        nowcast.value_at("t2m", nowcast_t2m_index) is None
    ):
        nowcast_t2m_index = None

    if inca_t2m is not None:
        observed_at = inca_t2m_at
    elif nowcast_t2m_index is not None:
        observed_at = min(nowcast.timestamps[nowcast_t2m_index], now)
    elif arome_current:
        observed_at = min(arome_current.get("time", now), now)
    else:
        observed_at = now

    return {
        "observed_at": observed_at,
        "temperature_c": temperature,
        "apparent_temperature_c": apparent_temperature(
            temperature, humidity, wind_speed
        ),
        "humidity_pct": humidity,
        "pressure_hpa": round(p0 / 100.0, 1) if p0 is not None else None,
        "wind_speed_ms": wind_speed,
        "wind_bearing_deg": chain(
            inca_wind_bearing, now_value("dd"), arome_field("wind_bearing_deg")
        ),
        "wind_gust_ms": gust,
        "precipitation_1h_mm": rr_1h,
        # The hour this total covers ends at its stamp: `RR` is the
        # accumulation over the hour it is stamped for.
        "precipitation_1h_at": rr_1h_time if rr_1h is not None else None,
        "precipitation_type": precipitation_type,
        # Decoded against the condition's own rate, so "no precipitation"
        # never sits beside a rainy condition (see derive_precipitation_type).
        "precipitation_kind": derive_precipitation_type(precipitation_type, rate_mm_h),
        "cloud_cover_pct": arome_field("cloud_cover_pct"),
        "weather_symbol": symbol,
        "condition": derive_current_condition(
            precipitation_type=precipitation_type,
            precipitation_rate_mm_h=rate_mm_h,
            temperature=temperature,
            symbol=symbol,
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
    which the server renders as an out-of-coverage notice. Other AROME errors
    propagate. The result carries a ``sources`` list of the contributing
    datasets.
    """
    now = now or datetime.now(UTC)
    inca_start = now - timedelta(hours=INCA_LOOKBACK_HOURS)
    # Anchored exactly as the hourly fetch is, and for the same reason: where
    # an unbounded request begins is the API's choice, and if it is past the
    # current hour `_arome_current` would take cloud, CAPE and the weather
    # symbol from a later row and present them as current. See
    # HOURLY_LOOKBACK_HOURS for why the anchor is the top of the hour rather
    # than `now`.
    arome_start = now.replace(minute=0, second=0, microsecond=0) - timedelta(
        hours=HOURLY_LOOKBACK_HOURS
    )
    # Anchored to the 15-min grid for the same reason: the API rounds a
    # mid-interval `start` up to the next stamp, which would drop the bucket
    # covering `now` along with everything before it. Unbounded, the endpoint
    # begins at that bucket and the series carries a single stamp at/before
    # now, which silently reduces the RATE_LOOKBACK peak below to the matched
    # bucket it exists to widen.
    nowcast_start = (
        now.replace(minute=now.minute - now.minute % 15, second=0, microsecond=0)
        - NOWCAST_LOOKBACK
    )

    arome_res, nowcast_res, inca_res = await asyncio.gather(
        async_get_timeseries(
            session,
            *DATASET_AROME,
            AROME_PARAMETERS,
            latitude,
            longitude,
            start=arome_start,
        ),
        async_get_timeseries(
            session,
            *DATASET_NOWCAST,
            NOWCAST_PARAMETERS,
            latitude,
            longitude,
            start=nowcast_start,
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
    _warn_unknown_pt_code(merged["precipitation_type"])
    _warn_unknown_sy_code(merged["weather_symbol"])
    merged["sources"] = sources
    return merged


async def async_fetch_hourly_forecast(
    session: aiohttp.ClientSession,
    latitude: float,
    longitude: float,
    hours: int = 24,
    start: datetime | None = None,
    now: datetime | None = None,
) -> dict[str, Any]:
    """Fetch AROME + C-LAEF ensemble concurrently and assemble the hourly forecast.

    Ensemble failure just omits the precipitation probability and the
    ensemble's thunderstorm signal. Raises :class:`GeoSphereOutOfDomainError`
    only when AROME is out of domain, which the server renders as an
    out-of-coverage notice; other AROME errors propagate. The result carries a
    ``sources`` list.
    """
    now = now or datetime.now(UTC)

    # The first hour the assembly will keep: the hour in progress, or the
    # caller's `start` when that is later.
    window_start = now.replace(minute=0, second=0, microsecond=0)
    if start is not None and start > window_start:
        window_start = start

    # Anchored to the top of the hour, plus an hour of margin — see
    # HOURLY_LOOKBACK_HOURS. The anchor is the part that matters: the API
    # rounds a mid-hour `start` up to the next whole stamp, so anchoring to
    # `now` at 19:33 would come back at 20:00 and drop the hour under way.
    series_start = window_start.replace(minute=0, second=0, microsecond=0) - timedelta(
        hours=HOURLY_LOOKBACK_HOURS
    )
    # And no further than the caller asked for: `hours=6` has no use for the
    # other ~54 h of the AROME horizon. Two hours of slack past the last
    # requested hour: one because its interval fields (gust, precipitation,
    # weather symbol) live on the *following* stamp, and one so rounding at the boundary
    # cannot clip that successor. The storm outlook asks for AROME_MAX_HOURS,
    # so its scan still spans the full horizon — where the slack buys nothing,
    # since the run itself ends there and the assembly drops its successorless
    # final stamp. `horizon_hours` reports what was actually scanned, so an
    # all-clear stays honest about covering an hour less than the run.
    series_end = window_start + timedelta(hours=max(hours, 1) + 2)

    arome_res, ensemble_res = await asyncio.gather(
        async_get_timeseries(
            session,
            *DATASET_AROME,
            AROME_PARAMETERS,
            latitude,
            longitude,
            start=series_start,
            end=series_end,
        ),
        async_get_timeseries(
            session,
            *DATASET_ENSEMBLE,
            ENSEMBLE_PARAMETERS,
            latitude,
            longitude,
            start=series_start,
            end=series_end,
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
    for row in assembled["hourly"]:
        _warn_unknown_sy_code(row["weather_symbol"])
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
