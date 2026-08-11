# Air Quality

## Purpose
Documents the air-quality feature: the pollutants both sources publish, the European AQI and its band
scale, and how the two very different source shapes are reconciled into one output.

## Responsibilities
- Specifying the four pollutants and the daily AQI that `get_air_quality` reports
- Documenting the EEA band scale and the numeric-to-band mapping
- Explaining the per-source differences and how normalization hides them
- Recording the degradation rule between the pollutant and AQI datasets

## Non-Responsibilities
- Dataset IDs and coverage rules — see
  [DATA-SOURCES-AND-COVERAGE.md](DATA-SOURCES-AND-COVERAGE.md)
- The rendered line format — see [OUTPUT-CONTRACT.md](OUTPUT-CONTRACT.md)

## Overview

`get_air_quality` reports two things: the pollutant concentrations happening now, and the European Air
Quality Index for today, tomorrow and in two days. Both are **model forecasts, not station measurements** —
they will track a nearby monitoring station without matching it.

### Pollutants

Four surface concentrations in µg/m³, the intersection of what both sources publish:

| Key | Label | GeoSphere parameter | Open-Meteo variable |
|-----|-------|---------------------|---------------------|
| `nitrogen_dioxide` | NO₂ | `no2surf` | `nitrogen_dioxide` |
| `ozone` | O₃ | `o3surf` | `ozone` |
| `pm10` | PM10 | `pm10surf` | `pm10` |
| `pm2_5` | PM2.5 | `pm25surf` | `pm2_5` |

That table is literally `AIR_QUALITY_POLLUTANTS` in `const.py`, and the uniform key is deliberately also
the Open-Meteo variable name, so one tuple derives `CHEM_POLLUTANTS`, `CHEM_PARAMETERS`,
`OPENMETEO_AIR_QUALITY_VARIABLES`, and the renderer's labels. Nothing here is hand-synced.

Neither path reports CO, SO₂, NH₃, pollen, or UV index, though Open-Meteo has some of them — the tool
reports the set both sources can serve, so its output does not change shape with the caller's coordinate.

The reported value is the forecast hour **nearest to now**, on both paths. An exact tie between two hours
resolves to the earlier one.

### The European Air Quality Index

The EEA scale is six bands, and the rendered output always leads with the band index so both sources read
alike:

| Band | Label | Numeric range |
|------|-------|---------------|
| 1 | good | 0 to <20 |
| 2 | fair | 20 to <40 |
| 3 | moderate | 40 to <60 |
| 4 | poor | 60 to <80 |
| 5 | very poor | 80 to <100 |
| 6 | extremely poor | 100 and above |

Each band is **half-open**: the bound belongs to the band above it, so an index of exactly 20 is `fair`,
not `good`. `AQI_NUMERIC_BAND_BOUNDS` holds the upper bounds and `aqi_band` compares with `<`.

The two sources publish **different halves of this table**, which is the only real complexity in the
feature:

- **GeoSphere** publishes the band index (1-6) directly, as a daily value. No numeric index exists, so none
  is rendered.
- **Open-Meteo** publishes the underlying numeric index (0-100+) hourly, and no daily value at all. The
  numeric value is banded with the thresholds above (`aqi_band` in `const.py`) and rendered as
  `3 (moderate, index 44)`, so the band leads and the numeric detail follows.

**Per-day values differ in how they are obtained.** GeoSphere's daily stamps are 00:00 UTC and are matched
to today / tomorrow / in 2 days by **local calendar day** in `Europe/Vienna` — a 00:00 UTC stamp is 01:00
or 02:00 local, so a naive UTC-date match would be off by a day for part of the year. Open-Meteo has no
daily index, so each day's figure is the **maximum of that local day's hourly values**. A daily maximum is
the honest summary for an index whose whole purpose is flagging the worst air of the day.

### Degradation

The GeoSphere path fetches two datasets concurrently and applies the same primary/secondary rule as the
C-LAEF ensemble: a `chem` failure propagates (the tool has nothing to say without concentrations), while an
AQI failure logs a warning and keeps the concentrations. The `sources` list in the response reflects what
actually contributed, and the rendered source line shows it.

An out-of-domain error on `chem` — and only on `chem` — triggers the Open-Meteo fallback, which is a single
request.

## Dependencies
- GeoSphere `chem-v2-1h-3km` (pollutants, ~73 h hourly) and `chem_aqi-v1-1d-3km` (daily band)
- Open-Meteo `air-quality-api.open-meteo.com/v1/air-quality` (CAMS), 3 forecast days
- `air_quality.py` holds the GeoSphere merge; `format.py` holds both normalizers and the renderer

## Design Decisions
- **A separate tool, not a section of `get_current_weather`**: air quality costs two extra requests that
  most current-weather calls do not want.
- **Band-first rendering**: the band is the part a human or model can act on, and it is the only figure both
  sources can produce. The numeric index is detail, shown where it exists.
- **Daily maximum for the Open-Meteo per-day figure**: an average would hide the afternoon ozone peak that
  the index exists to flag.
- **The four-pollutant intersection**: a stable output shape matters more than reporting everything each
  source happens to have.

## Known Risks
- WRF-Chem and CAMS are different models: the two paths will not agree numerically at a point near the
  coverage boundary. Each response states its source.
- The GeoSphere band and the Open-Meteo numeric index are computed by different methods, so a band may
  differ by one step between the paths for genuinely similar air.
- Air quality adds two requests per call against the shared GeoSphere budget (5 req/s, 240 req/h).

## Extension Guidelines
- New pollutant: add one row to `AIR_QUALITY_POLLUTANTS` in `const.py` — every request parameter list and
  the renderer's labels derive from it. A pollutant only one source has needs a decision about the output
  shape first.
- Never inline a band threshold: they live in `AQI_NUMERIC_BAND_BOUNDS` and `AQI_BAND_LABELS`.
