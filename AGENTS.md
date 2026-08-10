# GeoSphere MCP Server
> MCP server for weather forecasts, usable by LLMs via the Model Context Protocol. High-resolution GeoSphere Austria data (AROME/INCA/C-LAEF) for Austria and the Alps, Open-Meteo worldwide.

> **Editing this guide:** `AGENTS.md` is the single source of truth for project context, read by all AI
> coding agents and humans. Keep it concise — put detail in `docs/` and link it. When you change code that
> alters documented behavior, update the matching `docs/` file in the **same PR** (CodeRabbit enforces
> this — see [docs/README.md](docs/README.md)).

## Quick Reference
- **Lint**: `ruff check .`
- **Format**: `ruff format .`
- **Test (unit)**: `pytest tests/ -v -m "not integration"`
- **Test (integration, live APIs)**: `pytest tests/ -v -m integration`
- **Run server**: `uvx --from . geosphere-mcp-server` or `python -m geosphere_mcp_server.server`
- **Validate (CI)**: Ruff + pytest unit tests (all must pass via the `gate` job)
- **Release**: run the Auto Release workflow; the version comes from the git tag -- see [RELEASING.md](docs/tech/RELEASING.md)

## Where to Find Things
| I need to... | Read |
|--------------|------|
| Understand the architecture | [ARCHITECTURE.md](docs/tech/ARCHITECTURE.md) |
| Write code that fits conventions | [CONVENTIONS.md](docs/tech/CONVENTIONS.md) |
| Know the tech stack | [TECH-STACK.md](docs/tech/TECH-STACK.md) |
| Write or run tests | [TESTING.md](docs/tech/TESTING.md) |
| Cut a release, or add a changelog entry | [RELEASING.md](docs/tech/RELEASING.md) |
| Understand the business domain | [docs/domain/](docs/domain/README.md) |
| Know a tool's signature, validation, or exact output | [OUTPUT-CONTRACT.md](docs/domain/OUTPUT-CONTRACT.md) |
| Change a condition threshold or the merge chain | [CONDITION-DERIVATION.md](docs/domain/CONDITION-DERIVATION.md) |
| Work on gusts, thunderstorm timing, or the outlook windows | [STORM-OUTLOOK.md](docs/domain/STORM-OUTLOOK.md) |
| Work on pollutants or the air quality index | [AIR-QUALITY.md](docs/domain/AIR-QUALITY.md) |
| Add or bump a dataset, or check coverage rules | [DATA-SOURCES-AND-COVERAGE.md](docs/domain/DATA-SOURCES-AND-COVERAGE.md) |

## Architecture Overview
MCP presentation layer over pure async API clients and pure derivation/rendering helpers. Purely
functional -- no classes outside the `MCPServer` instance (only typed exceptions and small data holders).
All code lives in `src/geosphere_mcp_server/`.

- `server.py` -- MCPServer tool registration (5 tools), session lifecycle, GeoSphere-vs-Open-Meteo path selection + fallback, stdio entry point, sentinel error lines, `start`-argument parsing
- `weather.py` -- merge chain, hourly assembly, POP mapping, unit conversions (orchestration)
- `air_quality.py` -- WRF-Chem pollutant merge + daily AQI by local calendar day (orchestration)
- `geosphere_api.py` -- pure async client for the GeoSphere Dataset API
- `openmeteo_api.py` -- pure async client for Open-Meteo (current/hourly/daily/air quality)
- `condition.py` -- pure condition derivation, incl. the CAPE/CIN thunder gate (ported from ha-geosphere-next)
- `outlook.py` -- pure storm-outlook derivation over the hourly rows (ported from ha-geosphere-next)
- `format.py` -- emoji-markdown renderers for the five tools
- `const.py` -- most constants (URLs, resource IDs, parameter lists, thresholds, WMO->condition map)

Data flow: MCP tool call -> `server.py` handler. GeoSphere path goes through `weather.py` or
`air_quality.py` (-> `geosphere_api` -> derived via `condition.py` / `outlook.py`); on out-of-domain or
for daily, `server.py` calls `openmeteo_api` directly. `server.py` then normalizes + renders via
`format.py` -> markdown string.

See [Architecture](docs/tech/ARCHITECTURE.md) for module boundaries and data flow detail.

## Tech Stack
- Python 3.12+, `from __future__ import annotations` in every file
- `mcp[cli]` (`mcp.server.MCPServer`) for MCP server framework -- v2 line, pinned `>=2,<3`
- `aiohttp` for async HTTP, `astral` for day/night
- `ruff` for linting/formatting, `pytest` + `pytest-asyncio` for testing
- `uv` for environment management, `hatchling` + `hatch-vcs` build backend
- GitHub Actions CI (validate on push/PR)

See [Tech Stack](docs/tech/TECH-STACK.md) for full detail.

## Core Conventions
- Module-level async functions -- no client classes
- Constants centralized in `const.py` -- no inline magic values (one documented exception: see Structural Risks)
- Logger: `_LOGGER = logging.getLogger(__name__)` with `%s` formatting (not f-strings)
- Import order: `__future__` -> stdlib -> third-party -> local
- API modules raise typed exceptions; the server layer catches them and returns a short markdown error line -- tools never raise
- GeoSphere out-of-domain is not an error: it triggers the transparent Open-Meteo fallback

See [Conventions](docs/tech/CONVENTIONS.md) for naming tables and full rules.

## Business Domain
Weather MCP gateway. Five tools: `get_current_weather`, `get_hourly_forecast` and `get_daily_forecast`
match the OpenWeatherMap server surface they replace; `get_storm_outlook` and `get_air_quality` are
additions. GeoSphere Austria's gridded datasets (AROME ~60 h, INCA analysis/nowcast, C-LAEF ensemble,
WRF-Chem air quality) drive everything but daily for Austria and the Alps; points outside coverage fall
back to Open-Meteo automatically, and daily is always Open-Meteo (worldwide, 1-16 days). A shared Home
Assistant-style condition vocabulary is derived physically on the GeoSphere path and mapped from WMO codes
on the Open-Meteo path.

See [Domain Overview](docs/domain/OVERVIEW.md) for the concept catalogue, API surfaces, and glossary; it
indexes the per-concept files on data sources, condition derivation, and the tool/output contract.

## Structural Risks
- GeoSphere dataset resource IDs are versioned -- a catalog rotation breaks the server until IDs in `const.py` are bumped
- No GeoSphere forecast beyond ~60 h -- longer horizons must go through Open-Meteo
- Per-call `aiohttp.ClientSession` creation -- no connection pooling
- `condition.py` duplicates HA `ATTR_CONDITION_*` string literals (to stay import-free) -- could drift if HA renames a condition
- Rate limits (GeoSphere 5 req/s, 240 req/h) shared across all callers -- no server-side quota tracking
- `RATE_LIMIT_RETRY_MAX_S` lives in `server.py`, not `const.py` -- the one magic value outside the central module (see [ARCHITECTURE.md](docs/tech/ARCHITECTURE.md))
- Clients never let a bare `TimeoutError` escape (each wraps it in a typed `*TimeoutError`), so the server
  layer must catch those explicitly -- a new client that skips this regresses timeouts to the generic
  "no data" line (see [ARCHITECTURE.md](docs/tech/ARCHITECTURE.md))
- Several merged fields (dew point, CAPE, global radiation, hourly wind bearing) are computed and then
  dropped in normalization -- surfacing them is a `format.py` change only
- Open-Meteo's forecast endpoint has CAPE but no convective inhibition, so the storm outlook silently
  degrades to CAPE-only thunder gating outside GeoSphere coverage (the rendered output says so)
- GeoSphere and Open-Meteo report the European AQI on two different scales (1-6 band vs 0-100+ numeric);
  `format.py` reconciles them to the band, and a new pollutant must be added on both paths at once

## Detailed Guides
- [Technical Context](docs/tech/README.md) -- architecture, tech stack, conventions, testing
- [Domain Context](docs/domain/README.md) -- datasets, coverage, condition derivation, integrations
- [Documentation Guide](docs/README.md) -- how to maintain these docs
