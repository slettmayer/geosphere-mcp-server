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
  test_openmeteo_api.py   -- unit tests, mocked HTTP (runs in CI)
  test_server.py          -- unit tests for the three MCP tool functions (runs in CI)
  test_condition.py       -- unit tests, pure derivation table tests (runs in CI)
  test_weather.py         -- unit tests, merge chain / POP / differencing (runs in CI)
  test_format.py          -- unit tests, markdown renderer snapshots (runs in CI)
  test_integration.py     -- integration tests, live APIs (CI-excluded)
```

### Test File Naming
- Unit tests: `test_<module>.py`
- Integration tests: `test_integration.py`, marked with `@pytest.mark.integration`

### Test Method Naming
- Pattern: `test_<function>_<scenario>()` with `-> None` return annotation
- Examples: `test_fetch_arome_success`, `test_fetch_geosphere_out_of_domain`, `test_derive_condition_fog`, `test_pop_mapping_steps`

### Test Organization
- Arrange-Act-Assert pattern
- Tests grouped by function with `# --- Section header ---` comment banners
- Sample data defined as module-level `SAMPLE_*` constants, not inline (e.g. captured GeoJSON / Open-Meteo payloads)

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
- Both API clients: success, timeout, 429, out-of-domain 400
- Condition derivation: full threshold table incl. fog heuristic and day/night
- Merge-chain preference (INCA -> nowcast -> AROME), POP stepped mapping, accumulation differencing
- WMO map completeness (codes 0-99)
- Markdown renderer snapshots for each tool

### Integration Tests
- Marked with `@pytest.mark.integration`, hit live GeoSphere + Open-Meteo APIs
- Three coverage classes: Austria (INCA + AROME sources), Alps-non-AT (AROME-only), worldwide (Open-Meteo fallback), for all three tools
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
- Integration tests depend on GeoSphere/Open-Meteo availability, response format stability, and rate limits.
- GeoSphere resource-ID rotations are only caught by integration tests, not unit tests.

## Extension Guidelines
- New unit tests: add to the matching `test_<module>.py`, or create one for a new module
- New integration tests: add to `test_integration.py` with `@pytest.mark.integration`
- Follow `test_<function>_<scenario>` naming
- Use the shared mocked-session helper for unit tests
- Define sample data as `SAMPLE_*` module-level constants
