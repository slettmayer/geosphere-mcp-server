# Domain Overview

## Purpose
Indexes the domain documentation, classifies the domain, catalogues the core concepts, and holds the
terminology glossary and cross-cutting domain decisions.

## Responsibilities
- Classifying the domain and its audiences
- Cataloguing core domain concepts with a pointer to the file that owns each
- Maintaining the terminology glossary
- Recording cross-cutting design decisions and risks that span more than one concept

## Non-Responsibilities
- Detail on any single concept — each is owned by a sub-file listed below
- Technical architecture and module layout (see [../tech/ARCHITECTURE.md](../tech/ARCHITECTURE.md))
- API parsing implementation (see the source in `src/geosphere_mcp_server/`)

## Overview

### Domain Classification
A read-only gateway between language models and live weather data. It lets AI assistants ask for current
conditions and forecasts at any coordinate: high-resolution GeoSphere Austria data where available,
Open-Meteo worldwide. It replaces an OpenWeatherMap MCP server and keeps that server's tool names so agent
routing prompts transfer unchanged.

Industry: weather and developer tooling for AI integration.

### API Surfaces and Audiences
The server exposes exactly one inbound surface, and it is unauthenticated because it is local.

| Surface | Audience | Transport and auth | Detail |
|---------|----------|--------------------|--------|
| Five MCP tools (current, hourly, daily, storm outlook, air quality) | The user's AI assistant | MCP over stdio; no auth, launched locally by the client | [OUTPUT-CONTRACT.md](OUTPUT-CONTRACT.md) |
| Outbound to the GeoSphere Austria Dataset API | Upstream provider | HTTPS, keyless, rate-limited | [DATA-SOURCES-AND-COVERAGE.md](DATA-SOURCES-AND-COVERAGE.md) |
| Outbound to Open-Meteo | Upstream provider | HTTPS, keyless | [DATA-SOURCES-AND-COVERAGE.md](DATA-SOURCES-AND-COVERAGE.md) |

There is no HTTP server, no user-facing dashboard, and no operator surface. The MCP `instructions` string
registered in `server.py` is itself part of the surface: it is how a model decides which tool to call.

### Core Concepts

| Concept | One-liner | Owned by |
|---------|-----------|----------|
| Data sources | The GeoSphere dataset catalog and the Open-Meteo endpoint | [DATA-SOURCES-AND-COVERAGE.md](DATA-SOURCES-AND-COVERAGE.md) |
| Coverage and fallback | Dynamic coverage detection and the three coverage classes | [DATA-SOURCES-AND-COVERAGE.md](DATA-SOURCES-AND-COVERAGE.md) |
| Graceful degradation | Out-of-domain, rate limit, and secondary-fetch-failure paths | [DATA-SOURCES-AND-COVERAGE.md](DATA-SOURCES-AND-COVERAGE.md) |
| Condition vocabulary | The shared Home Assistant condition set both paths emit | [CONDITION-DERIVATION.md](CONDITION-DERIVATION.md) |
| Condition derivation | Physical derivation on GeoSphere, WMO mapping on Open-Meteo | [CONDITION-DERIVATION.md](CONDITION-DERIVATION.md) |
| Merge chain | Per-field source preference for current conditions | [CONDITION-DERIVATION.md](CONDITION-DERIVATION.md) |
| Precipitation probability | Stepped value from the C-LAEF ensemble percentiles | [CONDITION-DERIVATION.md](CONDITION-DERIVATION.md) |
| Thunder gate | CAPE gated by convective inhibition, and what a missing CIN means | [CONDITION-DERIVATION.md](CONDITION-DERIVATION.md) |
| Storm outlook | Peak gusts, next thunderstorm, and the round-up window semantics | [STORM-OUTLOOK.md](STORM-OUTLOOK.md) |
| Air quality | Pollutant concentrations and the European AQI band scale | [AIR-QUALITY.md](AIR-QUALITY.md) |
| Tool contract | Signatures, defaults, clamping, and validation errors | [OUTPUT-CONTRACT.md](OUTPUT-CONTRACT.md) |
| Output contract | Rendered fields, attribution lines, day dividers, error lines | [OUTPUT-CONTRACT.md](OUTPUT-CONTRACT.md) |

### Terminology Glossary

| Term | Definition |
|------|-----------|
| **MCP** | Model Context Protocol — how models invoke this server's tools (stdio transport) |
| **GeoSphere** | GeoSphere Austria — the national geophysical and meteorological service, and its Dataset API |
| **AROME** | High-resolution (2.5 km) numerical weather prediction model, ~60 h forecast |
| **INCA** | Integrated Nowcasting through Comprehensive Analysis — 1 km gridded analysis and nowcast, Austria only |
| **C-LAEF** | Convection-permitting Limited Area Ensemble Forecasting — supplies precipitation percentiles (rr_p10/p50/p90) |
| **WRF-Chem** | Weather Research and Forecasting model coupled with Chemistry — GeoSphere's 3 km air-quality forecast |
| **CAMS** | Copernicus Atmosphere Monitoring Service — the air-quality model behind Open-Meteo's worldwide fallback |
| **CAPE** | Convective Available Potential Energy (J/kg) — how much energy convection could release |
| **CIN** | Convective Inhibition (J/kg, published negative) — the lid holding convection down; gates CAPE in the thunder test |
| **EEA band** | The European Air Quality Index's six-step scale (1 good to 6 extremely poor) |
| **nowcast** | Very short-range (15-min cadence) forecast from INCA |
| **POP** | Probability of precipitation — a stepped value derived from ensemble percentiles |
| **WMO code** | World Meteorological Organization present-weather code (0-99), used by Open-Meteo |
| **out-of-domain** | A GeoSphere 400 carrying "outside of dataset bounds" — a coverage signal that triggers the Open-Meteo fallback |
| **rate limit** | A GeoSphere 429 — retried once when the wait is 5 s or less, else returned as a notice; unlike out-of-domain it triggers no source fallback |
| **apparent temperature** | "Feels like" temperature derived from temperature, humidity, and wind (Australian BoM formula), not fetched |
| **condition** | A Home Assistant-style condition string such as `partlycloudy` — the shared output vocabulary |
| **accumulation delta** | The hour-over-hour difference of AROME's cumulative `rr_acc`/`snow_acc`, used to split rain from snow |
| **day divider** | The `%a %Y-%m-%d` header the hourly renderer inserts whenever the local calendar date changes |

## Dependencies
- The GeoSphere Austria Dataset API and Open-Meteo are the only external data sources
- Daily forecasts depend solely on Open-Meteo

## Design Decisions
These span more than one concept; per-concept decisions live in the sub-file that owns the concept.

- **Tool names mirror the OpenWeatherMap server** so existing agent routing prompts transfer unchanged.
- **Dynamic coverage detection**: attempt GeoSphere, fall back on out-of-domain — no bounding box to
  maintain as the grids evolve.
- **One condition vocabulary across two very different sources**, so the caller cannot tell which path
  served a response except by reading the source line.
- **Markdown output in metric units**, compact and friendly to assistants that read results aloud.
- **Degrade rather than fail**: coverage gaps, rate limits, and secondary-dataset failures all have a
  defined partial-success path.

## Known Risks
- GeoSphere resource IDs are versioned — a catalog rotation breaks the primary path until the IDs in
  `const.py` are bumped.
- No GeoSphere forecast reaches beyond ~60 h, so daily and long-range must use Open-Meteo.
- The Open-Meteo forecast endpoint has no convective inhibition, so the storm outlook degrades to CAPE-only
  gating outside GeoSphere coverage.
- Shared GeoSphere rate limits (5 req/s, 240 req/h) with no server-side quota tracking.
- `condition.py` duplicates Home Assistant condition strings to stay import-free, so they could drift.

Per-concept risks live in the sub-file that owns the concept.

## Extension Guidelines
- New domain feature: add a tool in `server.py`, orchestration in `weather.py`, and a renderer in
  `format.py`, then document it in [OUTPUT-CONTRACT.md](OUTPUT-CONTRACT.md).
- New concept: create a sub-file, then add it to the concept catalogue above and to [README.md](README.md).
- Keep this file an index — put the detail in the sub-file that owns the concept, and add new terms to the
  glossary above.
