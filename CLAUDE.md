# GeoSphere MCP Server
> MCP server for weather forecasts, usable by LLMs via the Model Context Protocol. High-resolution GeoSphere Austria data (AROME/INCA/C-LAEF) for Austria and the Alps, Open-Meteo worldwide.

## Quick Reference
- **Lint**: `ruff check .`
- **Format**: `ruff format .`
- **Test (unit)**: `pytest tests/ -v -m "not integration"`
- **Test (integration, live APIs)**: `pytest tests/ -v -m integration`
- **Run server**: `uvx --from . geosphere-mcp-server` or `python -m geosphere_mcp_server.server`
- **Validate (CI)**: Ruff + pytest unit tests (all must pass via `gate` job)

## Architecture Overview
FastMCP presentation layer over pure async API clients and pure derivation/rendering helpers. Purely functional -- no classes outside the `FastMCP` instance. All code in `src/geosphere_mcp_server/`.

- `server.py` -- FastMCP tool registration (3 tools), session lifecycle, stdio entry point, sentinel error lines
- `weather.py` -- merge chain, hourly assembly, POP mapping, unit conversions (orchestration)
- `geosphere_api.py` -- pure async client for the GeoSphere Dataset API
- `openmeteo_api.py` -- pure async client for Open-Meteo (current/hourly/daily)
- `condition.py` -- pure condition derivation (ported from ha-geosphere-next)
- `format.py` -- emoji-markdown renderers for the three tools
- `const.py` -- all constants (URLs, resource IDs, parameter lists, thresholds, WMO->condition map)

Data flow: MCP tool call -> `server.py` handler -> `weather.py` orchestration -> `geosphere_api`/`openmeteo_api` -> live API -> derived via `condition.py` -> rendered by `format.py` -> markdown string.

See [Architecture](docs/tech/ARCHITECTURE.md) for module boundaries and data flow detail.

## Tech Stack
- Python 3.12+, `from __future__ import annotations` in every file
- `mcp[cli]` (FastMCP) for MCP server framework
- `aiohttp` for async HTTP, `astral` for day/night
- `ruff` for linting/formatting, `pytest` + `pytest-asyncio` for testing
- `uv` for environment management, `hatchling` + `hatch-vcs` build backend
- GitHub Actions CI (validate on push/PR)

See [Tech Stack](docs/tech/TECH-STACK.md) for full detail.

## Core Conventions
- Module-level async functions -- no client classes
- Constants in `const.py` only -- no inline magic values
- Logger: `_LOGGER = logging.getLogger(__name__)` with `%s` formatting (not f-strings)
- Import order: `__future__` -> stdlib -> third-party -> local
- API modules raise typed exceptions; the server layer catches them and returns a short markdown error line -- tools never raise
- GeoSphere out-of-domain is not an error: it triggers the transparent Open-Meteo fallback

See [Conventions](docs/tech/CONVENTIONS.md) for naming tables and full rules.

## Business Domain
Weather MCP gateway. Three tools -- `get_current_weather`, `get_hourly_forecast`, `get_daily_forecast` -- matching the OWM server surface they replace. GeoSphere Austria's gridded datasets (AROME ~60 h, INCA analysis/nowcast, C-LAEF ensemble) drive current + hourly for Austria/the Alps; points outside coverage fall back to Open-Meteo automatically, and daily is always Open-Meteo (worldwide, 1-16 days). A shared HA-style condition vocabulary is derived physically on the GeoSphere path and mapped from WMO codes on the Open-Meteo path.

See [Domain Overview](docs/domain/OVERVIEW.md) for datasets, coverage, condition derivation, and attribution.

## Structural Risks
- GeoSphere dataset resource IDs are versioned -- a catalog rotation breaks the server until IDs in `const.py` are bumped
- No GeoSphere forecast beyond ~60 h -- longer horizons must go through Open-Meteo
- Per-call `aiohttp.ClientSession` creation -- no connection pooling
- `condition.py` duplicates HA `ATTR_CONDITION_*` string literals (to stay import-free) -- could drift if HA renames a condition
- Rate limits (GeoSphere 5 req/s, 240 req/h) shared across all callers -- no server-side quota tracking

## Detailed Guides
- [Technical Context](docs/tech/README.md) -- architecture, tech stack, conventions, testing
- [Domain Context](docs/domain/README.md) -- datasets, coverage, condition derivation, integrations
- [Documentation Guide](docs/README.md) -- how to maintain these docs
