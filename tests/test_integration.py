"""Integration tests against the live GeoSphere and Open-Meteo APIs.

Marked ``integration`` and excluded from CI (``-m "not integration"``); run
locally with ``pytest -m integration``. Three coverage classes are exercised:
Vienna (full GeoSphere: INCA + nowcast + AROME), Munich (AROME domain but
outside Austria: degraded GeoSphere), and Lisbon (outside the AROME domain:
Open-Meteo fallback).
"""

from __future__ import annotations

import pytest

from geosphere_mcp_server.server import (
    get_current_weather,
    get_daily_forecast,
    get_hourly_forecast,
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


@pytest.mark.asyncio
async def test_hourly_forecast_vienna_uses_geosphere() -> None:
    result = await get_hourly_forecast(*VIENNA, hours=6)
    _assert_rendered(result)
    assert "AROME" in result


@pytest.mark.asyncio
async def test_daily_forecast_vienna() -> None:
    result = await get_daily_forecast(*VIENNA, days=5)
    _assert_rendered(result)
    assert "Open-Meteo" in result


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


# --- Lisbon: outside the AROME domain (Open-Meteo fallback) ---


@pytest.mark.asyncio
async def test_current_weather_lisbon_falls_back_to_openmeteo() -> None:
    result = await get_current_weather(*LISBON)
    _assert_rendered(result)
    assert "Open-Meteo" in result


@pytest.mark.asyncio
async def test_hourly_forecast_lisbon_falls_back_to_openmeteo() -> None:
    result = await get_hourly_forecast(*LISBON, hours=6)
    _assert_rendered(result)
    assert "Open-Meteo" in result


@pytest.mark.asyncio
async def test_daily_forecast_lisbon() -> None:
    result = await get_daily_forecast(*LISBON, days=3)
    _assert_rendered(result)
    assert "Open-Meteo" in result
