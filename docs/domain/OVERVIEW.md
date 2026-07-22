# Domain Overview

## Purpose
Documents the business domain, core concepts, data sources, coverage model, condition derivation, terminology, and external integrations.

## Responsibilities
- Defining core domain concepts and their relationships
- Mapping feature boundaries and data ownership
- Documenting the GeoSphere/Open-Meteo coverage and fallback model
- Documenting condition derivation and precipitation probability
- Maintaining the domain terminology glossary and attribution

## Non-Responsibilities
- Technical architecture and module layout (see [../tech/ARCHITECTURE.md](../tech/ARCHITECTURE.md))
- API parsing implementation (see source in `src/geosphere_mcp_server/`)

## Overview

### Domain Classification
Read-only MCP gateway between LLMs and live weather data. Enables AI assistants (voice agents) to ask for current conditions and forecasts at any coordinate: high-resolution GeoSphere Austria data where available, Open-Meteo worldwide. Replaces an OpenWeatherMap MCP server, matching its tool names so agent-prompt routing transfers.

Industry: weather / developer tooling (AI integration).

### Data Sources

**GeoSphere Austria Dataset API** -- `https://dataset.api.hub.geosphere.at/v1`, keyless, rate-limited 5 req/s and 240 req/h. Point queries: `GET /timeseries/{mode}/{resource_id}?parameters=...&lat_lon={lat},{lon}&output_format=geojson`. A point outside a dataset's grid returns HTTP 400 with `"outside of dataset bounds"` in `detail` -- this is treated as a coverage signal, not an error, and triggers the Open-Meteo fallback.

| Dataset | Resource ID | Mode | Horizon / cadence | Coverage |
|---------|-------------|------|-------------------|----------|
| AROME forecast | `nwp-v1-1h-2500m` | `forecast` | ~60 h hourly | Austria + Alps |
| C-LAEF ensemble | `ensemble-v1-1h-2500m` | `forecast` | hourly (rr_p10/p50/p90) | Austria + Alps |
| INCA analysis | `inca-v1-1h-1km` | `historical` | hourly | Austria only |
| INCA nowcast | `nowcast-v1-15min-1km` | `forecast` | 15-min | Austria only |

**No GeoSphere dataset forecast extends beyond ~60 h.** Long-range and daily forecasts always use Open-Meteo.

**Open-Meteo** -- `https://api.open-meteo.com/v1/forecast`, keyless/free (non-commercial), worldwide, up to 16 days. Supplies current + hourly + daily variables including precipitation probability and WMO weather codes, with `timezone=auto`. It is the automatic fallback for the current and hourly tools when a point is outside GeoSphere coverage, and the sole source for the daily tool.

### Coverage and Fallback Model
Coverage is discovered dynamically -- there is no hardcoded bounding box. A GeoSphere fetch is attempted; if it returns out-of-domain, the tool falls back to Open-Meteo. Three coverage classes:

1. **Austria** -- INCA analysis/nowcast + AROME available; current conditions merge across all sources.
2. **Alps outside Austria** -- inside the AROME grid but outside INCA/nowcast; AROME-only snapshot.
3. **Worldwide** -- outside AROME; Open-Meteo fallback (the drop-in OWM replacement role).

Every response names its data source (e.g. `📡 Source: GeoSphere (INCA + AROME)` or `📡 Source: Open-Meteo`).

### Tools

| Tool | Signature | Sources | Horizon |
|------|-----------|---------|---------|
| `get_current_weather` | `(latitude, longitude) -> str` | GeoSphere merge, else Open-Meteo | now |
| `get_hourly_forecast` | `(latitude, longitude, hours=24, start=None) -> str` | GeoSphere AROME (+C-LAEF), else Open-Meteo | 1-60 h (GeoSphere) / 1-48 h (fallback) |
| `get_daily_forecast` | `(latitude, longitude, days=7) -> str` | Open-Meteo only | 1-16 days |

Location input is plain decimal `latitude`/`longitude` -- the calling LLM geocodes place names; there is no geocoder in the server. Output is compact emoji-markdown in metric units.

### Current-Conditions Merge Chain
On the GeoSphere path, fields are filled from a per-field fallback chain (ported from `ha-geosphere-next`):
- temp / humidity / wind speed+bearing: INCA -> nowcast -> AROME
- dew point: INCA -> nowcast
- gust: nowcast -> AROME
- pressure (`P0` Pa->hPa) and global radiation: INCA only
- cloud cover / CAPE: AROME
- 1-h precipitation: INCA `RR`, else sum of the last four nowcast 15-min `rr` buckets
- precipitation flag: nowcast `pt` (255 = none)

### Condition Vocabulary
One shared vocabulary across all tools -- the Home Assistant condition set: `sunny`, `clear-night`, `partlycloudy`, `cloudy`, `rainy`, `pouring`, `lightning`, `lightning-rainy`, `snowy`, `snowy-rainy`, `windy`, `windy-variant`, `fog`.

- **GeoSphere paths** derive it physically (ported `condition.py`), with thresholds in `const.py`: CAPE >= 1000 J/kg (lightning), precip >= 0.1 mm (rainy), pouring >= 4 mm/h, gust >= 15 m/s (windy), cloud 12.5 % / 62.5 % breakpoints, fog when RH >= 98 % & wind < 2 m/s & cloud >= 87.5 %, snow when T <= 1 degC. Day/night (sunny vs clear-night) via `astral`.
- **Open-Meteo paths** map the WMO `weather_code` (0-99) to the same vocabulary via a static dict in `const.py`, combined with `is_day` for sunny/clear-night.

### Precipitation Probability (POP)
Derived from the C-LAEF ensemble as a stepped value matched by exact timestamp: p10 wet -> 95 %, p50 wet -> 70 %, p90 wet -> 30 %, otherwise 0 %. On the Open-Meteo path, `precipitation_probability` is used directly. Ensemble fetch failure simply omits probability (secondary dataset degrades gracefully).

### Terminology Glossary

| Term | Definition |
|------|-----------|
| **MCP** | Model Context Protocol -- how LLMs invoke this server's tools (stdio transport) |
| **GeoSphere** | GeoSphere Austria -- the national geophysical/meteorological service and its Dataset API |
| **AROME** | High-resolution (2.5 km) numerical weather prediction model, ~60 h forecast |
| **INCA** | Integrated Nowcasting through Comprehensive Analysis -- 1 km gridded analysis + nowcast, Austria only |
| **C-LAEF** | Convection-permitting Limited Area Ensemble Forecasting -- provides precipitation percentiles (rr_p10/p50/p90) |
| **nowcast** | Very short-range (15-min cadence) forecast from INCA |
| **POP** | Probability of precipitation -- stepped value derived from ensemble percentiles |
| **WMO code** | World Meteorological Organization present-weather code (0-99), used by Open-Meteo |
| **out-of-domain** | GeoSphere 400 with "outside of dataset bounds" -- a coverage signal that triggers the Open-Meteo fallback |
| **condition** | An HA-style weather condition string (e.g. `partlycloudy`), the shared output vocabulary |

### External Integrations
- **GeoSphere Austria Dataset API** -- primary source for Austria/Alps current + hourly; keyless, rate-limited, versioned resource IDs.
- **Open-Meteo** -- worldwide fallback + sole daily source; keyless.

### Attribution & Compliance
Both data sources are licensed **CC-BY 4.0** and must be attributed: **GeoSphere Austria** and **Open-Meteo**. Read-only server; no user data stored, no PII, no payment processing. All data is publicly available weather information.

## Dependencies
- GeoSphere Austria Dataset API and Open-Meteo are the only external data sources
- Daily forecasts depend solely on Open-Meteo

## Design Decisions
- **Tool names mirror the OWM server** (`get_current_weather`, `get_hourly_forecast`, `get_daily_forecast`) so existing agent routing prompts transfer unchanged.
- **Dynamic coverage detection**: attempt GeoSphere, fall back on out-of-domain -- no maintained bounding box.
- **Physically derived conditions**: the condition comes from physical parameters, not GeoSphere's proprietary symbol code, and shares one vocabulary with the WMO-code mapping.
- **Emoji-markdown output**: compact, voice-agent-friendly, metric units.

## Known Risks
- GeoSphere resource IDs are versioned -- a catalog rotation breaks the primary path until IDs in `const.py` are bumped.
- No GeoSphere forecast beyond ~60 h -- daily/long-range must use Open-Meteo.
- Shared GeoSphere rate limits (5 req/s, 240 req/h) with no server-side quota tracking.
- `condition.py` duplicates HA condition strings to stay import-free -- could drift if HA renames a condition.

## Extension Guidelines
- New domain feature: add a tool in `server.py` + orchestration in `weather.py` + renderer in `format.py`
- New GeoSphere dataset: add its resource ID and parameter list to `const.py`, wire fetch in `geosphere_api.py`
- Update this glossary and the attribution note when introducing new sources or terms
