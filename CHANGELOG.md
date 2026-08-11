# Changelog

The version of a release is derived from its git tag by `hatch-vcs`; there is no version string in the
source tree. Add entries under `## Unreleased` as you go — the release workflow moves them under the
version being cut, so you never rename that heading by hand. See
[docs/tech/RELEASING.md](docs/tech/RELEASING.md).

## Unreleased

- Added: `get_storm_outlook` -- peak wind gust for the next hour and the next 12 hours, whether a
  thunderstorm is expected within the next hour (tri-state, so a data gap reads `unknown` rather than
  `no`), when the next thunderstorm is expected across the whole horizon, and peak CAPE over 12 hours.
  Ported from `ha-geosphere-next` 0.9.0. Two semantics carry over and are documented in the output
  itself: horizons round up to whole hours, so the "next 1 h" window covers the hour already under way
  plus the next one; and the next-thunderstorm stamp can sit up to 59 minutes in the past, which means a
  storm is already in progress. An hour counts as a thunderstorm hour on the derived condition *or* on
  the raw CAPE/CIN predicate plus forecast precipitation -- the second branch catches thundersnow and
  hours with missing cloud cover, while the precipitation requirement keeps a dry high-CAPE afternoon
  from raising a signal. Inside GeoSphere coverage this costs one AROME request (the C-LAEF ensemble is
  skipped, since the outlook reports no precipitation probability); elsewhere it falls back to
  Open-Meteo, which supplies both CAPE and convective inhibition, so the same gate applies there.
- Added: `get_air_quality` -- NO₂, O₃, PM10 and PM2.5 surface concentrations plus the European Air
  Quality Index for today, tomorrow and in two days. GeoSphere's WRF-Chem forecast (`chem-v2-1h-3km` /
  `chem_aqi-v1-1d-3km`, 3 km grid) serves Austria and the Alps; elsewhere it falls back to Open-Meteo's
  CAMS air-quality API. The two sources publish different halves of the same scale -- GeoSphere the 1-6
  EEA band, Open-Meteo a 0-100+ numeric index and no daily value at all -- so the output always leads
  with the band, appends the numeric index only where one exists, and derives Open-Meteo's per-day figure
  as the maximum of that local day's hourly values. A daily-AQI failure degrades to concentrations only;
  a pollutant failure propagates.
- Changed: **behaviour change.** Thunder derivation now requires weak convective inhibition
  (`cin > -50` J/kg, `CAP_CIN_JKG`) in addition to CAPE >= 1000 J/kg, so high CAPE under a strong lid no
  longer produces `lightning` / `lightning-rainy`. AROME's `cin` parameter is now fetched for this. A
  missing `cin` counts as uncapped, which both preserves the previous behaviour and is what the
  Open-Meteo path depends on. Ported from `ha-geosphere-next` 0.9.0.
- Added: `cape` and `convective_inhibition` to the Open-Meteo hourly request, which the fallback storm
  outlook needs and which cost no extra call. Open-Meteo reports inhibition as a positive magnitude where
  AROME reports it negative, so the value is negated during normalization -- `is_thunder` only ever sees
  the AROME convention.
- Fixed: the hourly forecast (and with it the storm outlook) no longer drops the hour already under way.
  The API trims the forecast to the current hour and the first step has no predecessor for the
  accumulation deltas, so that hour was being skipped -- which silently broke every outlook window: the
  "next 1 h" window held one stamp instead of two, and a thunderstorm forecast for the current hour was
  invisible. One hour of history is now requested alongside the forecast,
  anchored to the top of the hour rather than to `now` (the API rounds `start` up to the next whole
  stamp, so `now - 1h` at 15:30 would come back as 15:00 and change nothing).
- Changed: the hourly forecast now starts with the hour already under way rather than the next one, so
  `hours=N` returns the in-progress hour plus `N - 1` later ones. This is a consequence of the lookback
  fix above and it aligns the GeoSphere path with the Open-Meteo path, which always included the current
  hour, and with `ha-geosphere-next`. The leading hour's precipitation figure covers the whole hour,
  including the part already elapsed.
- Changed: the hourly AROME and C-LAEF requests are now bounded to the window actually asked for
  instead of pulling the full ~60 h horizon every time. `hours=6` fetches 8 hourly steps rather than
  ~57, and an explicit `start` moves the fetched window with it. The bound carries an hour of slack at
  each end (one for the accumulation predecessor, one so rounding cannot clip the last requested hour).
  `get_storm_outlook` asks for the full horizon, so its thunderstorm scan is unaffected.
- Fixed: the current condition no longer reads cloud cover, CAPE and CIN from the hour *after* now. The
  AROME snapshot used for the fallback chain skipped index 0, copying the hourly path's need for an
  accumulation predecessor -- but every field it reads is instantaneous, and the API trims the series to
  the current hour. With the new CIN gate gating the thunder verdict, that meant a storm under way could
  be reported as plain rain because the *next* hour was capped.
- Fixed: `⚡ Next thunderstorm` now reports `unknown (no usable forecast hours)` instead of a confident
  `none in the forecast horizon` when no forecast hour ahead can be judged. The underlying scan returns
  the same empty result for "no storm" and "nothing readable here", so a response could declare the
  window `unknown` on one line and assert a 60-hour all-clear on the next.
- Fixed: a storm-outlook all-clear now names the horizon it covers (`none in the next 54 h`), and the
  Open-Meteo fallback is asked for three days rather than 48 h. Open-Meteo counts forecast days from local
  *midnight*, so a 48 h request at 20:00 left only ~28 h ahead and the scan reported an unqualified
  all-clear over it. `OUTLOOK_FALLBACK_HOURS` keeps at least 48 h ahead at any hour of the day, and the
  rendered span is measured from the rows that actually came back, so a short series still reports itself
  honestly. AROME is unaffected at ~60 h.
- Fixed: the European AQI bands are half-open, so an index of exactly 20 is now `fair` rather than `good`
  (and likewise at 40/60/80/100). `aqi_band` compared with `<=`, which put every boundary value one band
  too low.
- Fixed: a rate-limited `get_air_quality` no longer suffixes `— get_daily_forecast still works
  (Open-Meteo)`. That tool carries no air-quality data, so the pointer only bought the caller another
  wasted call. The suffix stays on current / hourly / storm outlook, where it is true.
- Fixed: `render_air_quality` now names its source even when neither concentrations nor an AQI came back.
  Which source drew the blank is what tells a caller whether asking elsewhere is worth anything. The EEA
  band legend is dropped in that case, having no figures left to explain.
- Fixed: `merge_air_quality` no longer builds a full per-pollutant hourly series. Nothing downstream read
  it, and it zipped the timestamp column against each value column with `strict=True` — so a response
  whose columns disagreed in length would have raised rather than degraded.
- Changed: `outlook.py` now takes windowing as the caller's job — `window()` is applied once per horizon
  and handed to `max_gust` / `max_cape` / `thunderstorm_outlook` — and `series_is_decidable` is folded
  into `scan_thunderstorm`, which returns the storm hour, its CAPE, and whether the scan counts from a
  single pass. One outlook now walks the series three times instead of six, and the decidability answer
  can no longer drift from the storm answer it qualifies.
- Changed: the four pollutant lists (`CHEM_PARAMETERS`, `CHEM_POLLUTANTS`,
  `OPENMETEO_AIR_QUALITY_VARIABLES`, `format._POLLUTANT_LABELS`) are now derived from one
  `AIR_QUALITY_POLLUTANTS` table in `const.py` instead of being hand-synced. Adding a pollutant is one row.
- Removed: the permanently-`None` `aqi_value_*` keys from `merge_air_quality` (GeoSphere publishes no
  numeric index, so the renderer supplies the `None`), and the unreferenced `CHEM_MAX_HOURS` constant.
- Docs: `outlook._is_lightning` states why only its CAPE/CIN branch requires precipitation — that
  requirement substitutes for the cloud-cover corroboration the derived-condition branch already carries,
  rather than being an inconsistency between the two. Behaviour is unchanged.
- Fixed: **behaviour change.** Every hourly row's precipitation, snow and wind gust now describes the hour
  the row is stamped for rather than the hour before it. AROME mixes two stampings and the assembly read
  both at the same index: `t2m`, `rh2m`, wind, `tcc`, `cape`, `cin` are instantaneous at the stamp, but
  `ugust`/`vgust` are the maximum "in the last forecast intervall" and `rr_acc`/`snow_acc` are
  run-accumulations, so a delta spans the interval *ending* at the stamp. A row could therefore show rain
  that had already stopped and the previous hour's gust peak — and because `_is_lightning` corroborates
  CAPE with precipitation, `get_storm_outlook` could call a thunderstorm that was already over. Interval
  parameters now come from the following step; the last forecast hour is dropped in exchange, having no
  successor to read them from.
- Fixed: the current conditions' AROME gust likewise came from the in-progress hour's stamp, whose gust
  covered the hour before it. It now reads the successor, so `get_current_weather` outside the
  nowcast/INCA grid reports the gust of the hour actually under way.
- Fixed: a `start` with a non-UTC offset no longer requests the wrong window. `async_get_timeseries`
  formatted the bound with `strftime`, dropping the offset, and the API reads naive stamps as UTC — so
  `start="2026-08-11T15:00+02:00"` fetched from 15:00 UTC and the caller silently lost the first two
  requested hours. Bounds are converted to UTC before serialization. The Open-Meteo path was already
  correct, so this also removes a divergence between the two source paths.
- Fixed: the Open-Meteo hourly fallback no longer returns nothing for a `start` in the future, and no
  longer under-delivers late in the day. `forecast_days` was `ceil(hours / 24)` and that API counts days
  from **local midnight**, not from now — so `hours=24` asked at 20:00 local yielded four hours, and a
  `start` two days out fell off the end of the response entirely. The request now accounts for the lead
  time to `start` and carries a day of headroom.
- Fixed: the Open-Meteo storm outlook ran its window and horizon arithmetic on naive local wall-clock, so
  a DST transition inside the window made the stated horizon 11 or 13 real hours — on a spring-forward
  night the "next 12 h" peak-gust scan silently lost an hour off the end. Rows are now resolved to
  instants in the point's own zone and compared in UTC, matching the GeoSphere path. (Attaching the zone
  alone is not enough: `aware + timedelta` is wall-clock arithmetic within that zone.)
- Fixed: `get_air_quality` falls back to Open-Meteo when an in-domain WRF-Chem response comes back empty,
  not only when the point is out of domain. The API answers HTTP 200 with an empty series when a run is
  stale or incomplete, so a location CAMS covers worldwide could dead-end on "No air-quality data
  available".
- Changed: `outlook._LIGHTNING_PREFIX` is gone in favour of `const.CONDITION_LIGHTNING`, which is the same
  string. A rename of the condition vocabulary would have left the predicate matching nothing, and
  `get_storm_outlook` reporting an all-clear through an actual storm.

## 0.3.2 - 2026-08-07

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
- Build: bump ruff in the python-dependencies group.

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
