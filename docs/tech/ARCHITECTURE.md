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
Layered functional design: an MCP presentation layer over pure async API clients plus pure derivation
and rendering helpers. Purely functional -- no classes outside the `MCPServer` instance, aside from the
typed exceptions and two small `@dataclass(slots=True)` response holders in `geosphere_api.py`. The API
clients and the `condition`/`weather`/`format` helpers carry no MCP or Home Assistant imports, so they are
independently testable and portable.

### Project Structure
```
src/geosphere_mcp_server/
  __init__.py          -- version string only (importlib.metadata, fallback "0.0.0+unknown")
  server.py            -- MCPServer tool registration, session lifecycle, entry point
  weather.py           -- merge chain, hourly assembly, POP mapping, unit conversions
  air_quality.py       -- WRF-Chem pollutant merge + daily AQI (orchestration)
  geosphere_api.py     -- pure async client for the GeoSphere Dataset API
  condition.py         -- pure condition derivation (ported from ha-geosphere-next)
  outlook.py           -- pure storm-outlook derivation (ported from ha-geosphere-next)
  format.py            -- emoji-markdown renderers for the four tools
  const.py             -- all constants (URLs, resource IDs, parameters, thresholds)
  py.typed             -- typing marker
  _version.py          -- hatch-vcs generated, gitignored
tests/
  test_geosphere_api.py                          -- unit tests (mocked HTTP)
  test_server.py                                 -- unit tests for the four MCP tool functions
  test_condition.py, test_weather.py, test_format.py  -- unit tests (pure logic)
  test_outlook.py, test_air_quality.py           -- unit tests (pure logic + fetch orchestration)
  test_integration.py                            -- integration tests (live APIs, CI-excluded)
```

One module per responsibility. No sub-packages.

### Module Boundaries

**`server.py` (Presentation Layer)**
- Owns: MCP tool registration via `@mcp.tool()`, the `aiohttp.ClientSession` lifecycle (created per tool
  call via `async with`), catching API exceptions and turning them into short markdown error lines
  (including `OUT_OF_DOMAIN_MESSAGE` for an uncovered point), all argument parsing/validation/clamping,
  and the `main()` entry point
- Argument parsing helpers: `_parse_start` (ISO datetime for `get_hourly_forecast`) and `_clamp` (horizon
  bounds). Both run **before** any session is opened, so a validation failure never issues an HTTP request.
- Does not own: HTTP communication, the merge/derivation logic
- Calls: `weather.py` (fetch + merge), `air_quality.py`, and `format.py` (normalize + render) -- passing
  in the session
- `RATE_LIMIT_RETRY_MAX_S = 5.0` is defined here (not in `const.py`) -- the only literal threshold outside the central constants module

**`weather.py` (Orchestration Layer)**
- Owns: the current-conditions merge chain (INCA -> nowcast -> AROME per field), hourly assembly (accumulation differencing, wind-from-components, POP mapping), unit conversions
- Does not own: HTTP calls (delegates to `geosphere_api`), the out-of-domain response (raises `GeoSphereOutOfDomainError` up to `server.py`), rendering, MCP concerns
- Calls: `geosphere_api`, `condition`
- `async_fetch_hourly_forecast(include_ensemble=False)` skips the C-LAEF request; the storm outlook uses
  this, since it reports no precipitation probability

**`air_quality.py` (Orchestration Layer)**
- Owns: the WRF-Chem pollutant merge (nearest forecast hour, full series retained) and the daily AQI
  match by local calendar day, plus the concurrent fetch of both datasets
- Applies the same primary/secondary rule as the ensemble: a `chem` failure propagates, an AQI failure
  degrades with a warning
- Calls: `geosphere_api`. Does not call `condition` -- air quality has no derived condition

**`geosphere_api.py` (Data Access Layer)**
- Owns: all HTTP communication, URL/query construction, GeoJSON parsing, error taxonomy
  (connection/timeout, 429, out-of-domain 400)
- Does not own: session creation/teardown, merge logic, MCP concerns
- Raises typed exceptions on failure (see Error Signaling)

**`condition.py` (Pure Derivation)**
- Owns: `derive_condition`, `derive_current_condition`, `is_thunder` (the CAPE/CIN gate), fog heuristic, `is_night` (astral), Magnus dew point, apparent temperature, wind-from-components
- No I/O, no HTTP, no MCP/HA imports

**`outlook.py` (Pure Derivation)**
- Owns: the round-up `window`, the window readers `max_gust` / `max_cape` / `thunderstorm_outlook`, the
  full-series `scan_thunderstorm` (storm hour, its CAPE, and whether the scan counts), and `horizon_hours`
- Reads the hourly **row dicts** `weather.assemble_hourly_forecast` produces, through `.get`, so an hour
  missing a field degrades rather than raising
- Depends only on `condition.is_thunder` and `const.py`; no I/O, no MCP/HA imports

**`format.py` (Normalization + Rendering)**
- Owns: the `normalize_*` functions (shape a merged/assembled dict into a render-ready one) and the
  markdown renderers (`render_current`/`render_hourly`/`render_outlook`/`render_air_quality`) for the
  four tools
- Owns the timezone handling: rows stay aware UTC through the derivation and only the *reported*
  timestamps are localized to Vienna — `aware + timedelta` is wall-clock arithmetic, so only UTC makes a
  stated horizon a true duration across a DST transition
- Normalization is a narrowing step: several merged fields are deliberately not forwarded to the renderers
  (see [../domain/OUTPUT-CONTRACT.md](../domain/OUTPUT-CONTRACT.md))

**`const.py` (Configuration)**
- Owns: nearly all literal values -- the API base URL, dataset resource IDs, parameter lists, thresholds, the condition vocabulary, timeouts
- The sole documented exception is `RATE_LIMIT_RETRY_MAX_S` in `server.py` (see Known Risks)

### Data Flow
```
LLM client
  -> stdio transport
    -> MCPServer framework (server.py)
      -> @mcp.tool() handler creates aiohttp.ClientSession
        -> weather.py / air_quality.py  (geosphere_api HTTP + parse
                                         -> condition.py / outlook.py derive -> merged dict)
        -> format.py  (normalize the merged dict -> render markdown)  [called by server.py]
        <- markdown string (success) or short error line (on caught exception)
    <- MCP protocol response
  <- LLM receives markdown tool result
```

Coverage is decided by the upstream API, not by a hardcoded bounding box: the fetch is attempted and
`GeoSphereOutOfDomainError` propagates to `_guarded`, which renders the out-of-coverage line.

### Session Management
- `aiohttp.ClientSession` is created per-tool-call in `server.py` using `async with`
- The session is passed into orchestration and API functions as a parameter
- The API modules never create or own a session
- No session reuse or connection pooling across calls

### Error Signaling

**Exception taxonomy.** Each client defines its own hierarchy rooted at a plain `Exception`:

| Exception | Raised for | Caught by |
|-----------|------------|-----------|
| `GeoSphereApiError` | Base; also a rejected request or an unexpected HTTP status | `_guarded` generic handler |
| `GeoSphereConnectionError` | Network failure (`aiohttp.ClientError` is wrapped) | `_guarded` generic handler |
| `GeoSphereTimeoutError` | A `GEOSPHERE_TIMEOUT` breach; subclasses `GeoSphereConnectionError` | `_guarded` timeout handler |
| `GeoSphereRateLimitError` | HTTP 429; carries a `retry_after` attribute | `_guarded` rate-limit handler |
| `GeoSphereOutOfDomainError` | HTTP 400 containing "outside of dataset bounds" | `_guarded` out-of-domain handler |

**Validation lines** are returned by the tool body before `_guarded` runs and before any session opens:

- `⚠️ Invalid start time '{start}'; use ISO 8601 (e.g. 2026-07-22T15:00)` (`_parse_start`)

An over-long `hours` value is **clamped silently** rather than rejected — see
[../domain/OUTPUT-CONTRACT.md](../domain/OUTPUT-CONTRACT.md).

**Runtime failures** are funnelled through the `_guarded` wrapper, which returns:

- out-of-domain -> `OUT_OF_DOMAIN_MESSAGE`, checked **first** so a point outside the grid never falls
  through to a retryable-sounding line
- 429 -> the server retries **once**, but only when `retry_after <= RATE_LIMIT_RETRY_MAX_S` (5 s); a
  longer or absent retry-after skips the retry. The message is
  `⚠️ GeoSphere rate limit exceeded (retry in {N}s)` when the API sent a `Retry-After` header, else
  `... (retry shortly)`. A failing retry is logged and the original rate-limit message is still returned —
  except when the retry itself is out-of-domain, which yields the coverage line.
- a timeout -> `⚠️ Timeout fetching weather data`
- anything else -> `⚠️ No weather data available` (logged at warning level)

The timeout branch catches `GeoSphereTimeoutError` **as well as** a bare `TimeoutError`. This matters: the
client never lets a bare `asyncio` timeout escape — it wraps it in its own typed error — so catching only
the builtin would silently route every real timeout to the generic handler. Any new client must therefore
either raise a timeout type or be added to that branch. A non-timeout network failure stays a plain
`GeoSphereConnectionError` and is reported as "no weather data", because the caller can do nothing
differently about it.

Out-of-domain is handled **once**, in `_guarded`, rather than per tool: with no second source behind it,
every tool answers an uncovered point identically. Tools never raise across the MCP boundary.

## Dependencies
- `server.py` depends on `weather.py`, `air_quality.py`, `format.py`, `const.py`
- `weather.py` depends on `geosphere_api.py`, `condition.py`, `const.py` (not `format`)
- `geosphere_api.py` depends on `const.py`, `aiohttp`, `asyncio`
- `condition.py` depends on `astral` and `const.py` only
- `outlook.py` depends on `condition.py` and `const.py`; `format.py` depends on `outlook.py`
- `air_quality.py` depends on `geosphere_api.py` and `const.py`
- `const.py` has no internal dependencies
- No circular dependencies exist

## Design Decisions
- **Import-free core**: the API clients and `condition.py` avoid MCP/HA imports so the derivation logic can be shared with (and stays portable from) `ha-geosphere-next`.
- **API-decided coverage over a coverage table**: attempt the fetch and let out-of-domain answer -- no bounding box to maintain as the datasets evolve.
- **A distinct out-of-coverage line, not a generic failure**: a caller that cannot separate "never served here" from "try again" will either retry forever or give up on a blip.
- **Per-call sessions**: each tool invocation creates a fresh `aiohttp.ClientSession`. Simplifies lifecycle at the cost of connection reuse.
- **Markdown, not JSON**: tools return compact emoji-markdown tuned for LLM voice agents, matching the OWM server they replace.

## Known Risks
- GeoSphere dataset resource IDs are versioned -- a catalog rotation breaks the server until IDs in `const.py` are bumped.
- Per-call session creation prevents HTTP connection reuse.
- `condition.py` duplicates HA condition string literals to stay import-free -- could drift if HA renames a condition.
- `RATE_LIMIT_RETRY_MAX_S` lives in `server.py`, not `const.py` -- a documented deviation from the "constants in `const.py` only" convention; move it if a second server-layer threshold appears.
- The timeout branch depends on the client raising a dedicated timeout type. A new client that wrapped
  timeouts into a plain connection error would silently regress to the generic "no data" line, since a
  bare `TimeoutError` never reaches the server layer.
- The generic `except Exception` at the server layer can mask genuine defects behind
  `⚠️ No weather data available`; the warning log is the only signal.

## Extension Guidelines
- New MCP tool: add `@mcp.tool()` in `server.py`, orchestration in `weather.py`, renderer in `format.py`
- New API method: add to the relevant client following the existing pattern (build query, GET, parse, raise typed error on failure)
- New constants: add to `const.py`
- Keep `geosphere_api.py` and `condition.py` free of MCP/HA imports
