# Tool and Output Contract

## Purpose
Documents the four MCP tools' exact signatures and argument validation, and the markdown each renderer
produces — fields, source attribution, day-divider headers, and horizon notes.

## Responsibilities
- Specifying each tool's parameters, defaults, bounds, and validation errors
- Documenting the rendered field inventory per tool
- Documenting source-attribution line variants and the day-divider grouping
- Recording which computed values never reach the output

## Non-Responsibilities
- Which upstream dataset supplies a field (see [DATA-SOURCES-AND-COVERAGE.md](DATA-SOURCES-AND-COVERAGE.md))
- How a condition string is computed (see [CONDITION-DERIVATION.md](CONDITION-DERIVATION.md))
- Renderer module boundaries (see [../tech/ARCHITECTURE.md](../tech/ARCHITECTURE.md))

## Overview

### Tools

| Tool | Signature | Sources | Horizon |
|------|-----------|---------|---------|
| `get_current_weather` | `(latitude, longitude) -> str` | INCA + nowcast + AROME merge | now |
| `get_hourly_forecast` | `(latitude, longitude, hours=24, start=None) -> str` | AROME (+C-LAEF) | 1-60 h |
| `get_storm_outlook` | `(latitude, longitude) -> str` | AROME (no ensemble) | fixed 1 h / 12 h windows over the full series |
| `get_air_quality` | `(latitude, longitude) -> str` | WRF-Chem | now + 3 days |

Location input is plain decimal `latitude`/`longitude`. The calling model geocodes place names; the server
has no geocoder. Output is compact markdown in metric units. Tools never raise — every failure resolves to
a short markdown error line, including a point outside coverage.

**There is no daily or multi-day tool.** No GeoSphere dataset reaches past AROME's ~60 h and there is no
second source, so the longest answer available is `get_hourly_forecast` at its cap.

### Argument Validation and Clamping

**`hours`** is clamped silently into `1-60` (`AROME_MAX_HOURS`).

**`start`** accepts an ISO 8601 datetime. A naive value is assumed to be UTC. An unparseable value returns
`⚠️ Invalid start time '{start}'; use ISO 8601 (e.g. 2026-07-22T15:00)` **before** any HTTP session is
opened.

### Current Weather Output
The only renderer that uses per-field emoji. Fields are omitted when the value is unavailable:

`🌡️ Temperature` (with an appended "feels like" suffix), `🌤️ Condition`, `💧 Humidity`, `💨 Wind`
(speed, bearing, gust), `🌧️ Precipitation (last hour)`, `📊 Pressure`, `☁️ Cloud cover`, `🌅 Sunrise`,
`🕐 Timezone`, and a closing `📡 Source: ...` line. There are no sunrise/sunset lines — no GeoSphere
dataset publishes them.

The source names every contributing dataset, for example
`📡 Source: GeoSphere (INCA + nowcast + AROME)`. When an observation time is available the line gains an
`— observed {HH:MM}` suffix.

### Hourly Forecast Output
Plain text lines with **no per-field emoji**. The header carries the model, reference time, and source on
one line, for example `AROME model, reference 2026-07-25 06:00 CEST · Source: GeoSphere (AROME)` — note
this line uses `· Source:` with no `📡`.

Hours are grouped under **day-divider headers**. Whenever the local calendar date changes, a header line
formatted `%a %Y-%m-%d` (for example `Sat 2026-07-25`) is emitted and the hour lines beneath it are
indented by two spaces.

**The series starts with the hour already under way**, not the next one: the window floors to the top of
the current hour, so a request at 15:50 leads with the 15:00 line. That takes an hour of lookback on the
request — see [STORM-OUTLOOK.md](STORM-OUTLOOK.md) — and matches the OpenWeatherMap server this one
replaces. `hours=N` therefore yields the in-progress hour plus `N - 1`
later ones, and the leading hour's precipitation figure covers the whole hour, including the part of it
that has already elapsed.

Each hour renders as `HH:MM: {temp}°C — {condition}, {precip} mm ({prob}% chance), wind {speed} m/s`.
Precipitation is omitted when zero or unavailable, the probability is omitted when it is absent or zero,
and the whole line degrades to `HH:MM: n/a` when there is no temperature.

When fewer hours come back than were requested, a trailing note is appended:
`Note: AROME forecast horizon ends {timestamp} (~60 h). This server publishes nothing beyond it.`
An empty window renders `No forecast hours available for the requested window.`

### Storm Outlook Output
Per-field emoji, like current weather. The header carries model, reference time, and source on one line
(`AROME model, reference 2026-08-10 14:00 CEST · Source: GeoSphere (AROME)`).

`💨 Max gust next 1 h` and `💨 Max gust next 12 h` render as `{N} m/s (at {Day} {YYYY-MM-DD} {HH:MM})`, or
`unknown` when the window holds no gust value. `⛈️ Thunderstorm expected next 1 h` is tri-state: `yes`,
`no`, or `unknown (no usable forecast hours)`. `⚡ Next thunderstorm` renders the stamp and that hour's
CAPE, `none in the next {N} h` when the series is readable and calm — naming the horizon actually
scanned, which is nominally ~60 h but less when a run is stale or truncated — or
`unknown (no usable forecast hours)` when no hour ahead can be judged at all. `🌡️ Max CAPE next 12 h` is omitted when unavailable. A
`🕐 Timezone` line closes the block.

A trailing note always explains the round-up horizon and the storm-in-progress timestamp. A series with no
hours at all renders `No forecast hours available for the outlook window.` See
[STORM-OUTLOOK.md](STORM-OUTLOOK.md) for the semantics behind each figure.

### Air Quality Output
Two content lines plus attribution. `🏷️ European AQI` joins the available days with ` · `, each as
`{band} ({label}) {day}`. GeoSphere publishes no underlying numeric index, so the band is the whole
figure. A day whose value is unknown is omitted rather than rendered as a gap.
`🌫️ Concentrations ({HH:MM})` joins the four pollutants with ` · `. Then `🕐 Timezone`, a
`📡 Source: ...` line, and a trailing note giving the six EEA band names and stating that these are model
forecasts rather than station measurements. With neither AQI nor concentrations available the body is
`No air-quality data available for this location.` — still followed by the `📡 Source:` line, which is what
distinguishes an in-coverage run that came back empty (worth retrying) from a point outside coverage
(never worth retrying, and answered by the out-of-coverage line instead). The band legend is dropped
there, having no figures left to explain. See [AIR-QUALITY.md](AIR-QUALITY.md).

### Values Computed but Never Rendered
Several merged and assembled fields are dropped during normalization and never appear in any output.
Current weather drops dew point, global radiation, snow limit, CAPE, CIN, precipitation type, and the
precipitation flag. The hourly path additionally computes wind bearing, wind gust, dew point, humidity,
cloud cover, snowfall, snow limit, CAPE, CIN, and global radiation, of which the renderer reads only time,
temperature, condition, precipitation, probability, and wind speed. Surfacing any of them is a renderer
change only — the data is already assembled.

### Error Lines
Every tool resolves failures to one of these lines rather than raising. See
[../tech/ARCHITECTURE.md](../tech/ARCHITECTURE.md) for which exception maps to which line, and for the
rate-limit retry policy.

| Situation | Line |
|-----------|------|
| Rate limit, retry-after known | `⚠️ GeoSphere rate limit exceeded (retry in {N}s)` |
| Rate limit, no retry-after | `⚠️ GeoSphere rate limit exceeded (retry shortly)` |
| Point outside the AROME grid | `⚠️ Outside coverage — this server only serves Austria and the Alpine region (the GeoSphere AROME grid).` |
| Timeout against the upstream API | `⚠️ Timeout fetching weather data` |
| Anything else, including non-timeout network failures | `⚠️ No weather data available` |

Every line but the coverage one describes a **transient** condition worth retrying. The coverage line
describes the location, and no amount of retrying will change it — which is why it is worded and handled
separately (`server.OUT_OF_DOMAIN_MESSAGE`) rather than folded into the generic failure line.

## Dependencies
- `server.py` owns argument parsing, validation, and clamping
- `format.py` owns normalization and all four renderers
- The horizon bound comes from `AROME_MAX_HOURS` in `const.py`

## Design Decisions
- **Tool names mirror the OpenWeatherMap server** they replace, so existing agent routing prompts transfer
  unchanged.
- **Markdown rather than JSON**: the output is tuned for voice-oriented assistants reading results aloud,
  which is also why emoji density drops from current weather to the list-shaped hourly and daily output.
- **Silent clamping over rejection**: an over-long horizon returns the best available window instead of an
  error, since a partial forecast is more useful to a caller than a refusal.

## Known Risks
- Attribution formatting is inconsistent across the tools (`📡 Source:` versus `· Source:`), so any
  consumer parsing the source string must handle both shapes.
- A caller whose agent prompt still routes multi-day questions here gets the hourly cap or a horizon note,
  not an error — the absence of a daily tool is only discoverable from the tool list and the
  `instructions` string.

## Extension Guidelines
- New tool: register it in `server.py`, add orchestration in `weather.py`, add a renderer in `format.py`,
  and document its signature and output shape here.
- New rendered field: normalization must forward it before the renderer can read it — check the normalize
  function first, since several fields are already assembled but dropped.
- New validation rule: return a `⚠️ `-prefixed line and add it to the error table above.
