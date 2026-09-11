"""Integration tests against the live GeoSphere API.

Marked ``integration`` and excluded from CI (``-m "not integration"``); run
locally with ``pytest -m integration``. Three coverage classes are exercised:
Vienna (full GeoSphere: INCA + nowcast + AROME), Munich (AROME domain but
outside Austria: degraded GeoSphere), and Lisbon (outside the AROME domain,
and so outside what this server serves at all).
"""

from __future__ import annotations

import pytest

from geosphere_mcp_server.server import (
    OUT_OF_DOMAIN_MESSAGE,
    get_air_quality,
    get_current_weather,
    get_hourly_forecast,
    get_storm_outlook,
)

VIENNA = (48.2208, 16.3738)
MUNICH = (48.1372, 11.5755)
LISBON = (38.7223, -9.1393)

pytestmark = pytest.mark.integration


def _assert_rendered(result: str) -> None:
    assert result.startswith("#"), result
    assert "⚠️" not in result, result


# --- Vienna: full GeoSphere coverage ---


@pytest.mark.asyncio
async def test_current_weather_vienna_uses_geosphere() -> None:
    result = await get_current_weather(*VIENNA)
    _assert_rendered(result)
    assert "GeoSphere" in result
    assert "Temperature" in result
    # The source line names every contributing dataset, so this pins the
    # nowcast as actually present. Without it a rejected request degrades
    # silently -- `_optional_response` swallows any GeoSphereApiError -- and
    # every assertion above still passes while the gust, the precipitation
    # type and `is_precipitating` quietly vanish. The nowcast call carries an
    # anchored `start`; this is what fails loudly if the API ever stops
    # honouring one reaching past the serving run's t0.
    assert "nowcast" in result, result


@pytest.mark.asyncio
async def test_hourly_forecast_vienna_uses_geosphere() -> None:
    result = await get_hourly_forecast(*VIENNA, hours=6)
    _assert_rendered(result)
    assert "AROME" in result


@pytest.mark.asyncio
async def test_storm_outlook_vienna_uses_geosphere() -> None:
    result = await get_storm_outlook(*VIENNA)
    _assert_rendered(result)
    assert "AROME" in result


@pytest.mark.asyncio
async def test_air_quality_vienna_uses_geosphere() -> None:
    result = await get_air_quality(*VIENNA)
    _assert_rendered(result)
    assert "WRF-Chem" in result


# --- Munich: AROME domain, outside Austria (degraded GeoSphere) ---


@pytest.mark.asyncio
async def test_current_weather_munich_degrades_gracefully() -> None:
    result = await get_current_weather(*MUNICH)
    _assert_rendered(result)
    assert "Temperature" in result


@pytest.mark.asyncio
async def test_hourly_forecast_munich() -> None:
    result = await get_hourly_forecast(*MUNICH, hours=6)
    _assert_rendered(result)


# --- Lisbon: outside the AROME domain (not served) ---
#
# These pin the live API's out-of-domain signal, which is the whole basis of
# the coverage message: GeoSphere answers HTTP 400 rather than an empty
# series, and only the client's mapping of that to GeoSphereOutOfDomainError
# keeps an uncovered point from reading as a generic failure.


@pytest.mark.asyncio
@pytest.mark.parametrize(
    "tool",
    [get_current_weather, get_hourly_forecast, get_storm_outlook, get_air_quality],
    ids=["current", "hourly", "outlook", "air_quality"],
)
async def test_lisbon_is_out_of_coverage(tool) -> None:
    assert await tool(*LISBON) == OUT_OF_DOMAIN_MESSAGE
