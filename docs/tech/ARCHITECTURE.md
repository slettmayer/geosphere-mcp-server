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
  openmeteo_api.py     -- pure async client for Open-Meteo (weather + air quality)
  condition.py         -- pure condition derivation (ported from ha-geosphere-next)
  outlook.py           -- pure storm-outlook derivation (ported from ha-geosphere-next)
  format.py            -- emoji-markdown renderers for the five tools
  const.py             -- all constants (URLs, resource IDs, parameters, thresholds, WMO map)
  py.typed             -- typing marker
  _version.py          -- hatch-vcs generated, gitignored
tests/
  test_geosphere_api.py, test_openmeteo_api.py  -- unit tests (mocked HTTP)
  test_server.py                                 -- unit tests for the five MCP tool functions
  test_condition.py, test_weather.py, test_format.py  -- unit tests (pure logic)
  test_outlook.py, test_air_quality.py           -- unit tests (pure logic + fetch orchestration)
  test_integration.py                            -- integration tests (live APIs, CI-excluded)
```

One module per responsibility. No sub-packages.

### Module Boundaries

**`server.py` (Presentation Layer)**
- Owns: MCP tool registration via `@mcp.tool()`, the `aiohttp.ClientSession` lifecycle (created per tool
  call via `async with`), the GeoSphere-vs-Open-Meteo path selection and fallback (catches
  `GeoSphereOutOfDomainError` and calls `openmeteo_api`), catching API exceptions and turning them into
  short markdown error lines, all argument parsing/validation/clamping, and the `main()` entry point
- Argument parsing helpers: `_parse_start` (ISO datetime for `get_hourly_forecast`), `_parse_date`
  (ISO calendar date for the `get_daily_forecast` range), `_clamp` (horizon bounds). All run **before**
  any session is opened, so a validation failure never issues an HTTP request.
- Does not own: HTTP communication, the merge/derivation logic
- Calls: `weather.py` (GeoSphere fetch + merge), `air_quality.py` (GeoSphere air quality), `openmeteo_api` (fallback and daily), and `format.py` (normalize + render) -- passing in the session
- `RATE_LIMIT_RETRY_MAX_S = 5.0` is defined here (not in `const.py`) -- the only literal threshold outside the central constants module

**`weather.py` (Orchestration Layer)**
- Owns: the current-conditions merge chain (INCA -> nowcast -> AROME per field), hourly assembly (accumulation differencing, wind-from-components, POP mapping), unit conversions
- Does not own: HTTP calls (delegates to `geosphere_api`), the Open-Meteo fallback (raises `GeoSphereOutOfDomainError` up to `server.py`), rendering, MCP concerns
- Calls: `geosphere_api`, `condition`
- `async_fetch_hourly_forecast(include_ensemble=False)` skips the C-LAEF request; the storm outlook uses
  this, since it reports no precipitation probability

**`air_quality.py` (Orchestration Layer)**
- Owns: the WRF-Chem pollutant merge (nearest forecast hour, full series retained) and the daily AQI
  match by local calendar day, plus the concurrent fetch of both datasets
- Applies the same primary/secondary rule as the ensemble: a `chem` failure propagates, an AQI failure
  degrades with a warning
- Calls: `geosphere_api`. Does not call `condition` -- air quality has no derived condition

**`geosphere_api.py` / `openmeteo_api.py` (Data Access Layer)**
- Own: all HTTP communication, URL/query construction, GeoJSON / JSON parsing, error taxonomy (connection/timeout, 429, out-of-domain 400)
- Do not own: session creation/teardown, merge logic, MCP concerns
- Raise typed exceptions on failure (see Error Signaling)
- `openmeteo_api.async_get_daily` has two request modes: a forward day count (`days`), or an explicit
  inclusive `start_date`/`end_date` range that takes precedence when supplied

**`condition.py` (Pure Derivation)**
- Owns: `derive_condition`, `derive_current_condition`, `is_thunder` (the CAPE/CIN gate), fog heuristic, `is_night` (astral), Magnus dew point, apparent temperature, wind-from-components
- No I/O, no HTTP, no MCP/HA imports

**`outlook.py` (Pure Derivation)**
- Owns: `max_gust`, `max_cape`, `next_thunderstorm`, `thunderstorm_outlook`, `series_is_decidable`, and
  the round-up window they share
- Reads the hourly **row dicts** both source paths produce, through `.get`, so one implementation serves
  GeoSphere and Open-Meteo and a source missing a key degrades rather than raising
- Depends only on `condition.is_thunder` and `const.py`; no I/O, no MCP/HA imports

**`format.py` (Normalization + Rendering)**
- Owns: the `normalize_*` functions (shape a GeoSphere or Open-Meteo payload into a render-ready dict)
  and the markdown renderers (`render_current`/`render_hourly`/`render_daily`/`render_outlook`/`render_air_quality`) for the five tools
- Owns the timezone reconciliation the outlook needs: GeoSphere rows are aware UTC and Open-Meteo rows are
  naive local, so each normalizer converts `now` into its own rows' convention before any comparison
- The renderers are shared across both data paths, so presentation behaviour such as the hourly
  day-divider headers applies identically to GeoSphere and Open-Meteo results
- Normalization is a narrowing step: several merged fields are deliberately not forwarded to the renderers
  (see [../domain/OUTPUT-CONTRACT.md](../domain/OUTPUT-CONTRACT.md))

**`const.py` (Configuration)**
- Owns: nearly all literal values -- API base URLs, dataset resource IDs, parameter lists, thresholds, the WMO-code -> condition map, timeouts
- The sole documented exception is `RATE_LIMIT_RETRY_MAX_S` in `server.py` (see Known Risks)

### Data Flow
```
LLM client
  -> stdio transport
    -> MCPServer framework (server.py)
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

Coverage is detected in `server.py` by attempting the GeoSphere path and catching
`GeoSphereOutOfDomainError`, which transparently triggers the Open-Meteo fallback -- there is no
hardcoded bounding box.

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
| `GeoSphereOutOfDomainError` | HTTP 400 containing "outside of dataset bounds" | each tool's `work()`, to trigger the fallback |
| `OpenMeteoApiError` | Base; an error status or an unexpected response shape | `_guarded` generic handler |
| `OpenMeteoConnectionError` | Network failure (`aiohttp.ClientError` is wrapped) | `_guarded` generic handler |
| `OpenMeteoTimeoutError` | An `OPENMETEO_TIMEOUT` breach; subclasses `OpenMeteoConnectionError` | `_guarded` timeout handler |

**Validation lines** are returned by the tool body before `_guarded` runs and before any session opens:

- `⚠️ Invalid start time '{start}'; use ISO 8601 (e.g. 2026-07-22T15:00)` (`_parse_start`)
- `⚠️ Invalid {label} '{value}'; use an ISO 8601 date (e.g. 2026-07-25)` (`_parse_date`, label is
  `start_date` or `end_date`)
- `⚠️ end_date '{end}' is before start_date '{start}'`

An over-long `hours`, `days`, or date range is **clamped silently** rather than rejected — see
[../domain/OUTPUT-CONTRACT.md](../domain/OUTPUT-CONTRACT.md).

**Runtime failures** are funnelled through the `_guarded` wrapper, which returns:

- 429 -> the server retries **once**, but only when `retry_after <= RATE_LIMIT_RETRY_MAX_S` (5 s); a
  longer or absent retry-after skips the retry. The message is
  `⚠️ GeoSphere rate limit exceeded (retry in {N}s)` when the API sent a `Retry-After` header, else
  `... (retry shortly)`. For the current and hourly tools it gains the suffix
  `— get_daily_forecast still works (Open-Meteo).` A failing retry is logged and the original rate-limit
  message is still returned.
- a timeout -> `⚠️ Timeout fetching weather data`
- anything else -> `⚠️ No weather data available` (logged at warning level)

The timeout branch catches `GeoSphereTimeoutError` and `OpenMeteoTimeoutError` **as well as** a bare
`TimeoutError`. This matters: the clients never let a bare `asyncio` timeout escape — each wraps it in its
own typed error — so catching only the builtin would silently route every real timeout to the generic
handler. Any new client must therefore either raise one of these timeout types or be added to that branch.
A non-timeout network failure stays a plain `*ConnectionError` and is reported as "no weather data",
because the caller can do nothing differently about it.

GeoSphere out-of-domain is **not** an error -- `weather.py` raises `GeoSphereOutOfDomainError`, and each
tool's `work()` catches it and falls back to Open-Meteo. Tools never raise across the MCP boundary.

## Dependencies
- `server.py` depends on `weather.py`, `air_quality.py`, `openmeteo_api.py`, `format.py`, `const.py`
- `weather.py` depends on `geosphere_api.py`, `condition.py`, `const.py` (not `openmeteo_api` or `format`)
- `geosphere_api.py` / `openmeteo_api.py` depend on `const.py`, `aiohttp`, `asyncio`
- `condition.py` depends on `astral` and `const.py` only
- `outlook.py` depends on `condition.py` and `const.py`; `format.py` depends on `outlook.py`
- `air_quality.py` depends on `geosphere_api.py` and `const.py`
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
- The timeout branch depends on each client raising a dedicated timeout type. A new client that wraps
  timeouts into a plain connection error would silently regress to the generic "no data" line, since a
  bare `TimeoutError` never reaches the server layer.
- `INCA_MAX_AGE_SECONDS` in `const.py` is defined but never referenced -- leftover from the
  `ha-geosphere-next` port, implying a staleness check this stateless server does not perform.
- The generic `except Exception` at the server layer can mask genuine defects behind
  `⚠️ No weather data available`; the warning log is the only signal.

## Extension Guidelines
- New MCP tool: add `@mcp.tool()` in `server.py`, orchestration in `weather.py`, renderer in `format.py`
- New API method: add to the relevant client following the existing pattern (build query, GET, parse, raise typed error on failure)
- New constants: add to `const.py`
- Keep `geosphere_api.py`, `openmeteo_api.py`, and `condition.py` free of MCP/HA imports
