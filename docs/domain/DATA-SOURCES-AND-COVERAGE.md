# Data Sources and Coverage

## Purpose
Documents the upstream weather data source, its datasets and limits, the dynamic coverage model, and the
attribution obligations that come with the data.

## Responsibilities
- Cataloguing the GeoSphere Austria datasets
- Defining the coverage classes and what happens outside them
- Documenting the graceful-degradation paths (out-of-domain, rate limit, secondary-fetch failure)
- Recording licensing and attribution requirements

## Non-Responsibilities
- How a condition string is derived from the data (see [CONDITION-DERIVATION.md](CONDITION-DERIVATION.md))
- The rendered output contract per tool (see [OUTPUT-CONTRACT.md](OUTPUT-CONTRACT.md))
- HTTP client implementation and error taxonomy (see [../tech/ARCHITECTURE.md](../tech/ARCHITECTURE.md))

## Overview

### GeoSphere Austria Dataset API
Base URL `https://dataset.api.hub.geosphere.at/v1`. Keyless, rate-limited to 5 req/s and 240 req/h.
Point queries use
`GET /timeseries/{mode}/{resource_id}?parameters=...&lat_lon={lat},{lon}&output_format=geojson`.

A point outside a dataset's grid returns HTTP 400 with `"outside of dataset bounds"` in `detail`. The
client maps this to `GeoSphereOutOfDomainError`, which the server renders as its own out-of-coverage line
rather than as a generic failure — see *Coverage Classes* below.

| Dataset | Resource ID | Mode | Horizon / cadence | Coverage |
|---------|-------------|------|-------------------|----------|
| AROME forecast | `nwp-v2-1h-1km` | `forecast` | ~60 h hourly, reruns every 3 h | Austria + Alps |
| C-LAEF ensemble | `ensemble-v2-1h-1km` | `forecast` | ~60 h hourly, reruns every 3 h (`tp_p10/p50/p90`, `cape_p90`) | Austria + Alps |
| INCA analysis | `inca-v1-1h-1km` | `historical` | hourly | Austria only |
| INCA nowcast | `nowcast-v1-15min-1km` | `forecast` | 15-min | Austria only |
| WRF-Chem pollutants | `chem-v2-1h-3km` | `forecast` | ~73 h hourly | Austria + Alps |
| WRF-Chem daily AQI | `chem_aqi-v1-1d-3km` | `forecast` | daily (EEA band 1-6) | Austria + Alps |

Resource IDs, the per-dataset parameter lists, and the 30 s request timeout live in `const.py`.
`AROME_MAX_HOURS = 60` bounds the GeoSphere hourly horizon.

### The forecast datasets

"AROME" names the deterministic forecast throughout the code and the output. The dataset itself,
`nwp-v2-1h-1km`, is the deterministic control run of GeoSphere's C-LAEF AlpeAdria ensemble on a 1 km grid
(latitude 43.0–51.5°, longitude 5.03–22.57°): 61 hourly stamps from the run's t0. `ensemble-v2-1h-1km`
is that ensemble on the same grid, publishing only p10/p50/p90 percentiles — no member counts.

Requested parameters (`AROME_PARAMETERS`): `2t` (°C), `2r` (%), `10u`/`10v` (wind components),
`10fg` (peak 10 m gust, m/s), `tcc` (cloud cover, **%**), `tp` (per-hour precipitation, kg m⁻²),
`snowlmt` (m), `cape` (J/kg, declared `m2 s-2`, which is the same unit) and `sy`
(GeoSphere's weather-symbol code — see [CONDITION-DERIVATION.md](CONDITION-DERIVATION.md)). From the
ensemble (`ENSEMBLE_PARAMETERS`): `tp_p10`, `tp_p50`, `tp_p90` (kg m⁻²) and `cape_p90` (J/kg). There is
no convective inhibition and no run-accumulated series. The dataset also publishes `msl`, `pt` (a
precipitation-type code distinct from the nowcast's), `rain`, `sf`, `ssrd` and `sund`, which are not
requested.

Each parameter either holds at its stamp or covers the interval **ending** at it, and which is decided by
measurement, not by name: requested from the run's own t0, an interval parameter is `null` at index 0.
`10fg`, `tp`, `sy` and the `tp` percentiles are interval-ending, so a row stamped `T` reads them at
the *next* stamp to describe the hour `T..T+1h`; `2t`, `2r`, `10u`/`10v`, `tcc`, `cape`, `snowlmt` and
`cape_p90` are instantaneous and read at `T`. `scripts/record_fixtures.py` prints this classification, and
the recorded fixtures pin it — see [../tech/TESTING.md](../tech/TESTING.md). A new parameter has to be
classified the same way before it is wired in; getting it wrong shifts that field by an hour, silently.

**Publication lag.** A run's `reference_time` is the hour it is stamped for, not when it becomes
available. GeoSphere publishes the 3-hourly runs several hours later, the ensemble later than the
deterministic run, so the newest ensemble run in service is routinely 6–9 h old. Two consequences: the
remaining horizon from *now* is typically ~52–55 h rather than 60, and the storm outlook allows the
ensemble up to 12 h of run age (`ENSEMBLE_MAX_AGE`, see [STORM-OUTLOOK.md](STORM-OUTLOOK.md)).

Hourly requests carry both a `start` and an `end`: one hour of history (so the series is guaranteed to
reach the hour in progress, which the API's round-up of `start` would otherwise skip past) through two
hours past the window the caller asked for — one because the last hour's interval parameters live on the
following stamp, one so rounding at the boundary cannot clip that successor. A 6-hour request therefore
transfers 9 hourly steps rather than the full horizon. A `start` before the run's t0 comes back at t0.
`get_storm_outlook` asks for `AROME_MAX_HOURS`, so its scan still spans everything AROME publishes.

Bounds are serialized as naive stamps, which the API reads as UTC, so an aware `start`/`end` is converted
to UTC first — formatting it directly would drop the offset and shift the whole window.

**No GeoSphere dataset forecast extends beyond ~60 h**, and there is no second source behind it, so the
longest answer this server can give is roughly two and a half days of hourly rows. There is no daily or
long-range tool.

### Coverage Classes
Coverage is discovered dynamically — there is no hardcoded bounding box. A GeoSphere fetch is attempted
and its own out-of-domain answer decides. Three coverage classes result:

1. **Austria** — INCA analysis and nowcast plus AROME available; current conditions merge across all
   sources.
2. **Alps outside Austria** — inside the AROME grid but outside INCA/nowcast; AROME-only snapshot. The
   two fields that only observations can supply — the last hour's precipitation and the "is it
   precipitating" flag — are absent here rather than guessed at.
3. **Everywhere else** — outside AROME, and therefore **not served**. Every tool answers with the same
   out-of-coverage line (`server.OUT_OF_DOMAIN_MESSAGE`).

That line is deliberately distinct from the retryable failure lines. Being outside coverage is a permanent
property of the location, so a caller that cannot tell the two apart will retry forever or give up on a
transient blip. It is produced once, in `server._guarded`, rather than per tool.

Every response names the datasets that served it. The exact wording and formatting differ per tool — see
[OUTPUT-CONTRACT.md](OUTPUT-CONTRACT.md).

### Graceful Degradation Paths
Three distinct signals let a call degrade instead of failing:

1. **Out-of-domain (HTTP 400)** — a coverage signal, rendered as the out-of-coverage line above rather
   than as an error. Not a degradation so much as an honest refusal.
2. **Rate limit (HTTP 429)** — the server retries once when the API asks for a wait of 5 s or less,
   otherwise it returns a rate-limit notice.
3. **Secondary fetch failure** — the secondary dataset is dropped and the call still renders. A C-LAEF
   failure omits the precipitation chance (see [CONDITION-DERIVATION.md](CONDITION-DERIVATION.md)) and
   leaves the storm outlook on the weather symbol alone (see [STORM-OUTLOOK.md](STORM-OUTLOOK.md)); a
   daily-AQI failure keeps the pollutant concentrations (see [AIR-QUALITY.md](AIR-QUALITY.md)). In both
   cases the primary dataset failing propagates instead.

### Attribution and Compliance
The data is licensed **CC-BY 4.0** and must be attributed to **GeoSphere Austria**. The server is
read-only: no user data is stored, no PII is handled, and no payments are processed. All data served is
publicly available weather information.

## Dependencies
- The GeoSphere Austria Dataset API is the only external data source
- Dataset resource IDs, parameter lists, horizons, and timeouts are all centralized in `const.py`

## Design Decisions
- **Dynamic coverage detection over a maintained bounding box**: attempt the fetch and let the API's
  out-of-domain answer decide. The grids evolve; a hardcoded box would drift silently.
- **One source, no worldwide fallback**: an Open-Meteo fallback existed through v0.3.x and was removed.
  It doubled every tool — two normalizers, two condition vocabularies, two AQI scales, two convective
  inhibition sign conventions — for a second-rate answer outside the region this server exists to serve.
  Callers that need worldwide coverage are better served by pairing this with a dedicated global source
  than by having one hidden behind it.
- **Metric units end to end**: everything is m/s, °C, mm and hPa as GeoSphere publishes it, so the
  renderers never convert.

## Known Risks
- GeoSphere resource IDs are versioned — a catalog rotation breaks the primary path until the IDs in
  `const.py` are bumped. Only the integration tests catch this. A retired dataset does not necessarily
  fail loudly: `nwp-v1-1h-2500m` kept answering HTTP 200 with all-null data after its last run, which
  renders as `n/a` rows with no condition — the Vienna integration test asserts against exactly that.
- Shared GeoSphere rate limits (5 req/s, 240 req/h) apply across all callers, with no server-side quota
  tracking.

## Extension Guidelines
- New GeoSphere dataset: add its resource ID and parameter list to `const.py`, then wire the fetch in
  `geosphere_api.py`.
- New coverage class: prefer letting the API's out-of-domain answer decide over introducing a bounding
  box.
- Update the attribution note above whenever a new upstream source is introduced.
