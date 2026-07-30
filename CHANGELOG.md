# Changelog

The version of a release is derived from its git tag by `hatch-vcs`; there is no version string in the
source tree. Add entries under `## Unreleased` as you go — the release workflow moves them under the
version being cut, so you never rename that heading by hand. See
[docs/tech/RELEASING.md](docs/tech/RELEASING.md).

## Unreleased

- Added: `tests/test_server_json.py` validates `server.json` against the MCP Registry's publish
  constraints -- description length, package identifier, transport, OIDC namespace, and the `mcp-name:`
  ownership marker in the README. The registry only validates at publish time, after the PyPI upload has
  succeeded and the tag is immovable, so a rejection there cannot be re-run and costs a version number;
  the sibling `calc-mcp-server` burned two that way. This repo already satisfies every check (the
  description is 98 characters, two under the cap) -- the tests stop a future edit crossing it.
- Added: `.python-version` pinning local development to 3.12, the version CI installs. Without it `uv`
  picks the newest interpreter present, so local and CI silently diverge -- stdlib `math` error messages
  were reworded after 3.12, so a test asserting on them passes locally and fails in CI.
- Changed: `auto-release.yml` now passes `client-id` to `actions/create-github-app-token` instead of the
  deprecated `app-id`, reading a new `GH_ACTION_APP_CLIENT_ID` secret. Every run warned
  `Input 'app-id' has been deprecated`; the token it mints is what pushes the changelog commit past the
  `main` ruleset, so the input will not be left to be removed on the action's schedule.

## 0.3.1 - 2026-07-29

- Added: releases now file their own changelog section. `auto-release.yml` runs `scripts/changelog_release.py` to move `## Unreleased` entries under the version being cut and append a `- Build:` line per Dependabot commit, then commits that before tagging. Previously it tagged without touching `CHANGELOG.md`, which is why 0.1.1, 0.1.2 and 0.2.1 were published with no section at all.
- Added: the Auto Release workflow takes an optional `version` input, so a deliberate minor or major release is one click instead of a hand-made tag. It also refuses a version whose tag already exists.
- Added: `docs/tech/RELEASING.md` — the release process was previously undocumented, which is the other half of why the changelog drifted.
- Added: an advisory `Changelog reminder` CI job that warns when a PR changes `src/` without `CHANGELOG.md`. It never blocks a merge and skips Dependabot and `no-changelog`-labelled PRs.
- Fixed: `docs/tech/RELEASING.md` claimed `main` was unprotected. It is protected by an active repository ruleset; the earlier check used the classic branch-protection API, which returns 404 here and reads as "not protected". The release App is now a ruleset bypass actor, without which the changelog push fails `GH013`.

## 0.3.0 - 2026-07-29

- Changed: migrated to the mcp Python SDK v2 (`mcp[cli]>=2,<3`). `mcp.server.fastmcp.FastMCP` became `mcp.server.MCPServer`; the `@mcp.tool()` and `mcp.run()` surface is unchanged, and `@mcp.tool()` still returns the plain undecorated function, so `tests/test_server.py` keeps calling the tools directly. This lifts the temporary `<2` pin added in 0.2.1, so Dependabot can track the v2 line again.
- Fixed: the server now advertises its own package version over the wire. v1's `FastMCP` had no `version` parameter and reported the *SDK* version (`1.28.1`) as the server version; v2 added the parameter but defaults it to an empty string, so passing `__version__` explicitly is what keeps the field meaningful.
- Fixed: the CI `ruff` job now installs from the committed `uv.lock` (`ruff` moved into the `dev` dependency group) instead of an unpinned `pip install ruff`. Same class of drift as the `mcp` bug in 0.2.1: a ruff release could fail the build with no change to this repo, and CI could lint with a different version than any developer.

## 0.2.1 - 2026-07-29

- Fixed: constrained `mcp[cli]` to `>=1.28.1,<2`. The mcp 2.0.0 release (2026-07-28) reworked the SDK and removed `mcp.server.fastmcp`, so any fresh install resolved a version this server cannot import. CI caught it as an unrelated-looking `ModuleNotFoundError` on a dependency-bump PR. (Superseded by the v2 migration in 0.3.0.)
- Changed: the CI `test` job now installs with `uv sync --locked` instead of `pip install -e .`. The previous command ignored the committed `uv.lock` entirely, so dependency bumps were never actually exercised by CI and unpinned upstream releases could break the build without any change to this repo.
- Fixed: a request that timed out reported `⚠️ No weather data available` instead of `⚠️ Timeout fetching weather data`. Both API clients wrapped `TimeoutError` into their own connection error, which does not subclass `TimeoutError`, so the server's timeout branch was never reached. Each client now raises a dedicated `GeoSphereTimeoutError` / `OpenMeteoTimeoutError` (both still subclasses of the respective connection error), and the server catches those alongside the builtin.
- Docs: split the domain docs per concept (`DATA-SOURCES-AND-COVERAGE.md`, `CONDITION-DERIVATION.md`, `OUTPUT-CONTRACT.md`), reducing `OVERVIEW.md` to an index, and corrected claims that had drifted from the code — notably the then-undocumented hourly day-divider headers and the `get_daily_forecast` date-range validation.
- Build: bumped `aiohttp` to 3.14.3.

## 0.2.0 - 2026-07-23

- `get_daily_forecast` now accepts an explicit calendar range via optional `start_date`/`end_date` (ISO `YYYY-MM-DD`) in addition to the `days`-from-today count. The range takes precedence, defaults `end_date` to `start_date` (single day), and is capped at 16 days. This lets a caller answer a named period ("the weekend", "next Tuesday") by passing the exact dates instead of converting the period into an error-prone day count.

## 0.1.2 - 2026-07-22

- The hourly forecast now emits a `%a %Y-%m-%d` day-divider header whenever the local date changes, with the hours indented beneath it. Previously rows carried time-of-day only, so a sequence crossing midnight (23:00 -> 00:00) left the reader counting rollovers — and the date could not be inferred from the header, whose reference timestamp is the model run time rather than the first data row.

## 0.1.1 - 2026-07-22

- Fixed: shortened the `server.json` description to <=100 characters, the MCP Registry's limit, which was rejecting the publish step.

## 0.1.0 - 2026-07-22

- Initial release
- MCP server with 3 tools: `get_current_weather`, `get_hourly_forecast`, `get_daily_forecast`
- High-resolution weather for Austria and the Alps from the GeoSphere Austria Dataset API: AROME forecast (~60 h hourly), INCA analysis, INCA nowcast, and C-LAEF ensemble precipitation probability
- Automatic worldwide fallback to Open-Meteo when a point is outside GeoSphere coverage (current + hourly tools); daily forecast (1-16 days) always via Open-Meteo
- Location input is plain decimal `latitude`/`longitude` (the calling LLM geocodes place names)
- HA-style condition vocabulary derived physically on the GeoSphere path and mapped from WMO weather codes on the Open-Meteo path
- Compact emoji-markdown output (metric units), each response naming its data source
- First public distribution: published to [PyPI](https://pypi.org/project/geosphere-mcp-server/) (installable via `uvx geosphere-mcp-server`) and listed in the [official MCP Registry](https://registry.modelcontextprotocol.io)
- Tag-driven release pipeline: PyPI Trusted Publishing (OIDC), GitHub Release, and MCP Registry publish on `v*` tags
- Version single-sourced from git tags via `hatch-vcs`; PyPI metadata (authors, URLs, classifiers, keywords) and a `py.typed` marker
