# CIN-gated thunder, storm outlook, and air quality

Date: 2026-08-10
Status: approved

## Problem

Three improvements landed in the sister project `ha-geosphere-next` (v0.7.0 and v0.9.0) that this
server does not have. `condition.py` here was ported from that repo and has since drifted:

1. **Thunder derivation ignores convective inhibition.** `derive_condition` fires `lightning` on
   CAPE alone, so a capped atmosphere — high CAPE under a strong lid, which produces no storm —
   reads as a thunderstorm.
2. **No storm outlook.** Answering "will it be gusty this evening" or "when is the next
   thunderstorm" requires pulling the full 60-hour table and reasoning over it.
3. **No air quality.** GeoSphere publishes a WRF-Chem pollutant forecast and a daily European AQI
   that this server never touches.

## Scope

In scope: porting 1 and 2 from `ha-geosphere-next`, and adding air quality as a new tool with a
worldwide fallback. Out of scope: the HA integration's entity/km-h presentation concerns, and any
change to the three existing tools' output.

## Design

### 1. CIN-gated thunder derivation

`const.py` gains `cin` in `AROME_PARAMETERS` and `CAP_CIN_JKG = 50.0`.

`condition.py` gains:

```python
def is_thunder(cape: float | None, cin: float | None) -> bool:
    if cape is None or cape < THUNDER_CAPE_JKG:
        return False
    return cin is None or cin > -CAP_CIN_JKG
```

`derive_condition` and `derive_current_condition` take a `cin` parameter and call it instead of
comparing CAPE directly. AROME publishes `cin` as negative J/kg (0.0 = uncapped); a missing value is
treated as uncapped, so any source without CIN keeps today's behaviour exactly.

This is a **behaviour change**: high CAPE under a strong cap no longer yields `lightning` /
`lightning-rainy`. It matches ha-geosphere-next v0.9.0.

`weather.py` threads `cin` from the AROME response into both derivation calls and exposes `cin_jkg`
on each hourly row and on the current-conditions dict.

### 2. Storm outlook

New module `outlook.py`, ported from `ha-geosphere-next/custom_components/geosphere_next/outlook.py`.
The HA original reads `HourlyForecast` dataclass attributes; this port reads the **row dicts** that
`weather.assemble_hourly_forecast` already produces, so one implementation serves both the GeoSphere
and Open-Meteo paths.

Row keys consumed: `time`, `condition`, `wind_gust_ms`, `cape_jkg`, `cin_jkg`, `precipitation_mm`.

Functions (all pure, all threshold-free — what counts as "too windy" is the caller's policy):

| Function | Returns |
|---|---|
| `max_gust(rows, hours, now)` | `(peak gust m/s, its hour)` within the horizon |
| `max_cape(rows, hours, now)` | peak CAPE J/kg within the horizon |
| `next_thunderstorm(rows, now)` | `(first storm hour, its CAPE)` over the whole series |
| `thunderstorm_outlook(rows, hours, now)` | tri-state `True` / `False` / `None` |

Two semantics carried over verbatim from the HA original, both deliberate:

- **The horizon rounds up to whole hourly steps.** The window starts at the top of the current hour
  and ends at `now + hours`, so a 1-hour window spans the in-progress hour plus the next one and can
  report an event up to ~2 h out.
- **`next_thunderstorm` may return a timestamp in the past**, by up to 59 minutes, when the storm
  hour is the one already under way. That reads as "storm in progress".

An hour counts as a thunderstorm hour when either the derived condition starts with `lightning`, or
the raw CAPE/CIN predicate holds *and* precipitation is forecast for that hour. The second branch
exists because `derive_condition` returns `snowy` before it looks at thunder (thundersnow) and
returns `None` when cloud cover is missing. It requires precipitation so that a dry high-CAPE summer
afternoon does not raise a storm signal.

`thunderstorm_outlook` returns `None` — not `False` — when the window holds no hour that can be
judged, so a data gap is distinguishable from "no storm".

Horizons are fixed at 1 h and 12 h (`OUTLOOK_SHORT_HORIZON_HOURS` / `OUTLOOK_LONG_HORIZON_HOURS`),
matching the HA entities. The tool takes no horizon argument.

**Tool:** `get_storm_outlook(latitude, longitude)`. On the GeoSphere path it fetches AROME only —
the C-LAEF ensemble contributes precipitation probability, which the outlook does not report, so the
extra request is skipped. Outside GeoSphere coverage it falls back to Open-Meteo hourly.

Open-Meteo's `/v1/forecast` endpoint offers both `cape` and `convective_inhibition`, added to
`OPENMETEO_HOURLY_VARIABLES` at no extra request. It reports inhibition as a **positive magnitude** where
AROME reports it negative, so normalization negates it before `is_thunder` ever sees it.

> **Corrected after review.** This section originally claimed the endpoint had no convective inhibition
> and designed a CAPE-only fallback around that. It was wrong — the variable exists and is now used, so
> the gate works identically on both paths and the "no inhibition" caveat that used to be printed in the
> output is gone.

The GeoSphere path additionally requests one hour of history (`HOURLY_LOOKBACK_HOURS`), anchored to the
top of the hour: the API trims the forecast to the current hour and `assemble_hourly_forecast` skips the
first step for lack of an accumulation predecessor, so without the lookback the in-progress hour — which
every window semantic above depends on — is dropped.

### 3. Air quality

New module `air_quality.py`, holding the GeoSphere fetch and merge:

- **Pollutants** (µg/m³) from `chem-v2-1h-3km`: `no2surf`, `o3surf`, `pm10surf`, `pm25surf`. The
  reported value is the forecast hour nearest to now; the full ~73 h series is kept for the optional
  hourly outlook.
- **Daily European AQI** (1–6, EEA bands) from `chem_aqi-v1-1d-3km`, matched to the local calendar
  day (stamps are 00:00 UTC) and reported as today / tomorrow / in 2 days.

The AQI fetch degrades independently: its failure logs a warning and keeps the pollutants, while a
`chem` failure propagates. Same primary/secondary rule the ensemble already follows.

`openmeteo_api.py` gains `async_get_air_quality` against `air-quality-api.open-meteo.com/v1/air-quality`
(`european_aqi`, `pm10`, `pm2_5`, `nitrogen_dioxide`, `ozone`), reusing the existing `_async_get`
plumbing with a different base URL.

**Two source mismatches, resolved in `format.py` so both paths read alike:**

- GeoSphere's `aqi` is the **1–6 EEA band index**; Open-Meteo's `european_aqi` is a **0–100+ numeric
  index**. Both render as the band *name* (good / fair / moderate / poor / very poor / extremely
  poor). The numeric index is shown alongside where it exists. Open-Meteo's numeric value is banded
  with the published EEA thresholds (≤20 / ≤40 / ≤60 / ≤80 / ≤100 / >100).
- Open-Meteo publishes no daily AQI, so today / tomorrow / in 2 days is the **maximum of the hourly
  series per local day**. GeoSphere serves those days natively.

**Tool:** `get_air_quality(latitude, longitude)` — current pollutant concentrations plus the
three-day AQI outlook. Two GeoSphere requests inside coverage, one Open-Meteo request outside.

### Rendering

`format.py` gains a normalize/render pair per tool, matching the existing shape: the normalizer folds
the two source paths into one uniform dict and the renderer never branches on source. GeoSphere times
render in `Europe/Vienna`; Open-Meteo times render in the timezone the API reports for the point.

Both tools go through `_guarded`, so they never raise — every failure resolves to the existing short
markdown error lines, including the rate-limit retry-once behaviour.

## Testing

- `is_thunder` — CAPE below threshold, CAPE above with weak/strong/missing CIN, and the boundary.
- Every `outlook.py` function — empty series, all-`None` fields, the round-up horizon boundary, the
  storm-in-progress past timestamp, thundersnow, missing cloud cover, dry high CAPE, and the
  tri-state `None`.
- The air-quality merge — nearest-hour selection, local-day AQI matching, AQI failure degrading to
  pollutants only, and `chem` failure propagating.
- The Open-Meteo air-quality client — request shape, error body, timeout typing.
- The formatters — both source paths, EEA banding at each threshold, missing fields omitted.
- Both tools in `test_server.py` — GeoSphere path, out-of-domain fallback, timeout, rate limit.

## Documentation

New `docs/domain/STORM-OUTLOOK.md` and `docs/domain/AIR-QUALITY.md`. Updates to
`docs/domain/OVERVIEW.md` (concept index), `OUTPUT-CONTRACT.md` (two new tool contracts),
`CONDITION-DERIVATION.md` (the CIN gate), `DATA-SOURCES-AND-COVERAGE.md` (chem datasets, the
Open-Meteo air-quality endpoint), `docs/tech/ARCHITECTURE.md` (two new modules), `AGENTS.md`
(five tools, not three), the README tool table, the `MCPServer` `instructions` string, and a
`CHANGELOG.md` Unreleased section.

## Risks

- The CIN gate is a behaviour change agents may notice as "fewer thunderstorm predictions". It is
  documented in the changelog as such.
- `CAP_CIN_JKG = 50.0` is the standard weak-inhibition boundary, but its discrimination against real
  capped situations is unconfirmed against observations — the same caveat the HA integration carries.
- WRF-Chem and CAMS (Open-Meteo) are different models; the two air-quality paths will not agree
  numerically at a border point. Both responses state their source.
- Air quality adds two GeoSphere requests per call against a shared 240 req/h budget.
