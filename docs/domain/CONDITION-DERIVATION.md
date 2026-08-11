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
| Thunder (with or without rain) | CAPE >= 1000 J/kg **and** CIN > -50 J/kg | `THUNDER_CAPE_JKG`, `CAP_CIN_JKG` |
| Thunder on *observed* precipitation | CAPE >= 1000 J/kg alone (no CIN veto) | `THUNDER_CAPE_JKG` |
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

**The thunder gate (`is_thunder`).** CAPE measures how much energy convection *could* release; convective
inhibition (CIN) measures the lid holding it down. High CAPE under a strong lid produces no storm, so the
forecast paths call `is_thunder(cape, cin)` rather than comparing CAPE alone. AROME publishes `cin` as a
**negative** value in J/kg — `0.0` is uncapped and more negative is a stronger lid — so the gate reads
`cin > -CAP_CIN_JKG`.

**One place deliberately skips the cap**: the precipitating branch of `derive_current_condition`. Its
precipitation evidence is *observed* (INCA and the nowcast are anchored to measurements) while CAPE and
CIN are AROME's forecast for the hour. Inhibition answers "can convection get started?", which the
observation has already settled, so a modelled lid must not veto a storm that is visibly happening — that
would render a thunderstorm in progress as plain `rainy`. Everywhere the verdict is forecast-driven end to
end, including that same function's non-precipitating branch, the full gate applies.

A **missing** `cin` counts as uncapped, which keeps the pre-gate behaviour intact for any hour a source
leaves blank. Open-Meteo publishes inhibition too, but as a **positive magnitude**, so `format.py` negates
it into the AROME convention before it ever reaches this function — `is_thunder` only ever sees the
negative form. `CAP_CIN_JKG = 50.0` is a standard boundary for weak inhibition; its discrimination against
real capped situations is unconfirmed against observations.

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
the function falls back to `derive_condition` on cloud, CAPE/CIN, and gust alone — and there the CIN cap
does apply, since nothing observed contradicts it (see the thunder gate above).

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
| Cloud cover, CAPE, CIN | AROME |
| 1-hour precipitation | INCA `RR`, else the sum of the last four nowcast 15-min `rr` buckets |
| Precipitation flag | nowcast `pt` (255 means none) |
| Observation time (`observed_at`) | INCA `T2M` analysis -> INCA `RR` analysis -> `now` (nowcast) -> the AROME row's stamp |

`observed_at` follows whichever source won, so it stays honest at every rung. It prefers the analysis
behind the **temperature** because that is the field the reading is judged by; anchoring it to
precipitation alone lets an analysis with no `RR` claim a fresher time than the temperature deserves. The
15-min nowcast is current by construction, so `now` is right there. With neither, values come from the
AROME row for the hour in progress, stamped at the top of that hour and up to an hour old — the staleness
this timestamp exists to expose, so the row's own stamp is reported. INCA publishes ~30 min after the hour
it analyses and serves the previous slice until the next appears, so `observed_at` can trail real time by
~90 min.

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

The percentiles are **interval** values, like AROME's accumulations and gusts: GeoSphere documents them as
"the last forecast period", so a percentile stamped `T` covers `T-1h .. T`. `_pop_by_timestamp` therefore
keys the dict by `ts - ENSEMBLE_STEP`, putting each probability on the forecast row that reports the
matching amount. Reading them at their own stamp pairs every row's amount with the *previous* hour's
probability.

Lookup is then a plain exact-match on the shifted key — there is no nearest-neighbour fallback, so a
timestamp mismatch silently yields no probability. On the Open-Meteo path, `precipitation_probability` is
used directly. An ensemble fetch failure omits the probability entirely and the forecast still renders.

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
- The CIN threshold is a textbook boundary, not one validated against Austrian storm reports; too strict a
  value would suppress real storms and too loose a one would not filter anything.
- Exact-timestamp POP matching yields no probability on any clock skew between the AROME and C-LAEF series.

## Extension Guidelines
- New threshold: add a named constant to `const.py`; never inline a literal in `condition.py`.
- New condition string: add it to the vocabulary list above and to the WMO map, so both paths stay aligned.
- Keep `condition.py` free of MCP and Home Assistant imports.
