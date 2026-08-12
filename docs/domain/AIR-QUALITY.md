# Air Quality

## Purpose
Documents the air-quality feature: the pollutants WRF-Chem publishes, the European AQI and its band
scale, and how the hourly and daily datasets are merged into one output.

## Responsibilities
- Specifying the four pollutants and the daily AQI that `get_air_quality` reports
- Documenting the EEA band scale
- Explaining how the reading is dated, and why it is clamped to the present
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

Four surface concentrations in µg/m³:

| Key | Label | WRF-Chem parameter |
|-----|-------|--------------------|
| `nitrogen_dioxide` | NO₂ | `no2surf` |
| `ozone` | O₃ | `o3surf` |
| `pm10` | PM10 | `pm10surf` |
| `pm2_5` | PM2.5 | `pm25surf` |

That table is literally `AIR_QUALITY_POLLUTANTS` in `const.py`, and one tuple derives `CHEM_POLLUTANTS`,
`CHEM_PARAMETERS`, and the renderer's labels. Nothing here is hand-synced.

CO, SO₂, NH₃, pollen, and UV index are not reported — WRF-Chem does not publish them at this resolution.

The reported value is the forecast hour **nearest to now**. An exact tie between two hours resolves to the
earlier one.

Nearest means nearest in *either* direction, so from HH:31 onward the closest hour has not happened yet —
the concentrations are still read from it, being the closest the dataset has, but the reported observation
time is **clamped to the present**. That timestamp is what the caller is told the reading describes, and
an observation can never be in the future. Genuine staleness is left intact: only a stamp ahead of `now`
is pulled back to it.

### The European Air Quality Index

The EEA scale is six bands, and the rendered output leads with the band index:

| Band | Label |
|------|-------|
| 1 | good |
| 2 | fair |
| 3 | moderate |
| 4 | poor |
| 5 | very poor |
| 6 | extremely poor |

GeoSphere publishes this band index (1-6) directly, as a daily value, in the `chem_aqi-v1-1d-3km` dataset.
There is **no underlying numeric index** to render alongside it, so `2 (fair) today` is the whole figure —
`AQI_BAND_LABELS` in `const.py` supplies the label and nothing has to be banded.

The daily stamps are 00:00 UTC and are matched to today / tomorrow / in 2 days by **local calendar day** in
`Europe/Vienna` — a 00:00 UTC stamp is 01:00 or 02:00 local, so a naive UTC-date match would be off by a
day for part of the year.

### Degradation

The GeoSphere path fetches two datasets concurrently and applies the same primary/secondary rule as the
C-LAEF ensemble: a `chem` failure propagates (the tool has nothing to say without concentrations), while an
AQI failure logs a warning and keeps the concentrations. The `sources` list in the response reflects what
actually contributed, and the rendered source line shows it.

An out-of-domain error on `chem` — and only on `chem` — produces the out-of-coverage line, the same as
every other tool. **An empty in-domain response is a different answer**: the API answers HTTP 200 with an
empty series rather than an error when a WRF-Chem run is stale or incomplete, so "inside the grid" and
"has data" are separate questions. That case renders `No air-quality data available for this location`
*and still names the source*, so the caller can tell a run worth retrying from a location that will never
be served.

## Dependencies
- GeoSphere `chem-v2-1h-3km` (pollutants, ~73 h hourly) and `chem_aqi-v1-1d-3km` (daily band)
- `air_quality.py` holds the merge; `format.py` holds the normalizer and the renderer

## Design Decisions
- **A separate tool, not a section of `get_current_weather`**: air quality costs two extra requests that
  most current-weather calls do not want.
- **Band-first rendering**: the band is the part a human or model can act on, and it is what the dataset
  publishes.

## Known Risks
- WRF-Chem is a model, not a monitoring network: expect it to track a nearby station without matching it,
  particularly for the traffic-driven pollutants at 3 km resolution.
- Air quality adds two requests per call against the shared GeoSphere budget (5 req/s, 240 req/h).

## Extension Guidelines
- New pollutant: add one row to `AIR_QUALITY_POLLUTANTS` in `const.py` — every request parameter list and
  the renderer's labels derive from it. A pollutant only one source has needs a decision about the output
  shape first.
- Never inline a band label: they live in `AQI_BAND_LABELS`.
