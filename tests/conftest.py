"""Pytest configuration.

Puts `scripts/` on the import path so release tooling can be tested. Those
scripts are development tools, not part of the shipped package, so they live
outside `src/` and are not importable by default.

Also empties the process-wide forecast cache around every test.
"""

from __future__ import annotations

import sys
from collections.abc import Iterator
from pathlib import Path

import pytest

from geosphere_mcp_server import forecast_cache

sys.path.insert(0, str(Path(__file__).parent.parent / "scripts"))


@pytest.fixture(autouse=True)
def _empty_forecast_cache(monkeypatch: pytest.MonkeyPatch) -> Iterator[None]:
    """Start every test with an empty forecast cache at its default lifetime.

    The cache is process-wide, so without this a forecast fetched by one test
    would answer the next one.
    """
    monkeypatch.delenv(forecast_cache.FORECAST_CACHE_TTL_ENV, raising=False)
    forecast_cache.clear()
    yield
    forecast_cache.clear()
