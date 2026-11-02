# Condition Derivation

## Purpose
Documents the shared condition vocabulary, how a condition is taken from GeoSphere's weather symbol (`sy`)
for each forecast hour and how observations override it for the current condition, the current-conditions
merge chain, and the precipitation-chance estimate.

## Responsibilities
- Defining the condition vocabulary and the `sy` code table it is mapped from
- Specifying `derive_condition_from_symbol` and `derive_current_condition`
- Documenting the per-field merge chain for current conditions
- Documenting the precipitation-chance (POP) estimate

## Non-Responsibilities
- Which dataset supplies which parameter, and how each is stamped (see
  [DATA-SOURCES-AND-COVERAGE.md](DATA-SOURCES-AND-COVERAGE.md))
- The thunderstorm outlook (see [STORM-OUTLOOK.md](STORM-OUTLOOK.md))
- How derived values are rendered, or whether they are rendered at all (see
  [OUTPUT-CONTRACT.md](OUTPUT-CONTRACT.md))
- Module boundaries of `condition.py` and `weather.py` (see
  [../tech/ARCHITECTURE.md](../tech/ARCHITECTURE.md))

## Overview

### Condition Vocabulary
One shared vocabulary across all tools — the Home Assistant condition set, as far as GeoSphere's symbol
reaches it: `sunny`, `clear-night`, `partlycloudy`, `cloudy`, `fog`, `rainy`, `pouring`, `snowy`,
`snowy-rainy`, `lightning-rainy`. The strings are declared once as `CONDITION_*` in `const.py` and
`condition.py` maps onto them, so the package stays free of Home Assistant imports. HA's `windy`,
`windy-variant`, `lightning` (dry thunder), `hail` and `exceptional` have no `sy` code and never occur.

### The `sy` Code Table
GeoSphere published the weather-symbol table, in German, in
[dataset-api-docs#30](https://github.com/Geosphere-Austria/dataset-api-docs/issues/30#issuecomment-2042539848).
It was confirmed there for `nwp-v1`; that `nwp-v2` follows it is measured, not published (see
[Known Risks](#known-risks)). The code groups live in `const.py` (`SY_CLEAR` … `SY_THUNDER`):

| `sy` | GeoSphere | Condition |
|---|---|---|
| 1, 2 | Wolkenlos, Heiter | `sunny`, or `clear-night` at night |
| 3 | Wolkig | `partlycloudy` |
| 4, 5 | Stark bewölkt, Bedeckt | `cloudy` |
| 6, 7 | Bodennebel, Hochnebel | `fog` |
| 8, 9 | Leichter / Mäßiger Regen | `rainy` |
| 10 | Starker Regen | `pouring` |
| 11–13 | Schneeregen | `snowy-rainy` |
| 14–16 | Leichter / Mäßiger / Starker Schneefall | `snowy` |
| 17, 18 | Regenschauer | `rainy` |
| 19 | Starker Regenschauer | `pouring` |
| 20–22 | Schneeregenschauer | `snowy-rainy` |
| 23–25 | Schneeschauer, Starker Schneeschauer | `snowy` |
| 26–28 | Gewitter, Starkes Gewitter | `lightning-rainy` |
| 29–32 | (Starkes) Gewitter mit Schneeregen / Schneefall | `lightning-rainy` |

A missing code, or one outside the table, maps to `None` (unknown) — never a guess — and logs one warning
per code per process (`weather._warn_unknown_sy_code`). The table has no night variants, so `is_night`
(`astral` solar elevation below 0) decides between `sunny` and `clear-night`: for a forecast row at the
middle of its hour (`FORECAST_ROW_MIDPOINT`), for the current condition at `now`. Every thunderstorm code,
the sleet and snow ones included, maps to `lightning-rainy`: there is no thundersnow condition, and the
thunderstorm is the hazard people act on. `is_thunder_symbol` is the shared 26–32 test, also used by the
current condition and the storm outlook.

### `derive_condition_from_symbol` (per forecast hour)
Inputs: the hour's `sy` code and `night`; returns the table's condition. A forecast row reads `sy` at the
**next** stamp, like `tp`: its precipitation classes follow `tp` at their own stamp, so only there does the
row's condition agree with the row's own precipitation amount. The cost is the cloud classes, which bin
`tcc` at that later stamp — the sky at the *end* of the row's hour, while the row's cloud cover is `tcc`
at its start. See [DATA-SOURCES-AND-COVERAGE.md](DATA-SOURCES-AND-COVERAGE.md#the-forecast-datasets) for
the stamping rule.

### `derive_current_condition` (now)
The symbol comes from a run that is routinely several hours old, so observations decide whether and what
is falling now. Keyword-only inputs: the nowcast precipitation-type code and rate, temperature, the `sy`
code of the forecast hour in progress, and `night`. `is_precipitating` reads the code and rate as observed
wet, observed dry, or nothing observed (`None`):

1. **Observed wet** (`pt` ≠ 255, or rate ≥ `PRECIP_MIN_MM` 0.1 mm/h). The `pt` code decides the kind;
   temperature (`SNOW_MAX_T2M_C` = 1.0 °C) splits rain from snow only when the code names no kind —
   absent, 255 beside an observed rate, or a code outside GeoSphere's table. In order: `snowy` if
   T ≤ 1 °C and the kind is pt 5 or none; else `lightning-rainy` when the hour's symbol is 26–32; else
   `snowy` (pt 5); else `snowy-rainy` (pt 7 rain and snow, or pt 3 freezing rain, which has no condition
   of its own); else `pouring` (≥ `POURING_MM_PER_H` 5 mm/h, where GeoSphere's heavy-rain codes 10/19
   start, so now and the forecast flip at the same intensity) or `rainy`. The symbol's own kind (rain,
   sleet, snow) is never used here.
2. **Observed dry**, which only the nowcast can say (`pt` 255, or a dry rate). The symbol's mapping
   applies, except that a precipitation or thunder code (8–32) reads `cloudy` — the model expects
   precipitation that is not falling, under what is then an overcast sky. Fog comes only from codes 6–7.
3. **Nothing observed** (outside the nowcast grid, or the nowcast down with no current wet INCA `RR`):
   the symbol is the only evidence and its mapping applies unchanged, rain and thunder included. A dry
   INCA `RR` never counts as "observed dry": it analyses an hour that ended up to ~90 min ago, so it
   cannot rule out rain that has started since.

**Snow outranks a thunder symbol when cold.** At or below 1 °C observed snow (pt 5, or no kind) reads
`snowy` even under a thunderstorm symbol: `lightning-rainy` depicts rain, thundersnow is rare and still
snow, and the run behind the symbol may have expected a warmer airmass than the one now snowing. Above
1 °C, or with no temperature from any source, the thunder symbol wins over every kind, since a graupel or
rain-and-snow shower can still be a storm. Cold pt 7 and pt 3 lose to thunder too: they are not snow.

Thunder is the one thing observations here cannot see, so it comes from the symbol, and only while
precipitation is observed. CAPE plays no part in any condition.

### Current-Conditions Merge Chain
Each field is filled from a per-field fallback chain:

| Field | Chain |
|-------|-------|
| Temperature, humidity, wind speed, wind bearing | INCA -> nowcast -> AROME |
| Gust | nowcast -> AROME |
| Pressure (`P0`, Pa converted to hPa) | INCA only |
| Cloud cover, weather symbol | the AROME row for the hour in progress (`weather._arome_current`, the same assembled row the hourly forecast emits — so the symbol is read from the next stamp, like the gust) |
| 1-hour precipitation | INCA `RR` only -- absent when INCA is |
| Precipitation rate (feeds the condition) | matched nowcast `rr` bucket x `NOWCAST_BUCKETS_PER_HOUR` whenever the nowcast reports `pt` or `rr` — a `pt` of 255 is an observed "dry" that INCA never overrules; otherwise INCA `RR`, but only when it is wet (>= 0.1 mm) and no older than `INCA_RR_MAX_AGE_SECONDS`; otherwise `None` (nothing observed). Once `pt` says it is precipitating, the peak across the last `RATE_LOOKBACK` (30 min) of buckets |
| Precipitation kind (`precipitation_kind`) | `condition.derive_precipitation_type` on the nowcast `pt` code and the condition's own rate (above). Absent for a code outside GeoSphere's table (logged once per code per process) and for 255 beside a rate that says it is precipitating, so "none" never sits beside a rainy condition |
| Observation time (`observed_at`) | INCA `T2M` analysis -> the matched nowcast bucket's stamp -> the AROME row's stamp (clamped to `now`) |

**Why the hourly total has no nowcast fallback.** The nowcast endpoint serves a single model run, clamped
to that run's own t0 and published ~25-35 min after the analysis it is stamped for, so the buckets on hand
never cover a full hour: a sum would be a 15-45 minute total labelled as an hour. Reconstructing a true
hour would need the t0 bucket of four consecutive runs (`forecast_offset=0..3`) — four extra requests per
call against a shared rate limit. Reporting nothing is the honest answer. The nowcast request still
carries a `start` anchored to the 15-min grid (`NOWCAST_LOOKBACK`): without one the series holds a single
bucket at or before `now`, and the `RATE_LOOKBACK` peak needs more than the matched bucket.

**Why INCA `RR` stands in only when wet and current.** Only the 15-min nowcast observes precipitation
*now*. INCA's hourly `RR` is an accumulation over the hour it is stamped for, so it can report rain that has
already stopped — 2.4 mm falling in the hour to 15:00 is still on the books at 16:50. When the nowcast
reports nothing, the condition still falls back to a **wet** `RR` younger than `INCA_RR_MAX_AGE_SECONDS`
(2 h). The bound catches a slice that has stopped updating, not ordinary lag: INCA routinely trails
~90 min, and a tighter bound would flap the condition between `rainy` and the symbol's condition once per
publish cycle through steady rain. A dry `RR` is never passed on as an observation: it would demote a rain
or thunder symbol to `cloudy` on the strength of an hour that is already over.

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

Apparent temperature ("feels like") is **derived rather than fetched**, with the Australian Bureau of
Meteorology formula from temperature, humidity, and wind.

The weather symbol and the raw precipitation-type code are merged but not rendered: they feed the
condition, the `Falling now` line and the once-per-code warnings. See
[OUTPUT-CONTRACT.md](OUTPUT-CONTRACT.md) for what each tool actually emits.

### Precipitation Chance (POP)
GeoSphere publishes no precipitation probability. The chance on each hourly row is an **estimate this
server derives** from the C-LAEF ensemble, which publishes only `tp_p10` / `tp_p50` / `tp_p90` per hour —
no member counts, so no true wet fraction. A percentile at or above `PRECIP_MIN_MM` (0.1 mm) bounds the
share of wet members, and the midpoint of that range is reported, matched to forecast rows by **exact
timestamp**. The hourly output says so in a trailing note whenever it shows a chance:

| Wettest percentile that is wet | Probability | Constant |
|--------------------------------|-------------|----------|
| p10 | 95 % | `POP_P10_WET_PCT` |
| p50 | 70 % | `POP_P50_WET_PCT` |
| p90 | 30 % | `POP_P90_WET_PCT` |
| none | 0 % | `POP_DRY_PCT` |

The percentiles are **interval** values, like `tp` and the gust: GeoSphere documents them as "in the last
forecast interval", so a percentile stamped `T` covers the period *ending* at `T`.
`_ensemble_by_timestamp` therefore keys them by the **preceding stamp**, putting each chance on the
forecast row that reports the matching amount. Reading them at their own stamp pairs every row's amount
with the *previous* hour's chance. The same mapping carries `tp_p90` (keyed the same way) and the
instantaneous `cape_p90` (kept on its own stamp) onto the rows for the storm outlook.

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
- Every threshold and the `sy` code groups are named constants in `const.py`

## Design Decisions
- **GeoSphere's own classification over local thresholds**: the condition matches what GeoSphere's products
  show, and no cloud, gust, fog or CAPE threshold needs tuning here. The raw values (cloud cover,
  precipitation, gusts, CAPE) are unchanged on the rows.
- **Observations outrank the symbol for what is falling now**, because the symbol's run is hours old.
  Thunder is the one thing observations cannot see, so it comes from the symbol, and only while
  precipitation is observed.
- **Only the nowcast can say "dry"**: a dry INCA `RR` describes an hour already over, so treating it as an
  observation would flip a rain symbol to `cloudy` and back as each analysis lands.
- **Stepped POP over interpolation**: three ensemble percentiles cannot support a continuous curve
  honestly, so the mapping stays coarse and explicit.
- **The nowcast bucket is matched nearest-in-either-direction**, so current values can come from up to
  7.5 minutes ahead. `nearest_index` minimises the absolute distance to `now`, so once `now` is more than
  7.5 minutes past a bucket the *next* one is closer and wins. At exactly 7.5 minutes the two tie, and
  `min` keeps the first of equal keys — the earlier bucket — so the switch happens strictly *after* the
  boundary, not at it. Every nowcast-sourced current field
  rides that single index — `t2m`, `rh2m`, `dd`, `ff`, `fx`, `pt` and `rr` — so the condition
  can read rain at 15:40 that the nowcast places at 15:45, and the same is true of the
  temperature and the gust beside it. Only the reported *time* is clamped: `observed_at` takes
  `min(stamp, now)` because an observation time in the future is simply wrong, while a *value* from the
  nearest bucket is not.

  Raised in review against the "falling right now" reading, and
  **deliberately declined**. The nowcast is a short-range forecast at every bucket, including the one
  behind `now`; there is no bucket that observes the present. Snapping backwards would therefore not buy
  an observation, it would substitute a staler estimate for a nearer one, and at the 7.5-minute boundary
  the next bucket genuinely is the better answer. The residual error sits inside the ordinary
  uncertainty of the source. Recorded here because it is a decision rather than an oversight — the
  `nearest_index` docstring states the behaviour and the clamping obligation, but not the verdict.

## Known Risks
- The `nwp-v2` `sy` table is assumed equal to the `nwp-v1` one GeoSphere confirmed. `ha-geosphere-next`
  measured it across the whole grid (cloud classes bin `tcc` exactly; rain intensities bin `tp`; rain,
  sleet and snow classes track temperature and snowfall; every thunder point is wet), but codes 6–7
  (fog), 25, 30 and 32 never occurred. If `nwp-v2` does not emit fog codes, **no hour and no current
  condition will ever read `fog`** — accepted, since a local fog heuristic is what the symbol replaced.
- GeoSphere's thunder codes fire at modest CAPE (median ~160 J/kg in that sample), so `lightning-rainy` is
  GeoSphere's own diagnosis, not a CAPE threshold.
- A row's condition (from `sy` at the end of its hour) and its cloud cover (from `tcc` at the start) can
  disagree by a cloud class.
- `condition.py` relies on Home Assistant's condition strings, declared in `const.py` — these could drift if
  a condition is renamed upstream.
- The nowcast `pt` and the `sy` code tables are hard-coded from GeoSphere's issue replies; `/metadata`
  carries neither. A new code shows up only as a one-time warning in the log, and reads unknown.
- Exact-timestamp POP matching yields no chance on any clock skew between the AROME and C-LAEF series.

## Extension Guidelines
- To change a mapping, move the code between the `SY_*` groups in `const.py`; `tests/test_condition.py`
  pins the table code by code.
- New threshold: add a named constant to `const.py`; never inline a literal in `condition.py`.
- New condition string: add it to the vocabulary list above and to the `CONDITION_*` block in `const.py`.
- Keep `condition.py` free of MCP and Home Assistant imports.
