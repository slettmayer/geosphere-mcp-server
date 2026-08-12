# Conventions

## Purpose
Documents naming patterns, code style, import ordering, error handling, and logging conventions.

## Responsibilities
- Defining naming rules for files, functions, constants, and variables
- Specifying code style requirements
- Documenting error handling and logging patterns
- Establishing import ordering rules

## Non-Responsibilities
- Architecture and module boundaries (see [ARCHITECTURE.md](ARCHITECTURE.md))
- Technology choices (see [TECH-STACK.md](TECH-STACK.md))
- Test-specific conventions (see [TESTING.md](TESTING.md))

## Overview

### File Naming
- Source files: `snake_case.py`
- Module names match their responsibility: `server`, `weather`, `air_quality`, `geosphere_api`, `condition`, `outlook`, `format`, `const`

### Function Naming
| Category | Pattern | Example |
|----------|---------|---------|
| MCP tools | `<verb>_<noun>` (no prefix) | `get_current_weather`, `get_hourly_forecast` |
| Async API fetch | `<verb>_<noun>` module-level async | `async_get_timeseries`, `async_fetch_current_conditions` |
| Derived logic | `derive_<noun>` | `derive_condition`, `derive_current_condition` |
| Private helpers | `_<verb>_<noun>` | `_diff`, `_percent`, `_wind_from_components` |

- Module-level async functions only -- no client classes.

### Variable Naming
- `snake_case` for all variables
- Plural for collections: `hours`, `timestamps`, `parameters`
- `*_count` suffix for totals

### Constants
- `UPPER_SNAKE_CASE`, centralized in `const.py` (the API base URL, dataset resource IDs, parameter lists, thresholds, the condition vocabulary, timeouts) -- no inline magic values
- One documented exception: `RATE_LIMIT_RETRY_MAX_S = 5.0` lives in `server.py`, since it governs presentation-layer retry policy rather than a data/derivation value
- Test fixture data uses `SAMPLE_*` prefix

### Logger
- One per module: `_LOGGER = logging.getLogger(__name__)`
- Always `%s`-style formatting, never f-strings: `_LOGGER.warning("Error: %s", msg)`
- Log levels: `error` for infrastructure failures (timeout, network), `warning` for logical/API failures (e.g. ensemble omitted)

### Imports
Strict ordering enforced by ruff `I` rule:
1. `from __future__ import annotations` (mandatory in every file)
2. Standard library
3. Third-party packages
4. Local imports

The API clients and `condition.py` must not import `mcp` or `homeassistant`.

### Type Annotations
- Full annotations on all function signatures
- Lowercase built-in generics: `dict[str, Any]`, `list[float]` (enabled by `from __future__ import annotations`)
- `py.typed` marker ships in the package

### Code Style
- 4-space indentation (PEP 8)
- 88-character max line length (Black-compatible)
- Double quotes (ruff formatter default)
- `noqa` comments used sparingly for intentional suppressions

### Error Handling
- API modules raise typed exceptions for failure categories: timeout, connection failure, rate-limit (429),
  out-of-domain (400 with "outside of dataset bounds"), generic client error
- Each client wraps `TimeoutError` into its own `*TimeoutError` and `aiohttp.ClientError` into its own
  `*ConnectionError`; a bare `TimeoutError` is therefore never propagated to the server layer, which is
  why that layer catches the typed timeouts explicitly (see [ARCHITECTURE.md](ARCHITECTURE.md))
- The **server layer** catches these and returns a short markdown error line -- tools never raise across the MCP boundary
- Argument validation happens in the tool body before any session is opened, and returns its own
  `⚠️ `-prefixed line rather than raising
- GeoSphere out-of-domain is not a failure: `weather.py` raises `GeoSphereOutOfDomainError` and `server._guarded` renders it as the out-of-coverage line, distinct from the retryable ones
- `asyncio.timeout()` enforces the per-request timeout (30 s, `GEOSPHERE_TIMEOUT`)

### Return Types
- MCP tools return `str` (emoji-markdown)
- API fetch functions return parsed `dict[str, Any]` / structured values, or raise a typed exception
- Orchestration functions in `weather.py` return the assembled data structures consumed by `format.py`

## Dependencies
- ruff enforces import ordering (`I`), line length and whitespace (`E`, `W`), unused/undefined names
  (`F`), pyupgrade rewrites (`UP`), bugbear checks (`B`), and simplification hints (`SIM`)
- `from __future__ import annotations` enables lowercase generics

## Design Decisions
- **Typed exceptions in API modules, sentinel lines at the server**: keeps the clients honest and testable while the presentation layer guarantees a tool never throws.
- **%s logging over f-strings**: avoids eager string interpolation; arguments are only formatted if the log level is active.
- **Constants centralized**: dataset resource IDs and thresholds are versioned/tunable in one place.

## Known Risks
- `condition.py` duplicates HA condition string literals to stay import-free -- could drift if HA renames a condition.
- Broad exception handling at the server layer could mask unexpected errors behind
  `⚠️ No weather data available`; the warning log is the only signal.
- A new API client that does not raise a dedicated `*TimeoutError` would have its timeouts silently folded
  into that same generic line (see [ARCHITECTURE.md](ARCHITECTURE.md) Known Risks).
- Constants can go stale without any signal: `INCA_MAX_AGE_SECONDS` sits in `const.py` unreferenced.

## Extension Guidelines
- New functions follow the naming pattern for their category (see table above)
- New constants go in `const.py`
- All new files must start with `from __future__ import annotations`
- Run `ruff check .` and `ruff format .` before committing
