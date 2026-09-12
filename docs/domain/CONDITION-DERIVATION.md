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
| Thunder on *observed* rain >= 4.0 mm/h | CAPE >= 1000 J/kg alone (CIN veto overridden) | `THUNDER_CAPE_JKG`, `POURING_MM_PER_H` |
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

**One place can override the cap**: the precipitating branch of `derive_current_condition`, and only when
the observed rate reaches `POURING_MM_PER_H`. Its precipitation evidence is *observed* (INCA and the
nowcast are anchored to measurements) while CAPE and CIN are AROME's forecast for the hour. Inhibition
answers "can convection get started?", which a downpour has already settled, so a modelled lid must not
veto a storm that is visibly happening — that would render a thunderstorm in progress as plain `rainy`.

The intensity qualifier is what keeps the override narrow. `is_precipitating` is true of drizzle, so
overriding on *any* observed rain would promote high CAPE under a strong lid with light stratiform rain —
a real frontal pattern, not a storm — to `lightning-rainy`, contradicting the hourly path's `rainy` for the
same hour. Below that rate, and everywhere the verdict is forecast-driven end to end (including this
function's non-precipitating branch), the full gate applies. The cost is a genuine storm raining more
weakly than 4 mm/h under a modelled lid, which still reads as `rainy`; the alternative was a second rate
constant with nothing to validate it against.

A **missing** `cin` counts as uncapped, which keeps the pre-gate behaviour intact for any hour AROME
leaves blank. `is_thunder` takes the AROME sign convention (negative J/kg) and does not verify it — a
future source publishing inhibition as a positive magnitude must be negated before calling.
`CAP_CIN_JKG = 50.0` is a standard boundary for weak inhibition; its discrimination against real capped
situations is unconfirmed against observations.

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

Day versus night (`sunny` vs `clear-night`) is resolved with `astral` in both derivation functions.

### Current-Conditions Merge Chain
Each field is filled from a per-field fallback chain (ported from `ha-geosphere-next`):

| Field | Chain |
|-------|-------|
| Temperature, humidity, wind speed, wind bearing | INCA -> nowcast -> AROME |
| Dew point | INCA -> nowcast |
| Gust | nowcast -> AROME |
| Pressure (`P0`, Pa converted to hPa), global radiation | INCA only |
| Cloud cover, CAPE, CIN | AROME |
| 1-hour precipitation | INCA `RR` only -- absent when INCA is |
| Precipitation rate (feeds the condition) | matched nowcast `rr` bucket x `NOWCAST_BUCKETS_PER_HOUR`, else INCA `RR` if its stamp is in the past and no older than `INCA_RR_MAX_AGE_SECONDS`; once `pt` says it is precipitating, the peak across the last `RATE_LOOKBACK` (30 min) of buckets |
| Precipitation flag (`is_precipitating`) | `condition.is_precipitating` on the nowcast `pt` code (255 means none) and the nowcast rate -- never INCA `RR`. Tri-state: absent when neither spoke |
| Observation time (`observed_at`) | INCA `T2M` analysis -> the matched nowcast bucket's stamp -> the AROME row's stamp (clamped to `now`) |

**Why the hourly total has no nowcast fallback.** Summing the last four 15-min `rr` buckets was tried and
removed. The nowcast endpoint serves a single model run, clamped to that run's own t0 and published
~25-35 min after the analysis it is stamped for, so there were never four buckets to sum: measured
2026-09-11 against the live API, an unbounded request returns exactly one bucket at or before `now` and an
anchored one reaches only the serving run's start. Every such sum was a 15-45 minute total labelled as a
full hour, under-reporting by up to 4x on exactly the degraded path it existed for. Reconstructing a true
hour would need the t0 bucket of four consecutive runs (`forecast_offset=0..3`) — four extra requests per
call against a shared rate limit. Reporting nothing is the honest answer, and it is why the request now
carries a `start` anchored to the 15-min grid (`NOWCAST_LOOKBACK`): without one the series holds a single
bucket, which silently reduced the `RATE_LOOKBACK` peak to the matched bucket it exists to widen.

**Why `is_precipitating` is tri-state.** "Is it precipitating right now" is an instantaneous question, and
only the 15-min nowcast observes it. INCA's hourly `RR` is an accumulation over the hour it is stamped
for, so substituting it reports rain that has already stopped — 2.4 mm falling in the hour to 15:00 still
reads "wet" at 16:50. With no nowcast at all (a point inside the AROME domain but outside the Austrian
grid, or a transient fetch failure) nothing observed precipitation, and a confident "dry" would be
invented, so the flag is absent rather than false — consistent with `precipitation_1h_mm` in the same
case. The condition derivation *does* still fall back to `RR`, because it has to name something, but only
while that value is younger than `INCA_RR_MAX_AGE_SECONDS` (2 h) **and not future-dated** — a bare
age comparison is also satisfied by a negative age, which would let an hour that has not happened yet
read as the freshest reading there is. The bound catches a slice that has
stopped updating, not ordinary lag: INCA routinely trails ~90 min, and a tighter bound would flap the
condition between `rainy` and cloud-derived once per publish cycle through steady rain.

`observed_at` reports the stamp of whichever source supplied the **temperature** — the field the reading
is judged by — at every rung, so no other field's freshness can vouch for it. The INCA analysis behind the
*precipitation* is deliberately not a rung: an analysis with no `RR` would claim a fresher time than the
temperature deserves, and one with `RR` but no `T2M` would date a current nowcast temperature to an
hour-old slice. Both directions are wrong.

Where the nowcast supplies the temperature, the matched 15-min bucket's own stamp is reported rather than
`now` — no source ever states `now`, and this timestamp exists to show the gap. With neither, values come
from the AROME row for the hour in progress, stamped at the top of that hour and up to an hour old — the
staleness this timestamp exists to expose, so the row's own stamp is reported, clamped to `now` because an
observation time can never be in the future. INCA publishes ~30 min after the hour it analyses and serves
the previous slice until the next appears, so `observed_at` can trail real time by ~90 min.

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
"the last forecast period", so a percentile stamped `T` covers the period *ending* at `T`.
`_pop_by_timestamp` therefore keys the dict by the **preceding stamp**, putting each probability on the
forecast row that reports the matching amount. Reading them at their own stamp pairs every row's amount
with the *previous* hour's probability.

The period start is read from the series rather than by subtracting a fixed step. Ensembles commonly
coarsen along their horizon, and if C-LAEF ever does, a hardcoded 1 h step would miss every AROME row past
the break and blank the probability across the whole forecast — silently, with nothing logged. The
predecessor is correct at any cadence; index 0 (the run start, whose "last period" precedes the run) has
no row to land on.

Lookup is then a plain exact-match on the shifted key — there is no nearest-neighbour fallback, so a
timestamp mismatch silently yields no probability. An ensemble fetch failure omits the probability
entirely and the forecast still renders.

## Dependencies
- `condition.py` depends only on `astral` and `const.py`
- `weather.py` owns the merge chain and POP mapping and calls `condition.py`
- Every threshold is a named constant in `const.py`

## Design Decisions
- **Physically derived conditions**: the condition comes from physical parameters rather than GeoSphere's
  proprietary `sy` symbol code, whose table is undocumented and could change under us without notice.
- **Two derivation functions instead of one**: the current path has nowcast humidity and a usable
  temperature signal, so it can afford a fog heuristic and a temperature-based snow rule; the hourly path
  has accumulation deltas instead and must not guess at fog.
- **Duplicated Home Assistant condition literals**: keeps `condition.py` import-free and portable.
- **Stepped POP over interpolation**: three ensemble percentiles cannot support a continuous curve
  honestly, so the mapping stays coarse and explicit.
- **The nowcast bucket is matched nearest-in-either-direction**, so current values can come from up to
  7.5 minutes ahead. `nearest_index` minimises the absolute distance to `now`, which means that from
  `HH:MM+7:30` the *next* 15-min bucket is the closest one and wins. Every nowcast-sourced current field
  rides that single index — `t2m`, `rh2m`, `td`, `dd`, `ff`, `fx`, `pt` and `rr` — so `is_precipitating`
  can read `true` at 15:40 from rain the nowcast places at 15:45, and the same is true of the
  temperature and the gust beside it. Only the reported *time* is clamped: `observed_at` takes
  `min(stamp, now)` because an observation time in the future is simply wrong, while a *value* from the
  nearest bucket is not.

  Raised in review (2026-09-11) against `is_precipitating`, whose contract says "falling right now", and
  **deliberately declined**. The nowcast is a short-range forecast at every bucket, including the one
  behind `now`; there is no bucket that observes the present. Snapping backwards would therefore not buy
  an observation, it would substitute a staler estimate for a nearer one, and at the 7.5-minute boundary
  the next bucket genuinely is the better answer. The residual error sits inside the ordinary
  uncertainty of the source. Recorded here because it is a decision rather than an oversight — the
  `nearest_index` docstring states the behaviour and the clamping obligation, but not the verdict.

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
- New condition string: add it to the vocabulary list above and to the `CONDITION_*` block in `const.py`.
- Keep `condition.py` free of MCP and Home Assistant imports.
