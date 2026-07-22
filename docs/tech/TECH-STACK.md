# Tech Stack

## Purpose
Documents the languages, frameworks, build tools, and key libraries used in this project.

## Responsibilities
- Defining the runtime and language requirements
- Listing framework choices and their architectural roles
- Documenting build, lint, and test tooling
- Tracking external API dependencies

## Non-Responsibilities
- Project structure and module boundaries (see [ARCHITECTURE.md](ARCHITECTURE.md))
- Code style and naming rules (see [CONVENTIONS.md](CONVENTIONS.md))
- Test patterns and commands (see [TESTING.md](TESTING.md))

## Overview

### Language
- Python 3.12+ -- `requires-python = ">=3.12"` in `pyproject.toml`
- `from __future__ import annotations` required in every file

### Framework
- **FastMCP** (`mcp[cli]`) -- MCP server framework. Exposes Python async functions as Model Context Protocol tools over stdio transport. Tools registered via `@mcp.tool()`. The framework handles protocol serialization, tool schema generation from type hints/docstrings, and transport lifecycle.

### Build and Environment
- **Hatchling** -- PEP 517 build backend declared in `pyproject.toml`
- **hatch-vcs** -- single-sources the version from git tags into `src/geosphere_mcp_server/_version.py` (generated, gitignored)
- **uv** -- environment and dependency management (`uv sync`); also the recommended runtime launcher (`uvx`). `uv.lock` is committed.

### Linting and Formatting
- **ruff** -- single tool for both linting and formatting; configured in `pyproject.toml`
- Rule sets enabled: `E`, `W`, `F`, `I`, `UP`, `B`, `SIM`
- Max line length: 88 characters (Black-compatible)
- Target version: Python 3.12

### HTTP Client and Utilities
- **aiohttp** -- async HTTP client for all outbound requests
- **astral** -- sunrise/sunset and day/night determination for condition derivation
- `asyncio.timeout` (stdlib, Python 3.11+) for timeout enforcement -- no `async_timeout` shim

### External APIs
- **GeoSphere Austria Dataset API** (`https://dataset.api.hub.geosphere.at/v1`) -- keyless; rate limits 5 req/s, 240 req/h. Point queries via `GET /timeseries/{mode}/{resource_id}?parameters=...&lat_lon={lat},{lon}&output_format=geojson`. Out-of-bounds returns HTTP 400 with `"outside of dataset bounds"`. Datasets: AROME `forecast/nwp-v1-1h-2500m`, C-LAEF `forecast/ensemble-v1-1h-2500m`, INCA analysis `historical/inca-v1-1h-1km`, INCA nowcast `forecast/nowcast-v1-15min-1km`.
- **Open-Meteo** (`https://api.open-meteo.com/v1/forecast`) -- keyless/free non-commercial, worldwide, up to 16 days; current + hourly + daily variables incl. precipitation probability and WMO weather codes, `timezone=auto`. Automatic fallback when a point is outside GeoSphere coverage, and the sole source for the daily tool.

### CI/CD
- **GitHub Actions** -- `.github/workflows/validate.yml`
- Triggers: push to `main` and all pull requests
- Jobs: `ruff` (lint + format check), `test` (unit tests only), `gate` (fan-in that fails if either prior job fails; the single required status check)
- Integration tests are excluded from CI
- Release pipeline (`release.yml` on `v*` tags): `uv build` + tag/version check -> PyPI Trusted Publishing (OIDC) -> GitHub Release -> MCP Registry publish
- `auto-release.yml`: merged `dependabot/uv/*` PRs (or manual dispatch) cut the next patch tag

### No Infrastructure
No Docker, Kubernetes, Terraform, or cloud platform configuration. Distributed as a PyPI package, run locally via `uvx`.

## Dependencies
- Runtime: `mcp[cli]>=1.28.1`, `aiohttp>=3.0.0`, `astral>=3.2`
- Dev: `pytest`, `pytest-asyncio`; `ruff` (installed in CI)
- External: GeoSphere Austria Dataset API, Open-Meteo

## Design Decisions
- **uv over pip/poetry**: speed and deterministic resolution; enables `uvx` one-command launch.
- **aiohttp over httpx**: mature async HTTP client, consistent with `ha-geosphere-next`.
- **ruff as sole linter/formatter**: replaces Black, isort, flake8 with one fast tool.
- **hatch-vcs versioning**: version derived from git tags, so releases and package metadata never drift.

## Known Risks
- GeoSphere dataset resource IDs are versioned -- a catalog rotation breaks the server until IDs are bumped.
- No GeoSphere forecast beyond ~60 h -- longer horizons must route through Open-Meteo.
- Shared rate limits (5 req/s, 240 req/h) with no server-side quota tracking.
- No connection pooling -- a new `aiohttp.ClientSession` per tool call.

## Extension Guidelines
- Add new runtime dependencies under `[project.dependencies]`, then `uv sync` and commit `uv.lock`.
- Add new dev dependencies under `[dependency-groups] dev`.
- New ruff rules: add to the `select` list in `[tool.ruff.lint]`.
