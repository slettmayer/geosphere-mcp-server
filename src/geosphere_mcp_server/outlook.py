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
dataclass attributes.
"""

from __future__ import annotations

from datetime import datetime, timedelta
from typing import Any

from geosphere_mcp_server.condition import is_thunder
from geosphere_mcp_server.const import PRECIP_MIN_MM

# A condition string starting with this prefix means the derivation decided
# thunder is likely ("lightning" and "lightning-rainy").
_LIGHTNING_PREFIX = "lightning"


def _window(rows: list[dict[str, Any]], hours: int, now: datetime) -> list[dict]:
    """Hours from the top of the current hour through ``now + hours``.

    The series' first entry is the in-progress hour, stamped at the top of the
    hour and therefore earlier than ``now`` — it must still count.

    The horizon therefore rounds *up* to whole hourly steps: the start floors
    to the top of the current hour while the end stays at ``now + hours``, so an
    ``hours``-hour window always spans ``hours + 1`` hourly stamps — the
    in-progress hour plus the next ``hours``. A 1-hour window consequently
    covers the current hour and the next one, and can report an event up to
    ~2 h ahead. Callers that need a strict "within the next 60 minutes" answer
    must compare timestamps themselves.
    """
    start = now.replace(minute=0, second=0, microsecond=0)
    end = now + timedelta(hours=hours)
    return [
        row
        for row in rows
        if row.get("time") is not None and start <= row["time"] <= end
    ]


def hour_at(rows: list[dict[str, Any]], now: datetime) -> dict[str, Any] | None:
    """The forecast hour covering ``now`` — the in-progress hour.

    Matches on the top-of-hour floor of ``now``, because the series is stamped
    at the top of each hour and its first entry is the hour already under way
    (so it is earlier than ``now`` by up to 59 minutes).

    Returns ``None`` when the series does not cover ``now`` at all — e.g. a
    forecast that has aged out entirely, or one that has not reached ``now``
    yet.
    """
    start = now.replace(minute=0, second=0, microsecond=0)
    for row in rows:
        if row.get("time") == start:
            return row
    return None


def _is_lightning(row: dict[str, Any]) -> bool:
    """True when the hour reads as a thunderstorm hour.

    Two branches, because the derived condition alone misses real storms:

    a) the derived condition starts with "lightning" — the model's own
       judgement that convection is occurring, and the primary signal; or
    b) the raw CAPE/CIN thunder predicate holds *and* the hour is forecast to
       produce precipitation. `derive_condition` returns `snowy` /
       `snowy-rainy` before it ever looks at thunder (thundersnow) and returns
       `None` when cloud cover is missing, so those hours would otherwise read
       as "no storm".

    Branch (b) deliberately requires precipitation: dry convective CAPE under
    low cloud is a routine summer afternoon and must not raise a storm signal.
    """
    condition = row.get("condition")
    if condition is not None and condition.startswith(_LIGHTNING_PREFIX):
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


def max_gust(
    rows: list[dict[str, Any]], hours: int, now: datetime
) -> tuple[float | None, datetime | None]:
    """Peak gust (m/s) within the horizon and the hour it falls in.

    The horizon rounds up to whole hourly steps — see :func:`_window`.
    """
    best_value: float | None = None
    best_time: datetime | None = None
    for row in _window(rows, hours, now):
        gust = row.get("wind_gust_ms")
        if gust is None:
            continue
        if best_value is None or gust > best_value:
            best_value = gust
            best_time = row["time"]
    return best_value, best_time


def max_cape(rows: list[dict[str, Any]], hours: int, now: datetime) -> float | None:
    """Peak CAPE (J/kg) within the horizon (rounds up to whole hourly steps)."""
    values = [
        row["cape_jkg"]
        for row in _window(rows, hours, now)
        if row.get("cape_jkg") is not None
    ]
    return max(values) if values else None


def next_thunderstorm(
    rows: list[dict[str, Any]], now: datetime
) -> tuple[datetime | None, float | None]:
    """First hour with thunder expected, and that hour's CAPE.

    Scans the full horizon rather than a window — "no storm for two days" and
    "storm in 40 hours" are both useful answers.

    The scan starts at the top of the *current* hour, so when the storm hour is
    the one already under way the returned timestamp is in the past — by up to
    59 minutes. That is intentional: it means "storm in progress". Downstream
    lead-time math must treat a non-positive lead time as "now" rather than
    assuming the timestamp is always in the future.
    """
    start = now.replace(minute=0, second=0, microsecond=0)
    for row in rows:
        when = row.get("time")
        if when is None or when < start:
            continue
        if _is_lightning(row):
            return when, row.get("cape_jkg")
    return None, None


def thunderstorm_outlook(
    rows: list[dict[str, Any]], hours: int, now: datetime
) -> bool | None:
    """Tri-state thunderstorm outlook for the horizon.

    ``True`` / ``False`` when the window holds hours that can be judged, and
    ``None`` when it holds none at all or none that are decidable — so a data
    gap is reported as "unknown" instead of a confident "no".
    """
    window = _window(rows, hours, now)
    if any(_is_lightning(row) for row in window):
        return True
    if not any(_is_decidable(row) for row in window):
        return None
    return False
