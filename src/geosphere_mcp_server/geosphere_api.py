"""Async client for the GeoSphere Austria Dataset API.

Module-level async functions (oebb-style) — no client class. The parsed
response dataclasses live here too so this module and ``condition.py`` form a
homeassistant-free, PyPI-extractable core. These functions raise typed
exceptions; the server layer converts them to user-facing text.
"""

from __future__ import annotations

import asyncio
import contextlib
import logging
from dataclasses import dataclass
from datetime import datetime
from typing import Any

import aiohttp

from geosphere_mcp_server.const import GEOSPHERE_API_BASE_URL, GEOSPHERE_TIMEOUT

_LOGGER = logging.getLogger(__name__)


class GeoSphereApiError(Exception):
    """Base error talking to the GeoSphere API."""


class GeoSphereConnectionError(GeoSphereApiError):
    """Network-level failure."""


class GeoSphereRateLimitError(GeoSphereApiError):
    """HTTP 429 — request budget exceeded (5 req/s, 240 req/h)."""

    def __init__(self, message: str, retry_after: float | None = None) -> None:
        super().__init__(message)
        self.retry_after = retry_after


class GeoSphereOutOfDomainError(GeoSphereApiError):
    """Requested point lies outside the dataset's grid bounds."""


@dataclass(slots=True)
class ParameterSeries:
    """One parameter's series within a timeseries response."""

    name: str
    unit: str
    data: list[float | None]


@dataclass(slots=True)
class GeoSphereResponse:
    """Parsed GeoJSON timeseries response."""

    resource_id: str
    reference_time: datetime | None
    timestamps: list[datetime]
    parameters: dict[str, ParameterSeries]
    grid_longitude: float
    grid_latitude: float

    def series(self, name: str) -> list[float | None]:
        """Return a parameter's series, or an all-None series if absent."""
        if name not in self.parameters:
            return [None] * len(self.timestamps)
        return self.parameters[name].data

    def value_at(self, name: str, index: int) -> float | None:
        """Return a single parameter value by index, or None if out of range."""
        data = self.series(name)
        if 0 <= index < len(data):
            return data[index]
        return None


async def async_get_timeseries(
    session: aiohttp.ClientSession,
    mode: str,
    resource_id: str,
    parameters: tuple[str, ...],
    latitude: float,
    longitude: float,
    start: datetime | None = None,
    end: datetime | None = None,
    base_url: str = GEOSPHERE_API_BASE_URL,
) -> GeoSphereResponse:
    """Fetch a point timeseries and parse the GeoJSON response.

    Raises GeoSphereRateLimitError (429, with retry_after from the Retry-After
    header), GeoSphereOutOfDomainError (400 "outside of dataset bounds"),
    GeoSphereConnectionError (timeout / network), or GeoSphereApiError.
    """
    url = f"{base_url.rstrip('/')}/timeseries/{mode}/{resource_id}"
    query: dict[str, str] = {
        "parameters": ",".join(parameters),
        "lat_lon": f"{latitude},{longitude}",
        "output_format": "geojson",
    }
    if start is not None:
        query["start"] = start.strftime("%Y-%m-%dT%H:%M")
    if end is not None:
        query["end"] = end.strftime("%Y-%m-%dT%H:%M")

    try:
        async with asyncio.timeout(GEOSPHERE_TIMEOUT):
            resp = await session.get(url, params=query)
            if resp.status == 429:
                retry_after = resp.headers.get("Retry-After")
                raise GeoSphereRateLimitError(
                    "GeoSphere API rate limit exceeded",
                    retry_after=float(retry_after) if retry_after else None,
                )
            if resp.status == 400:
                detail = ""
                with contextlib.suppress(aiohttp.ClientError, ValueError):
                    detail = str((await resp.json()).get("detail", ""))
                if "outside of dataset bounds" in detail:
                    raise GeoSphereOutOfDomainError(detail)
                raise GeoSphereApiError(
                    f"GeoSphere API rejected the request: {detail or resp.status}"
                )
            if resp.status >= 400:
                raise GeoSphereApiError(
                    f"GeoSphere API returned HTTP {resp.status} for {resource_id}"
                )
            body = await resp.json()
    except (TimeoutError, aiohttp.ClientError) as err:
        raise GeoSphereConnectionError(
            f"Error connecting to the GeoSphere API: {err}"
        ) from err

    return _parse_geojson(resource_id, body)


def _parse_geojson(resource_id: str, body: dict[str, Any]) -> GeoSphereResponse:
    """Parse the verified GeoJSON timeseries shape into a typed response."""
    try:
        feature = body["features"][0]
        raw_parameters = feature["properties"]["parameters"]
        parameters = {
            name: ParameterSeries(
                name=name,
                unit=str(param.get("unit", "")),
                data=list(param["data"]),
            )
            for name, param in raw_parameters.items()
        }
        reference_time = (
            datetime.fromisoformat(body["reference_time"])
            if body.get("reference_time")
            else None
        )
        return GeoSphereResponse(
            resource_id=resource_id,
            reference_time=reference_time,
            timestamps=[datetime.fromisoformat(ts) for ts in body["timestamps"]],
            parameters=parameters,
            grid_longitude=float(feature["geometry"]["coordinates"][0]),
            grid_latitude=float(feature["geometry"]["coordinates"][1]),
        )
    except (KeyError, IndexError, TypeError, ValueError) as err:
        raise GeoSphereApiError(
            f"Unexpected GeoSphere API response shape for {resource_id}: {err}"
        ) from err
