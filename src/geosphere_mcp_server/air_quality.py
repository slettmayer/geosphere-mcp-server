"""Pure processing + high-level fetch helper for GeoSphere air quality.

``merge_air_quality`` takes already-fetched :class:`GeoSphereResponse` objects
and is fully testable without I/O; ``async_fetch_air_quality`` orchestrates the
two concurrent HTTP calls and applies the graceful-degradation rule.

Ported from the ha-geosphere-next air-quality coordinator.
"""

from __future__ import annotations

import asyncio
import logging
from datetime import UTC, datetime
from typing import Any
from zoneinfo import ZoneInfo

import aiohttp

from geosphere_mcp_server.const import (
    CHEM_AQI_PARAMETERS,
    CHEM_PARAMETERS,
    CHEM_POLLUTANTS,
    DATASET_CHEM,
    DATASET_CHEM_AQI,
    GEOSPHERE_TZ,
)
from geosphere_mcp_server.geosphere_api import (
    GeoSphereApiError,
    GeoSphereOutOfDomainError,
    GeoSphereResponse,
    async_get_timeseries,
)

_LOGGER = logging.getLogger(__name__)


def merge_air_quality(
    chem: GeoSphereResponse,
    aqi: GeoSphereResponse | None,
    now: datetime,
) -> dict[str, Any]:
    """Merge the WRF-Chem pollutant series and the daily AQI into one dict.

    Pollutant concentrations (µg/m³) are read at the forecast hour nearest to
    ``now``, and ``observed_at`` reports that hour's own stamp clamped to the
    present: the match is nearest in *either* direction, so from HH:31 onward
    the closest stamp is the hour ahead, and an observation time can never be
    in the future. The daily AQI is the EEA band index (1-6) for today /
    tomorrow / in 2 days, matched by local calendar day — the daily stamps are
    00:00 UTC, which is the previous day in the Alpine zone for part of the
    year.
    """
    pollutants: dict[str, float | None] = dict.fromkeys(CHEM_POLLUTANTS)
    observed_at: datetime | None = None

    index = chem.nearest_index(now)
    if index is not None:
        observed_at = min(chem.timestamps[index], now)
        for key, parameter in CHEM_POLLUTANTS.items():
            pollutants[key] = chem.value_at(parameter, index)

    bands: dict[int, int] = {}
    if aqi is not None:
        tz = ZoneInfo(GEOSPHERE_TZ)
        today = now.astimezone(tz).date()
        for ts, value in zip(aqi.timestamps, aqi.series("aqi"), strict=True):
            if value is not None:
                bands[(ts.astimezone(tz).date() - today).days] = int(value)

    return {
        "observed_at": observed_at,
        "reference_time": chem.reference_time,
        "grid_latitude": chem.grid_latitude,
        "grid_longitude": chem.grid_longitude,
        "pollutants": pollutants,
        # EEA band index (1-6). GeoSphere publishes no underlying numeric
        # index, so the band is the whole figure the renderer has to work with.
        "aqi_band_today": bands.get(0),
        "aqi_band_tomorrow": bands.get(1),
        "aqi_band_in_2_days": bands.get(2),
    }


async def async_fetch_air_quality(
    session: aiohttp.ClientSession,
    latitude: float,
    longitude: float,
    now: datetime | None = None,
) -> dict[str, Any]:
    """Fetch the WRF-Chem pollutants and the daily AQI concurrently, and merge.

    The daily AQI is a nice-to-have on top of the concentrations: its failure
    logs a warning and keeps the pollutants, while a ``chem`` failure
    propagates. Raises :class:`GeoSphereOutOfDomainError` only when ``chem``
    itself is out of domain, which the server renders as an out-of-coverage
    notice. The result carries a ``sources`` list of the contributing datasets.
    """
    now = now or datetime.now(UTC)

    chem_res, aqi_res = await asyncio.gather(
        async_get_timeseries(
            session, *DATASET_CHEM, CHEM_PARAMETERS, latitude, longitude
        ),
        async_get_timeseries(
            session, *DATASET_CHEM_AQI, CHEM_AQI_PARAMETERS, latitude, longitude
        ),
        return_exceptions=True,
    )

    if isinstance(chem_res, BaseException):
        raise chem_res
    chem: GeoSphereResponse = chem_res

    aqi: GeoSphereResponse | None = None
    if isinstance(aqi_res, GeoSphereOutOfDomainError):
        _LOGGER.debug("Daily AQI outside coverage, keeping pollutants only")
    elif isinstance(aqi_res, GeoSphereApiError):
        _LOGGER.warning("AQI fetch failed, keeping pollutants only: %s", aqi_res)
    elif isinstance(aqi_res, BaseException):
        raise aqi_res
    else:
        aqi = aqi_res

    merged = merge_air_quality(chem, aqi, now)
    merged["sources"] = ["WRF-Chem"] + (["daily AQI"] if aqi is not None else [])
    return merged
