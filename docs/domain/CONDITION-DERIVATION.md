# Condition Derivation

## Purpose
Documents the shared condition vocabulary, the two physical derivation functions and their thresholds,
the current-conditions merge chain, and the precipitation-probability mapping.

## Responsibilities
- Defining the condition vocabulary shared by both data paths
- Specifying `derive_condition` and `derive_current_condition` and every threshold they use
- Documenting the per-field merge chain for current conditions
- Documenting the precipitation-probability (POP) mapping

## Non-Responsibilities
- Which dataset supplies which parameter (see [DATA-SOURCES-AND-COVERAGE.md](DATA-SOURCES-AND-COVERAGE.md))
- How derived values are rendered, or whether they are rendered at all (see
  [OUTPUT-CONTRACT.md](OUTPUT-CONTRACT.md))
- Module boundaries of `condition.py` and `weather.py` (see
  [../tech/ARCHITECTURE.md](../tech/ARCHITECTURE.md))

## Overview

### Condition Vocabulary
One shared vocabulary across all tools and both data paths — the Home Assistant condition set: `sunny`,
`clear-night`, `partlycloudy`, `cloudy`, `rainy`, `pouring`, `lightning`, `lightning-rainy`, `snowy`,
`snowy-rainy`, `windy`, `windy-variant`, `fog`.

`condition.py` duplicates these string literals rather than importing them, so the derivation logic stays
free of Home Assistant imports and portable to and from `ha-geosphere-next`.

### GeoSphere Paths: Two Derivation Functions
Conditions are derived physically from parameters, not read from a proprietary symbol code. All thresholds
live in `const.py`. The two functions deliberately apply different rules.

**`derive_condition`** — used per forecast hour by the hourly tool.

| Rule | Threshold | Constant |
|------|-----------|----------|
| Precipitation counts as wet | >= 0.1 mm | `PRECIP_MIN_MM` |
| Wet becomes `pouring` | >= 4.0 mm/h | `POURING_MM_PER_H` |
| Thunder (with or without rain) | CAPE >= 1000 J/kg | `THUNDER_CAPE_JKG` |
| Dry `lightning` also needs cloud | >= 60 % | `WINDY_CLOUD_TCC_PCT` |
| Gust makes it windy | >= 15 m/s | `WINDY_GUST_MS` |
| `windy` vs `windy-variant` split at cloud | 60 % | `WINDY_CLOUD_TCC_PCT` |
| `sunny` / `clear-night` at or below cloud | 12.5 % | `CLEAR_TCC_PCT` |
| `partlycloudy` above 12.5 % and at or below | 62.5 % | `CLOUDY_TCC_PCT` |
| `cloudy` strictly above cloud | 62.5 % | `CLOUDY_TCC_PCT` |

The cloud comparisons are inclusive at the lower bound (`tcc <= CLEAR_TCC_PCT`, `tcc <= CLOUDY_TCC_PCT`),
so exactly 12.5 % renders `sunny`/`clear-night` and exactly 62.5 % renders `partlycloudy`.

Rain versus snow is split from AROME's **accumulated** `snow_acc` and `rr_acc` deltas (rain = precipitation
minus snowfall), not from temperature. There is **no fog branch** — the hourly tool never returns `fog`.

**`derive_current_condition`** — used for current weather. It adds a fog heuristic and changes the
rain/snow rule:

| Rule | Threshold | Constant |
|------|-----------|----------|
| Fog requires relative humidity | >= 98 % | `FOG_MIN_RH_PCT` |
| Fog requires wind below | 2.0 m/s | `FOG_MAX_WIND_MS` |
| Fog requires cloud | >= 87.5 % | `FOG_MIN_TCC_PCT` |
| Snow instead of rain at or below | 1.0 degC | `SNOW_MAX_T2M_C` |

Snow versus rain is decided by temperature here because the nowcast precipitation-type code table is
undocumented — it only signals *that* it is precipitating, not what kind. The fog heuristic can be
switched off wholesale via the `FOG_HEURISTIC_ENABLED` flag in `const.py`. When it is not precipitating,
the function falls back to `derive_condition` on cloud, CAPE, and gust alone.

Day versus night (`sunny` vs `clear-night`) is resolved with `astral` on both derivation paths.

### Open-Meteo Path
The WMO `weather_code` (0-99) maps to the same vocabulary through a static dict in `const.py`, combined
with the `is_day` flag to pick `sunny` or `clear-night`. No physical derivation happens on this path.

### Current-Conditions Merge Chain
On the GeoSphere path, each field is filled from a per-field fallback chain (ported from
`ha-geosphere-next`):

| Field | Chain |
|-------|-------|
| Temperature, humidity, wind speed, wind bearing | INCA -> nowcast -> AROME |
| Dew point | INCA -> nowcast |
| Gust | nowcast -> AROME |
| Pressure (`P0`, Pa converted to hPa), global radiation | INCA only |
| Cloud cover, CAPE | AROME |
| 1-hour precipitation | INCA `RR`, else the sum of the last four nowcast 15-min `rr` buckets |
| Precipitation flag | nowcast `pt` (255 means none) |

Two values are **derived rather than fetched**: apparent temperature ("feels like", Australian Bureau of
Meteorology formula from temperature, humidity, and wind) and — on the hourly path only — dew point
(Magnus formula from temperature and humidity, since AROME has no native dew-point parameter).

Not every merged field reaches the output. Dew point, global radiation, snow limit, CAPE, precipitation
type, and the precipitation flag are computed and then dropped during normalization; on the hourly path
wind bearing and dew point are likewise computed but never rendered. See
[OUTPUT-CONTRACT.md](OUTPUT-CONTRACT.md) for what each tool actually emits.

### Precipitation Probability (POP)
Derived from the C-LAEF ensemble as a stepped value matched by **exact timestamp**:

| Wettest percentile that is wet | Probability | Constant |
|--------------------------------|-------------|----------|
| p10 | 95 % | `POP_P10_WET_PCT` |
| p50 | 70 % | `POP_P50_WET_PCT` |
| p90 | 30 % | `POP_P90_WET_PCT` |
| none | 0 % | `POP_DRY_PCT` |

The ensemble series is keyed into a dict by timestamp and looked up with a plain exact-match lookup — there
is no nearest-neighbour fallback, so a timestamp mismatch silently yields no probability. On the
Open-Meteo path, `precipitation_probability` is used directly. An ensemble fetch failure omits the
probability entirely and the forecast still renders.

## Dependencies
- `condition.py` depends only on `astral` and `const.py`
- `weather.py` owns the merge chain and POP mapping and calls `condition.py`
- Every threshold is a named constant in `const.py`

## Design Decisions
- **Physically derived conditions**: the condition comes from physical parameters rather than GeoSphere's
  proprietary symbol code, and shares one vocabulary with the WMO-code mapping so both paths are
  indistinguishable to the caller.
- **Two derivation functions instead of one**: the current path has nowcast humidity and a usable
  temperature signal, so it can afford a fog heuristic and a temperature-based snow rule; the hourly path
  has accumulation deltas instead and must not guess at fog.
- **Duplicated Home Assistant condition literals**: keeps `condition.py` import-free and portable.
- **Stepped POP over interpolation**: three ensemble percentiles cannot support a continuous curve
  honestly, so the mapping stays coarse and explicit.

## Known Risks
- `condition.py` duplicates Home Assistant condition strings — these could drift if a condition is renamed
  upstream.
- The nowcast precipitation-type code table is undocumented, so the current path infers snow from
  temperature alone.
- Exact-timestamp POP matching yields no probability on any clock skew between the AROME and C-LAEF series.

## Extension Guidelines
- New threshold: add a named constant to `const.py`; never inline a literal in `condition.py`.
- New condition string: add it to the vocabulary list above and to the WMO map, so both paths stay aligned.
- Keep `condition.py` free of MCP and Home Assistant imports.
