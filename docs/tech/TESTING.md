# Testing

## Purpose
Documents the test structure, patterns, tooling, and conventions used in the project.

## Responsibilities
- Defining test file organization and naming
- Specifying mock patterns and assertion style
- Documenting the unit/integration test split
- Listing test commands

## Non-Responsibilities
- Code style rules (see [CONVENTIONS.md](CONVENTIONS.md))
- Architecture and module boundaries (see [ARCHITECTURE.md](ARCHITECTURE.md))

## Overview

### Test Structure
```
tests/
  test_geosphere_api.py   -- unit tests, mocked HTTP (runs in CI)
  test_server.py          -- unit tests for the four MCP tool functions (runs in CI)
  test_condition.py       -- unit tests, pure derivation table tests (runs in CI)
  test_weather.py         -- unit tests, merge chain / POP / stamping, plus recorded v2 fixtures (runs in CI)
  test_format.py          -- unit tests, markdown renderer snapshots (runs in CI)
  test_outlook.py         -- unit tests, gust/thunderstorm windows (runs in CI)
  test_air_quality.py     -- unit tests, pollutant merge + AQI day matching (runs in CI)
  test_server_json.py     -- MCP Registry publish constraints (runs in CI)
  test_integration.py     -- integration tests, live APIs (CI-excluded)
  fixtures/               -- live GeoSphere responses, recorded by scripts/record_fixtures.py
```

### Recorded Fixtures
`tests/fixtures/` holds live responses for one point (Pörtschach by default): the deterministic forecast
(`nwp.json`) and the C-LAEF ensemble (`ensemble.json`), each requested from its run's own t0 so index 0
keeps the null that marks an interval-ending parameter, plus the nowcast and INCA requested exactly as the
server requests them, and `manifest.json` with the point and the recording instant. The tests use that
instant as their frozen clock.

Re-record after a dataset or parameter change: `uv run python scripts/record_fixtures.py [--lat-lon LAT,LON]`
from the repository root. The script also prints each forecast parameter's stamping (null at t0 =
interval-ending), which is how a new parameter is classified before it is wired in. Rows are compared
against the live recording, so the fixture tests assert *properties* (stamping, symbol/precipitation
agreement, the merge's verdict for that recording), and a re-record may need the last of those adjusted
to the new weather.

### Release-metadata tests
`test_server_json.py` checks `server.json` against constraints the MCP Registry only enforces at publish
time -- description length (<=100), package identifier matching `pyproject.toml`, transport and registry
type, the OIDC namespace, and the `mcp-name:` ownership marker in the README.

That timing is the point. The registry validates *after* the PyPI upload has succeeded and the tag is
pushed, and the failed job cannot be re-run (the workflow checks out the tag) nor the tag moved (PyPI
rejects a re-upload, failing the job the registry step needs). Every rejection therefore costs a version
number -- the sibling `calc-mcp-server` burned two that way on 2026-07-30. These tests move both checks
into CI, minutes before a release rather than during one.

### Test File Naming
- Unit tests: `test_<module>.py`
- Integration tests: `test_integration.py`, marked with `@pytest.mark.integration`

### Test Method Naming
- Pattern: `test_<function>_<scenario>()` with `-> None` return annotation
- Examples: `test_async_get_timeseries_builds_url_and_params`, `test_derive_condition_from_symbol_by_night`, `test_precipitation_probability`

### Test Organization
- Arrange-Act-Assert pattern
- Tests grouped by function with `# --- Section header ---` comment banners
- Sample data defined as module-level `SAMPLE_*` constants, not inline (e.g. captured GeoJSON payloads);
  whole live responses live in `tests/fixtures/` instead

### Async Testing
- `pytest-asyncio` with `asyncio_mode = "auto"` in `pyproject.toml`
- Tests still carry explicit `@pytest.mark.asyncio` decorators for clarity

### Mocking
- `unittest.mock` only (`AsyncMock`, `MagicMock`) -- no third-party mock library
- A shared session factory helper builds `AsyncMock` `aiohttp.ClientSession`s supporting:
  - Single response
  - Response sequence (list) -- e.g. concurrent INCA/nowcast/AROME fetches
  - Error injection (timeout, 429, 400 out-of-domain)

### Assertions
- Plain `assert` statements, no helper methods
- API error paths assert the expected typed exception is raised (`pytest.raises`)
- Derivation tests are table-driven, porting fixture values from `ha-geosphere-next`
- Renderer tests assert on the produced markdown string

### Coverage Targets (unit)
- The API client: success, timeout, 429, out-of-domain 400
- Condition derivation: the full `sy` table code by code, by day and night, plus every observed/dry/unobserved path of the current condition
- Merge-chain preference (INCA -> nowcast -> AROME), POP stepped mapping, interval-ending parameters read one stamp on
- Storm outlook: symbol, ensemble and both signals, the 12 h ensemble age rule, unknown only for an empty window
- Every tool's out-of-coverage line, kept distinct from the retryable failure lines
- Markdown renderer snapshots for each tool

### Integration Tests
- Marked with `@pytest.mark.integration`, hit the live GeoSphere API
- Three coverage classes: Austria (INCA + AROME sources, Vienna), Alps-non-AT (AROME-only, Munich), and outside the grid (Lisbon, where every tool must return the out-of-coverage line -- these pin the live API's out-of-domain signal, which the whole coverage message rests on)
- Excluded from CI: `pytest tests/ -v -m "not integration"`
- Run manually: `pytest tests/ -v -m integration`

### Commands
| Command | Scope | Runs in CI |
|---------|-------|------------|
| `pytest tests/ -v -m "not integration"` | Unit tests | Yes |
| `pytest tests/ -v -m integration` | Integration tests | No |

## Dependencies
- `pytest` -- test runner
- `pytest-asyncio` -- async test support
- `unittest.mock` (stdlib) -- mocking

## Design Decisions
- **stdlib mock over pytest-mock/responses**: keeps dev dependencies minimal.
- **Auto asyncio mode with explicit decorators**: `asyncio_mode = "auto"` avoids boilerplate, decorators kept for readability.
- **Integration tests excluded from CI**: avoids flaky CI from external API dependency and rate limits; run manually for validation.

## Known Risks
- Integration tests depend on GeoSphere availability, response format stability, and rate limits.
- GeoSphere resource-ID rotations are only caught by integration tests, not unit tests.

## Extension Guidelines
- New unit tests: add to the matching `test_<module>.py`, or create one for a new module
- New integration tests: add to `test_integration.py` with `@pytest.mark.integration`
- Follow `test_<function>_<scenario>` naming
- Use the shared mocked-session helper for unit tests
- Define sample data as `SAMPLE_*` module-level constants
