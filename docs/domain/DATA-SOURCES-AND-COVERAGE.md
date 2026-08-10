# Data Sources and Coverage

## Purpose
Documents the two upstream weather data sources, their datasets and limits, the dynamic coverage model,
the fallback rules, and the attribution obligations that come with the data.

## Responsibilities
- Cataloguing the GeoSphere Austria datasets and the Open-Meteo endpoint
- Defining the coverage classes and the transparent fallback model
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

A point outside a dataset's grid returns HTTP 400 with `"outside of dataset bounds"` in `detail`. This is
treated as a coverage signal, not an error, and triggers the Open-Meteo fallback.

| Dataset | Resource ID | Mode | Horizon / cadence | Coverage |
|---------|-------------|------|-------------------|----------|
| AROME forecast | `nwp-v1-1h-2500m` | `forecast` | ~60 h hourly | Austria + Alps |
| C-LAEF ensemble | `ensemble-v1-1h-2500m` | `forecast` | hourly (rr_p10/p50/p90) | Austria + Alps |
| INCA analysis | `inca-v1-1h-1km` | `historical` | hourly | Austria only |
| INCA nowcast | `nowcast-v1-15min-1km` | `forecast` | 15-min | Austria only |
| WRF-Chem pollutants | `chem-v2-1h-3km` | `forecast` | ~73 h hourly | Austria + Alps |
| WRF-Chem daily AQI | `chem_aqi-v1-1d-3km` | `forecast` | daily (EEA band 1-6) | Austria + Alps |

Resource IDs, the per-dataset parameter lists, and the 30 s request timeout live in `const.py`.
`AROME_MAX_HOURS = 60` bounds the GeoSphere hourly horizon.

**No GeoSphere dataset forecast extends beyond ~60 h.** Long-range and daily forecasts always use
Open-Meteo.

### Open-Meteo
Base URL `https://api.open-meteo.com/v1/forecast`. Keyless and free for non-commercial use, worldwide, up
to 16 days. Supplies current, hourly, and daily variables including precipitation probability and WMO
weather codes, with `timezone=auto`. Wind is requested in m/s so both paths share one unit. The request
timeout is 15 s.

It serves two roles: the automatic fallback for the current, hourly, and storm-outlook tools outside
GeoSphere coverage, and the sole source for the daily tool everywhere. `OPENMETEO_MAX_HOURS = 48` and
`OPENMETEO_MAX_DAYS = 16` bound its horizons.

The forecast endpoint publishes both `cape` and `convective_inhibition`, which the storm outlook uses —
but with the **opposite sign convention** to AROME (a positive magnitude rather than a negative one), so
the value is negated during normalization. See [STORM-OUTLOOK.md](STORM-OUTLOOK.md).

**Air quality lives on a separate host**: `https://air-quality-api.open-meteo.com/v1/air-quality`, also
keyless, serving CAMS data. Same request and response shape, so it reuses the same client plumbing. Three
forecast days are requested; unlike GeoSphere it publishes no daily index — see
[AIR-QUALITY.md](AIR-QUALITY.md).

The daily endpoint has two request modes: a forward-looking day count (`forecast_days`) or an explicit
inclusive calendar range (`start_date`/`end_date`). The explicit range takes precedence when supplied.

### Coverage Classes and Fallback
Coverage is discovered dynamically — there is no hardcoded bounding box. A GeoSphere fetch is attempted;
if it reports out-of-domain, the tool falls back to Open-Meteo. Three coverage classes result:

1. **Austria** — INCA analysis and nowcast plus AROME available; current conditions merge across all
   sources.
2. **Alps outside Austria** — inside the AROME grid but outside INCA/nowcast; AROME-only snapshot.
3. **Worldwide** — outside AROME; Open-Meteo fallback (the drop-in replacement role).

Every response names the source that served it. The exact wording and formatting differ per tool — see
[OUTPUT-CONTRACT.md](OUTPUT-CONTRACT.md).

### Graceful Degradation Paths
Three distinct signals let a call degrade instead of failing:

1. **Out-of-domain (HTTP 400)** — a coverage signal; transparently falls back to Open-Meteo.
2. **Rate limit (HTTP 429)** — the server retries once when the API asks for a wait of 5 s or less,
   otherwise it returns a rate-limit notice. The notice reminds the caller that `get_daily_forecast`
   still works, because daily is always Open-Meteo and never GeoSphere.
3. **Secondary fetch failure** — the secondary dataset is dropped and the call still renders. A C-LAEF
   failure omits the precipitation probability (see
   [CONDITION-DERIVATION.md](CONDITION-DERIVATION.md)); a daily-AQI failure keeps the pollutant
   concentrations (see [AIR-QUALITY.md](AIR-QUALITY.md)). In both cases the primary dataset failing
   propagates instead.

### Attribution and Compliance
Both data sources are licensed **CC-BY 4.0** and must be attributed: **GeoSphere Austria** and
**Open-Meteo**. The server is read-only: no user data is stored, no PII is handled, and no payments are
processed. All data served is publicly available weather information.

## Dependencies
- GeoSphere Austria Dataset API and Open-Meteo are the only external data sources
- Daily forecasts depend solely on Open-Meteo
- Dataset resource IDs, parameter lists, horizons, and timeouts are all centralized in `const.py`

## Design Decisions
- **Dynamic coverage detection over a maintained bounding box**: attempt GeoSphere, fall back on
  out-of-domain. The grids evolve; a hardcoded box would drift silently.
- **Open-Meteo as the sole daily source**: no GeoSphere dataset reaches beyond ~60 h, so routing daily
  through one worldwide source keeps the tool uniform everywhere.
- **Metric units end to end**: Open-Meteo wind is requested in m/s to match GeoSphere, so the renderers
  never convert per source.

## Known Risks
- GeoSphere resource IDs are versioned — a catalog rotation breaks the primary path until the IDs in
  `const.py` are bumped. Only the integration tests catch this.
- Shared GeoSphere rate limits (5 req/s, 240 req/h) apply across all callers, with no server-side quota
  tracking.
- Open-Meteo's free tier is licensed for non-commercial use; commercial deployment needs a different plan.
- `INCA_MAX_AGE_SECONDS` in `const.py` is defined but unreferenced — a leftover from the
  `ha-geosphere-next` port implying a staleness check that this stateless server does not perform.

## Extension Guidelines
- New GeoSphere dataset: add its resource ID and parameter list to `const.py`, then wire the fetch in
  `geosphere_api.py`.
- New coverage class: prefer extending the dynamic fallback chain over introducing a bounding box.
- Update the attribution note above whenever a new upstream source is introduced.
