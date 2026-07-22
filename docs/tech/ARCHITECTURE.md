# Architecture

## Purpose
Documents the project structure, module boundaries, layering, and data flow.

## Responsibilities
- Defining the architectural pattern and module layout
- Describing data flow across layers
- Specifying module boundaries and ownership
- Documenting session and lifecycle management

## Non-Responsibilities
- Technology choices and library details (see [TECH-STACK.md](TECH-STACK.md))
- Naming and code style rules (see [CONVENTIONS.md](CONVENTIONS.md))
- Domain concepts and terminology (see [../domain/OVERVIEW.md](../domain/OVERVIEW.md))

## Overview

### Architectural Pattern
Layered functional design: a FastMCP presentation layer over pure async API clients plus pure derivation and rendering helpers. Purely functional -- no classes outside the `FastMCP` instance. The API clients and the `condition`/`weather`/`format` helpers carry no MCP or Home Assistant imports, so they are independently testable and portable.

### Project Structure
```
src/geosphere_mcp_server/
  __init__.py          -- version string only (importlib.metadata, fallback "0.0.0+unknown")
  server.py            -- FastMCP tool registration, session lifecycle, entry point
  weather.py           -- merge chain, hourly assembly, POP mapping, unit conversions
  geosphere_api.py     -- pure async client for the GeoSphere Dataset API
  openmeteo_api.py     -- pure async client for Open-Meteo
  condition.py         -- pure condition derivation (ported from ha-geosphere-next)
  format.py            -- emoji-markdown renderers for the three tools
  const.py             -- all constants (URLs, resource IDs, parameters, thresholds, WMO map)
  py.typed             -- typing marker
  _version.py          -- hatch-vcs generated, gitignored
tests/
  test_geosphere_api.py, test_openmeteo_api.py  -- unit tests (mocked HTTP)
  test_server.py                                 -- unit tests for the three MCP tool functions
  test_condition.py, test_weather.py, test_format.py  -- unit tests (pure logic)
  test_integration.py                            -- integration tests (live APIs, CI-excluded)
```

One module per responsibility. No sub-packages.

### Module Boundaries

**`server.py` (Presentation Layer)**
- Owns: MCP tool registration via `@mcp.tool()`, `aiohttp.ClientSession` lifecycle (created per tool call via `async with`), the GeoSphere-vs-Open-Meteo path selection and fallback (catches `GeoSphereOutOfDomainError` and calls `openmeteo_api`), catching API exceptions and turning them into short markdown error lines, the `start`-argument parser (`_parse_start`) for `get_hourly_forecast`, `main()` entry point
- Does not own: HTTP communication, the merge/derivation logic
- Calls: `weather.py` (GeoSphere fetch + merge), `openmeteo_api` (fallback and daily), and `format.py` (normalize + render) -- passing in the session
- `RATE_LIMIT_RETRY_MAX_S = 5.0` is defined here (not in `const.py`) -- the only literal threshold outside the central constants module

**`weather.py` (Orchestration Layer)**
- Owns: the current-conditions merge chain (INCA -> nowcast -> AROME per field), hourly assembly (accumulation differencing, wind-from-components, POP mapping), unit conversions
- Does not own: HTTP calls (delegates to `geosphere_api`), the Open-Meteo fallback (raises `GeoSphereOutOfDomainError` up to `server.py`), rendering, MCP concerns
- Calls: `geosphere_api`, `condition`

**`geosphere_api.py` / `openmeteo_api.py` (Data Access Layer)**
- Own: all HTTP communication, URL/query construction, GeoJSON / JSON parsing, error taxonomy (timeout, 429, out-of-domain 400)
- Do not own: session creation/teardown, merge logic, MCP concerns
- Raise typed exceptions on failure (see Error Signaling)

**`condition.py` (Pure Derivation)**
- Owns: `derive_condition`, `derive_current_condition`, fog heuristic, `is_night` (astral), Magnus dew point, apparent temperature, wind-from-components
- No I/O, no HTTP, no MCP/HA imports

**`format.py` (Normalization + Rendering)**
- Owns: the `normalize_*` functions (shape a GeoSphere or Open-Meteo payload into a render-ready dict) and the emoji-markdown renderers (`render_current`/`render_hourly`/`render_daily`) for each of the three tools

**`const.py` (Configuration)**
- Owns: nearly all literal values -- API base URLs, dataset resource IDs, parameter lists, thresholds, the WMO-code -> condition map, timeouts
- The sole documented exception is `RATE_LIMIT_RETRY_MAX_S` in `server.py` (see Known Risks)

### Data Flow
```
LLM client
  -> stdio transport
    -> FastMCP framework (server.py)
      -> @mcp.tool() handler creates aiohttp.ClientSession
        -- GeoSphere path --
        -> weather.py  (geosphere_api HTTP + parse -> condition.py derive -> merged dict)
        -- fallback path (on GeoSphereOutOfDomainError) or daily --
        -> openmeteo_api  (HTTP + parse)  [called directly by server.py]
        -> format.py  (normalize the merged/parsed dict -> render markdown)  [called by server.py]
        <- markdown string (success) or short error line (on caught exception)
    <- MCP protocol response
  <- LLM receives markdown tool result
```

Coverage is detected in `server.py` by attempting the GeoSphere path and catching `GeoSphereOutOfDomainError`, which transparently triggers the Open-Meteo fallback -- there is no hardcoded bounding box.

### Session Management
- `aiohttp.ClientSession` is created per-tool-call in `server.py` using `async with`
- The session is passed into orchestration and API functions as a parameter
- The API modules never create or own a session
- No session reuse or connection pooling across calls

### Error Signaling
- API modules raise typed exceptions (timeout, rate-limit/429, out-of-domain 400, generic client error)
- `server.py` catches them and returns a short markdown line:
  - timeout -> `⚠️ Timeout fetching weather data`
  - unexpected -> `⚠️ No weather data available`
  - invalid `start` argument -> `⚠️ Invalid start time '{start}'; use ISO 8601 (e.g. 2026-07-22T15:00)` (raised by `_parse_start` **before** any session is opened)
  - 429 -> the server retries **once**, but only when `retry_after <= RATE_LIMIT_RETRY_MAX_S` (5 s); a longer or absent retry-after skips the retry. The message is `⚠️ GeoSphere rate limit exceeded (retry in {N}s)` when the API sent a `Retry-After` header, else `... (retry shortly)`. For the current/hourly tools it gains the suffix `— get_daily_forecast still works (Open-Meteo).`. A failing retry is logged and the original rate-limit message is still returned.
- GeoSphere out-of-domain is **not** an error -- `weather.py` raises `GeoSphereOutOfDomainError`, and `server.py` catches it and falls back to Open-Meteo
- Tools never raise across the MCP boundary

## Dependencies
- `server.py` depends on `weather.py`, `openmeteo_api.py`, `format.py`, `const.py`
- `weather.py` depends on `geosphere_api.py`, `condition.py`, `const.py` (not `openmeteo_api` or `format`)
- `geosphere_api.py` / `openmeteo_api.py` depend on `const.py`, `aiohttp`, `asyncio`
- `condition.py` depends on `astral` and `const.py` only
- `const.py` has no internal dependencies
- No circular dependencies exist

## Design Decisions
- **Import-free core**: the API clients and `condition.py` avoid MCP/HA imports so the derivation logic can be shared with (and stays portable from) `ha-geosphere-next`.
- **Transparent fallback over coverage table**: attempt GeoSphere, catch out-of-domain, fall back to Open-Meteo -- no bounding box to maintain as the datasets evolve.
- **Per-call sessions**: each tool invocation creates a fresh `aiohttp.ClientSession`. Simplifies lifecycle at the cost of connection reuse.
- **Markdown, not JSON**: tools return compact emoji-markdown tuned for LLM voice agents, matching the OWM server they replace.

## Known Risks
- GeoSphere dataset resource IDs are versioned -- a catalog rotation breaks the server until IDs in `const.py` are bumped.
- Per-call session creation prevents HTTP connection reuse.
- `condition.py` duplicates HA condition string literals to stay import-free -- could drift if HA renames a condition.
- `RATE_LIMIT_RETRY_MAX_S` lives in `server.py`, not `const.py` -- a documented deviation from the "constants in `const.py` only" convention; move it if a second server-layer threshold appears.

## Extension Guidelines
- New MCP tool: add `@mcp.tool()` in `server.py`, orchestration in `weather.py`, renderer in `format.py`
- New API method: add to the relevant client following the existing pattern (build query, GET, parse, raise typed error on failure)
- New constants: add to `const.py`
- Keep `geosphere_api.py`, `openmeteo_api.py`, and `condition.py` free of MCP/HA imports
