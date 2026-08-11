"""Forecast-outlook derivation — pure functions, fully testable.

Scans an assembled hourly series for the facts a storm question needs: peak
gusts within a horizon, and when thunder is next expected. Deliberately
threshold-free — what counts as "too windy" is the caller's policy, not this
server's.

Operates on the row dicts produced by :func:`weather.assemble_hourly_forecast`
and :func:`format.normalize_hourly_openmeteo`, so one implementation serves
both source paths. Rows are read through ``.get``: an Open-Meteo row carries no
``cin_jkg`` at all, which reads as "uncapped" (see :func:`condition.is_thunder`).

Gust values are returned in m/s, matching the rows themselves.

Ported from ha-geosphere-next, where the same functions read ``HourlyForecast``
dataclass attributes. The split differs there: here :func:`window` is applied
once by the caller and handed to the window readers, and the thunderstorm scan
returns its own decidability, so one series is walked three times rather than
six.
"""

from __future__ import annotations

from datetime import datetime, timedelta
from typing import Any

from geosphere_mcp_server.condition import is_thunder
from geosphere_mcp_server.const import CONDITION_LIGHTNING, PRECIP_MIN_MM

# Both source paths deliver hourly rows, so the hour a row describes runs from
# its own stamp to one step later. Used to find the row covering `now` without
# assuming anything about where the stamps sit on the clock — see `_from`.
_STEP = timedelta(hours=1)


def _from(now: datetime) -> datetime:
    """Exclusive lower bound selecting the hour already under way, and later.

    A row counts once its hour has not yet ended: ``row + _STEP > now``, i.e.
    ``row > now - _STEP``. Deliberately *not* ``now.replace(minute=0)`` — that
    assumes rows are stamped on whole UTC hours, which is only true of the
    GeoSphere path. Open-Meteo stamps rows in the point's local hour, so after
    the conversion to UTC a zone offset by :30 or :45 (India, Iran, Nepal,
    Myanmar, central Australia, the Chatham Islands) puts every row half an
    hour off the UTC grid. Flooring then lands *above* the in-progress row and
    drops it — silently turning a storm under way into an all-clear.
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
    real hours across a transition. Callers whose source is local (Open-Meteo)
    convert to UTC first and localize only what they render. The stamps need
    not align with the UTC hour grid, though — see :func:`_from`.
    """
    start = _from(now)
    end = now + timedelta(hours=hours)
    return [
        row
        for row in rows
        if row.get("time") is not None and start < row["time"] <= end
    ]


def _is_lightning(row: dict[str, Any]) -> bool:
    """True when the hour reads as a thunderstorm hour.

    Two branches, because the derived condition alone misses real storms:

    a) the derived condition starts with "lightning" — the primary signal, and
       already a *considered* thunder verdict: on the GeoSphere path
       `derive_condition` only reaches it with cloud cover at or above
       `WINDY_CLOUD_TCC_PCT` on top of the CAPE/CIN gate, and on the
       Open-Meteo path it comes from the model's own WMO thunderstorm code; or
    b) the raw CAPE/CIN thunder predicate holds *and* the hour is forecast to
       produce precipitation. `derive_condition` returns `snowy` /
       `snowy-rainy` before it ever looks at thunder (thundersnow) and returns
       `None` when cloud cover is missing, so those hours would otherwise read
       as "no storm".

    Only branch (b) requires precipitation, and the asymmetry is deliberate
    rather than an oversight: it is the substitute for the cloud-cover
    corroboration branch (a) gets for free. Branch (b) fires precisely on the
    hours whose cloud signal is absent or was consumed by a snow verdict, and
    without a second signal a bare CAPE reading would raise a storm on every
    routine dry-convective summer afternoon. A dry high-CAPE hour under heavy
    cloud does still count, via branch (a) — that is a plausible pre-storm
    hour, not a quiet one.
    """
    condition = row.get("condition")
    # `lightning` is also the prefix of `lightning-rainy`, so one `startswith`
    # catches both. Taken from const.py rather than restated here, so a rename
    # of the condition vocabulary cannot leave this predicate matching nothing.
    if condition is not None and condition.startswith(CONDITION_LIGHTNING):
        return True
    precipitation = row.get("precipitation_mm") or 0.0
    return (
        is_thunder(row.get("cape_jkg"), row.get("cin_jkg"))
        and precipitation >= PRECIP_MIN_MM
    )


def _is_decidable(row: dict[str, Any]) -> bool:
    """True when the hour carries enough data to judge thunder either way.

    An hour with neither a derived condition nor CAPE says nothing about
    thunder — "no storm" would be a guess, not an answer.
    """
    return row.get("condition") is not None or row.get("cape_jkg") is not None


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
    """Peak CAPE (J/kg) over an already-windowed series."""
    values = [row["cape_jkg"] for row in rows if row.get("cape_jkg") is not None]
    return max(values) if values else None


def thunderstorm_outlook(rows: list[dict[str, Any]]) -> bool | None:
    """Tri-state thunderstorm outlook over an already-windowed series.

    ``True`` / ``False`` when the window holds hours that can be judged, and
    ``None`` when it holds none at all or none that are decidable — so a data
    gap is reported as "unknown" instead of a confident "no".
    """
    decidable = False
    for row in rows:
        if _is_lightning(row):
            return True
        decidable = decidable or _is_decidable(row)
    return False if decidable else None


def scan_thunderstorm(
    rows: list[dict[str, Any]], now: datetime
) -> tuple[datetime | None, float | None, bool]:
    """First hour with thunder expected, its CAPE, and whether the scan counts.

    Scans the full series rather than a window — "no storm for two days" and
    "storm in 40 hours" are both useful answers.

    The third element is what separates "no storm ahead" from "nothing here can
    be read": both return ``(None, None, ...)`` for the first two, and a caller
    that reports an all-clear without it will assert one over an unreadable
    series. Returning it from the same pass is what keeps the two answers from
    drifting apart.

    The scan starts at the hour already under way (see :func:`_from`), so when
    the storm hour is that one the returned timestamp is in the past — by up to
    59 minutes. That is intentional: it means "storm in progress". Downstream
    lead-time math must treat a non-positive lead time as "now" rather than
    assuming the timestamp is always in the future.
    """
    start = _from(now)
    decidable = False
    for row in rows:
        when = row.get("time")
        if when is None or when <= start:
            continue
        if _is_lightning(row):
            return when, row.get("cape_jkg"), True
        decidable = decidable or _is_decidable(row)
    return None, None, decidable


def horizon_hours(rows: list[dict[str, Any]], now: datetime) -> int | None:
    """Hours of forecast left ahead of ``now``, or None for an empty series.

    What an all-clear actually covers: the two source paths hand the outlook
    series of quite different lengths, so "none in the forecast horizon" is
    only honest next to the horizon it was scanned over.
    """
    times = [row["time"] for row in rows if row.get("time") is not None]
    if not times:
        return None
    return max(0, round((max(times) - now).total_seconds() / 3600))
