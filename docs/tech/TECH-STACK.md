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
- `.python-version` pins local development to 3.12, the version CI installs. Without it `uv` picks the
  newest interpreter on the machine, so local and CI diverge silently -- stdlib `math` error messages
  were reworded after 3.12, for instance, so a test asserting on them passes locally and fails in CI
- `from __future__ import annotations` required in every file

### Framework
- **MCPServer** (`mcp[cli]`, the Python SDK's high-level server API) -- exposes Python async functions as Model Context
  Protocol tools over stdio transport, registered via `@mcp.tool()`. The framework handles protocol
  serialization, tool schema generation from type hints and docstrings, and the transport lifecycle.
  `@mcp.tool()` returns the plain undecorated function, so tools stay directly callable -- which is what
  lets `tests/test_server.py` import and await them without going through the framework.

### Build and Environment
- **Hatchling** -- PEP 517 build backend declared in `pyproject.toml`
- **hatch-vcs** -- single-sources the version from git tags into `src/geosphere_mcp_server/_version.py` (generated, gitignored)
- **uv** -- environment and dependency management (`uv sync`); also the recommended runtime launcher (`uvx`). `uv.lock` is committed.
- **`py.typed`** -- PEP 561 marker shipped inside the package so downstream consumers see the type hints
- **`server.json`** -- the MCP Registry manifest describing the published server. Its version field is
  rewritten by the release workflow, so it must stay in sync with the packaging metadata. The registry
  enforces a description of 100 characters or fewer.
- **`CHANGELOG.md`** -- maintained by hand per release and linked from the `pyproject.toml` project URLs

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
- **GeoSphere Austria Dataset API** (`https://dataset.api.hub.geosphere.at/v1`) -- keyless; rate limits
  5 req/s and 240 req/h. Out-of-bounds points return HTTP 400 with `"outside of dataset bounds"`.
  See [../domain/DATA-SOURCES-AND-COVERAGE.md](../domain/DATA-SOURCES-AND-COVERAGE.md) for the query
  shape and the dataset catalog.
- **Open-Meteo** (`https://api.open-meteo.com/v1/forecast`) -- keyless and free for non-commercial use,
  worldwide, up to 16 days; current, hourly, and daily variables including precipitation probability and
  WMO weather codes, with `timezone=auto`. It is the automatic fallback outside GeoSphere coverage and
  the sole source for the daily tool.

### CI/CD
- **GitHub Actions** -- `.github/workflows/validate.yml`
- Triggers: push to `main` and all pull requests
- Jobs: `ruff` (lint + format check), `test` (unit tests only), `gate` (fan-in that fails if either prior job fails; the single required status check)
- The `test` job installs via `uv sync --locked`, so it uses the committed `uv.lock` exactly and fails if
  the lock has drifted from `pyproject.toml`. This is what makes Dependabot's `uv.lock` bumps meaningful --
  an unlocked install would silently resolve different versions than the ones under review.
- Integration tests are excluded from CI
- Release pipeline (`release.yml` on `v*` tags), four jobs: `build` (`uv build` plus a tag/version
  consistency check) -> `pypi-publish` (Trusted Publishing over OIDC) -> then `github-release` and
  `mcp-registry` in **parallel**. The `mcp-registry` job rewrites the version in `server.json` and
  publishes with the `mcp-publisher` CLI.
- `auto-release.yml`: merged `dependabot/uv/*` PRs (or manual dispatch) cut the next patch tag
- `.github/dependabot.yml`: weekly grouped updates for Python dependencies (`uv` ecosystem) and GitHub
  Actions. Only the Python group feeds `auto-release.yml`.

### No Infrastructure
No Docker, Kubernetes, Terraform, or cloud platform configuration. Distributed as a PyPI package, run locally via `uvx`.

## Dependencies
- Runtime: `mcp[cli]>=2,<3`, `aiohttp>=3.0.0`, `astral>=3.2`
- The `mcp` major is bounded (`<3`) because the SDK breaks its high-level server API across majors:
  **mcp 2.0.0** (2026-07-28) removed `mcp.server.fastmcp` and replaced `FastMCP` with
  `mcp.server.MCPServer`. Bump the bound deliberately, not via Dependabot -- see the
  [migration guide](https://py.sdk.modelcontextprotocol.io/migration/).
- Dev: `pytest`, `pytest-asyncio`, `ruff` -- all in the `dev` group and pinned by `uv.lock`, so CI lints
  and tests with the same versions used locally
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
