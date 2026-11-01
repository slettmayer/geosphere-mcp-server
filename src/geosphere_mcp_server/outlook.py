"""Forecast-outlook derivation — pure functions, fully testable.

Scans an assembled hourly series for the facts a storm question needs: peak
gusts within a horizon, and when thunder is next expected. Gusts are
deliberately threshold-free — what counts as "too windy" is the caller's
policy, not this server's.

A thunderstorm hour is one whose weather symbol is a thunderstorm code, or
whose current C-LAEF ensemble run allows a storm. Either suffices: a false
alarm costs less than a miss.

Operates on the row dicts produced by :func:`weather.assemble_hourly_forecast`,
read through ``.get`` so a row missing a field is judged on what it carries.
Gust values are returned in m/s, matching the rows themselves.

Ported from ha-geosphere-next, where the same functions read ``HourlyForecast``
dataclass attributes. Here :func:`window` is applied once by the caller and
handed to the window readers, so one series is walked three times, not six.
"""

from __future__ import annotations

from datetime import datetime, timedelta
from typing import Any

from geosphere_mcp_server.condition import is_thunder_symbol
from geosphere_mcp_server.const import (
    ENSEMBLE_MAX_AGE,
    PRECIP_MIN_MM,
    THUNDER_CAPE_JKG,
    THUNDER_SOURCE_BOTH,
    THUNDER_SOURCE_ENSEMBLE,
    THUNDER_SOURCE_SYMBOL,
)

# Both source paths deliver hourly rows, so the hour a row describes runs from
# its own stamp to one step later. Used to find the row covering `now` without
# assuming anything about where the stamps sit on the clock — see `_from`.
_STEP = timedelta(hours=1)


def _from(now: datetime) -> datetime:
    """Exclusive lower bound selecting the hour already under way, and later.

    A row counts once its hour has not yet ended: ``row + _STEP > now``, i.e.
    ``row > now - _STEP``. Deliberately *not* ``now.replace(minute=0)``: that
    form only works while every stamp sits on a whole UTC hour, and it fails
    in the one direction that matters — landing *above* an in-progress row
    stamped off the hour grid and dropping it, silently turning a storm under
    way into an all-clear. The forecast does stamp on whole UTC hours, so this
    costs nothing; it just does not make the assumption load-bearing.
    """
    return now - _STEP


def window(rows: list[dict[str, Any]], hours: int, now: datetime) -> list[dict]:
    """Hours from the one already under way through ``now + hours``.

    The series' first entry is the in-progress hour, stamped at the start of
    the hour and therefore earlier than ``now`` — it must still count.

    The horizon therefore rounds *up* to whole hourly steps: the start reaches
    back to the hour covering ``now`` while the end stays at ``now + hours``,
    so an ``hours``-hour window always spans ``hours + 1`` hourly stamps — the
    in-progress hour plus the next ``hours``. A 1-hour window consequently
    covers the current hour and the next one, and can report an event up to
    ~2 h ahead. Callers that need a strict "within the next 60 minutes" answer
    must compare timestamps themselves.

    ``rows`` and ``now`` must share a **fixed-offset** zone — in practice both
    UTC. ``now + timedelta`` is wall-clock arithmetic even on aware datetimes,
    so passing times in a DST-observing zone would make the horizon 11 or 13
    real hours across a transition. GeoSphere stamps are UTC and the derivation
    runs on them directly; only the reported timestamps are localized to Vienna
    for display. The stamps need not align with the UTC hour grid — see
    :func:`_from`.
    """
    start = _from(now)
    end = now + timedelta(hours=hours)
    return [
        row
        for row in rows
        if row.get("time") is not None and start < row["time"] <= end
    ]


def ensemble_is_current(reference_time: datetime | None, now: datetime) -> bool:
    """Whether an ensemble run may feed the thunderstorm signal at ``now``.

    Current while its ``reference_time`` is less than ENSEMBLE_MAX_AGE old. No
    run, or a run without a stamp, is not current.
    """
    return reference_time is not None and now - reference_time < ENSEMBLE_MAX_AGE


def _storm_signals(row: dict[str, Any], ensemble: bool) -> tuple[bool, bool]:
    """Which signals call the hour a thunderstorm: (symbol, ensemble).

    The symbol fires on GeoSphere's thunderstorm codes. The ensemble fires, if
    ``ensemble`` says its run is current, when its 90th percentiles reach
    THUNDER_CAPE_JKG of CAPE and PRECIP_MIN_MM of precipitation in the hour;
    CAPE alone is a routine dry summer afternoon.
    """
    cape = row.get("ensemble_cape_p90_jkg")
    precipitation = row.get("ensemble_precipitation_p90_mm")
    return is_thunder_symbol(row.get("weather_symbol")), (
        ensemble
        and cape is not None
        and cape >= THUNDER_CAPE_JKG
        and precipitation is not None
        and precipitation >= PRECIP_MIN_MM
    )


def _source(symbol: bool, ensemble: bool) -> str | None:
    """Name the signals that fired, ``None`` for neither."""
    if symbol and ensemble:
        return THUNDER_SOURCE_BOTH
    if symbol:
        return THUNDER_SOURCE_SYMBOL
    if ensemble:
        return THUNDER_SOURCE_ENSEMBLE
    return None


def max_gust(rows: list[dict[str, Any]]) -> tuple[float | None, datetime | None]:
    """Peak gust (m/s) over the given hours, and the hour it falls in.

    Takes an already-windowed series — see :func:`window`.
    """
    best_value: float | None = None
    best_time: datetime | None = None
    for row in rows:
        gust = row.get("wind_gust_ms")
        if gust is None:
            continue
        if best_value is None or gust > best_value:
            best_value = gust
            best_time = row["time"]
    return best_value, best_time


def max_cape(rows: list[dict[str, Any]]) -> float | None:
    """Peak deterministic CAPE (J/kg) over an already-windowed series.

    A diagnostic only: neither thunderstorm signal reads it.
    """
    values = [row["cape_jkg"] for row in rows if row.get("cape_jkg") is not None]
    return max(values) if values else None


def thunderstorm_outlook(
    rows: list[dict[str, Any]], ensemble: bool
) -> tuple[bool | None, str | None]:
    """Whether any hour of an already-windowed series is a thunderstorm hour.

    Returns the verdict and which signal fired, judged across the whole
    window. ``None`` (unknown) only when the window holds no hour at all; an
    hour whose symbol is missing or outside GeoSphere's table counts as no
    storm unless the ensemble says otherwise. ``ensemble`` is
    :func:`ensemble_is_current` for the run behind the rows.
    """
    if not rows:
        return None, None
    signals = [_storm_signals(row, ensemble) for row in rows]
    source = _source(
        any(symbol for symbol, _ in signals), any(ens for _, ens in signals)
    )
    return source is not None, source


def next_thunderstorm(
    rows: list[dict[str, Any]], now: datetime, ensemble: bool
) -> tuple[datetime | None, str | None]:
    """Start of the first thunderstorm hour, and which signal fired in it.

    Scans the full series rather than a window — "no storm for two days" and
    "storm in 40 hours" are both useful answers. The same per-row test as
    :func:`thunderstorm_outlook`, so whenever that reports a storm in its
    window, this names an hour inside it.

    The scan starts at the hour already under way (see :func:`_from`), so when
    the storm hour is that one the returned timestamp is in the past — by up to
    59 minutes. That is intentional: it means "storm in progress". Downstream
    lead-time math must treat a non-positive lead time as "now" rather than
    assuming the timestamp is always in the future.
    """
    start = _from(now)
    for row in rows:
        when = row.get("time")
        if when is None or when <= start:
            continue
        source = _source(*_storm_signals(row, ensemble))
        if source is not None:
            return when, source
    return None, None


def horizon_hours(rows: list[dict[str, Any]], now: datetime) -> int | None:
    """Hours of forecast left ahead of ``now``, or None for an empty series.

    What an all-clear actually covers. The forecast nominally runs ~60 h, but
    publication lag and a stale or truncated run hand over fewer hours, so
    "none in the forecast horizon" is only honest next to the horizon it was
    actually scanned over.
    """
    times = [row["time"] for row in rows if row.get("time") is not None]
    if not times:
        return None
    return max(0, round((max(times) - now).total_seconds() / 3600))
