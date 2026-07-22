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
  test_condition.py, test_weather.py, test_format.py  -- unit tests (pure logic)
  test_integration.py                            -- integration tests (live APIs, CI-excluded)
```

One module per responsibility. No sub-packages.

### Module Boundaries

**`server.py` (Presentation Layer)**
- Owns: MCP tool registration via `@mcp.tool()`, `aiohttp.ClientSession` lifecycle (created per tool call via `async with`), catching API exceptions and turning them into short markdown error lines, `main()` entry point
- Does not own: HTTP communication, merge logic, rendering
- Calls: `weather.py` orchestration functions, passing in the session

**`weather.py` (Orchestration Layer)**
- Owns: the current-conditions merge chain (INCA -> nowcast -> AROME per field), hourly assembly (accumulation differencing, wind-from-components, POP mapping), unit conversions, GeoSphere-vs-Open-Meteo path selection and fallback
- Does not own: HTTP calls (delegates to the API clients), MCP protocol concerns
- Calls: `geosphere_api`, `openmeteo_api`, `condition`, `format`

**`geosphere_api.py` / `openmeteo_api.py` (Data Access Layer)**
- Own: all HTTP communication, URL/query construction, GeoJSON / JSON parsing, error taxonomy (timeout, 429, out-of-domain 400)
- Do not own: session creation/teardown, merge logic, MCP concerns
- Raise typed exceptions on failure (see Error Signaling)

**`condition.py` (Pure Derivation)**
- Owns: `derive_condition`, `derive_current_condition`, fog heuristic, `is_night` (astral), Magnus dew point, apparent temperature, wind-from-components
- No I/O, no HTTP, no MCP/HA imports

**`format.py` (Rendering)**
- Owns: the emoji-markdown renderers for each of the three tools

**`const.py` (Configuration)**
- Owns: all literal values -- API base URLs, dataset resource IDs, parameter lists, thresholds, the WMO-code -> condition map, timeouts
- Zero magic values exist elsewhere

### Data Flow
```
LLM client
  -> stdio transport
    -> FastMCP framework (server.py)
      -> @mcp.tool() handler creates aiohttp.ClientSession
        -> weather.py orchestration
          -> geosphere_api / openmeteo_api  (HTTP + parse)
          -> condition.py  (derive condition strings)
          -> format.py  (render markdown)
        <- markdown string (success) or short error line (on caught exception)
    <- MCP protocol response
  <- LLM receives markdown tool result
```

Coverage is detected by attempting GeoSphere and catching the out-of-domain error, which transparently triggers the Open-Meteo fallback -- there is no hardcoded bounding box.

### Session Management
- `aiohttp.ClientSession` is created per-tool-call in `server.py` using `async with`
- The session is passed into orchestration and API functions as a parameter
- The API modules never create or own a session
- No session reuse or connection pooling across calls

### Error Signaling
- API modules raise typed exceptions (timeout, rate-limit/429, out-of-domain 400, generic client error)
- `server.py` catches them and returns a short markdown line: timeout -> `⚠️ Timeout fetching weather data`; unexpected -> `⚠️ No data`; 429 -> retry-once then an error line with retry-after
- GeoSphere out-of-domain is **not** an error -- `weather.py` catches it and falls back to Open-Meteo
- Tools never raise across the MCP boundary

## Dependencies
- `server.py` depends on `weather.py`, `format.py`, `const.py`
- `weather.py` depends on the API clients, `condition.py`, `format.py`, `const.py`
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

## Extension Guidelines
- New MCP tool: add `@mcp.tool()` in `server.py`, orchestration in `weather.py`, renderer in `format.py`
- New API method: add to the relevant client following the existing pattern (build query, GET, parse, raise typed error on failure)
- New constants: add to `const.py`
- Keep `geosphere_api.py`, `openmeteo_api.py`, and `condition.py` free of MCP/HA imports
