# Tool and Output Contract

## Purpose
Documents the five MCP tools' exact signatures and argument validation, and the markdown each renderer
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
| `get_current_weather` | `(latitude, longitude) -> str` | GeoSphere merge, else Open-Meteo | now |
| `get_hourly_forecast` | `(latitude, longitude, hours=24, start=None) -> str` | GeoSphere AROME (+C-LAEF), else Open-Meteo | 1-60 h GeoSphere / 1-48 h fallback |
| `get_daily_forecast` | `(latitude, longitude, days=7, start_date=None, end_date=None) -> str` | Open-Meteo only | 1-16 days |
| `get_storm_outlook` | `(latitude, longitude) -> str` | GeoSphere AROME (no ensemble), else Open-Meteo | fixed 1 h / 12 h windows over the full series |
| `get_air_quality` | `(latitude, longitude) -> str` | GeoSphere WRF-Chem, else Open-Meteo CAMS | now + 3 days |

Location input is plain decimal `latitude`/`longitude`. The calling model geocodes place names; the server
has no geocoder. Output is compact markdown in metric units. Tools never raise — every failure resolves to
a short markdown error line.

### Argument Validation and Clamping

**`hours`** is clamped silently into `1-60` on the GeoSphere path and `1-48` on the Open-Meteo fallback.

**`start`** accepts an ISO 8601 datetime. A naive value is assumed to be UTC. An unparseable value returns
`⚠️ Invalid start time '{start}'; use ISO 8601 (e.g. 2026-07-22T15:00)` **before** any HTTP session is
opened.

**`days`** is clamped silently into `1-16`. It is ignored entirely when a date range is supplied.

**`start_date` / `end_date`** switch `get_daily_forecast` into explicit-range mode as soon as *either* is
present. The defaults fill in symmetrically: a missing `end_date` defaults to `start_date`, and a missing
`start_date` defaults to `end_date` — so passing only `end_date` yields a single-day forecast for that
date. Validation, in order:

1. An unparseable date returns `⚠️ Invalid {label} '{value}'; use an ISO 8601 date (e.g. 2026-07-25)`,
   where `label` is `start_date` or `end_date`.
2. A range that runs backwards returns `⚠️ end_date '{end}' is before start_date '{start}'`.
3. A valid range longer than 16 days is **silently truncated** to 16 inclusive days from `start_date`,
   mirroring the silent clamp applied to the `days` count.

The tool docstring instructs callers to resolve a named period such as "the weekend" into exact calendar
dates and pass those, rather than converting the period into a day count.

### Current Weather Output
The only renderer that uses per-field emoji. Fields are omitted when the value is unavailable:

`🌡️ Temperature` (with an appended "feels like" suffix), `🌤️ Condition`, `💧 Humidity`, `💨 Wind`
(speed, bearing, gust), `🌧️ Precipitation (last hour)`, `📊 Pressure`, `☁️ Cloud cover`, `🌅 Sunrise`,
`🌇 Sunset`, `🕐 Timezone`, and a closing `📡 Source: ...` line.

On the GeoSphere path the source names every contributing dataset, for example
`📡 Source: GeoSphere (INCA + nowcast + AROME)`. On the fallback path it is `📡 Source: Open-Meteo`. When
an observation time is available the line gains an `— observed {HH:MM}` suffix.

### Hourly Forecast Output
Plain text lines with **no per-field emoji**. The header carries the model, reference time, and source on
one line, for example `AROME model, reference 2026-07-25 06:00 CEST · Source: GeoSphere (AROME)` — note
this line uses `· Source:` with no `📡`.

Hours are grouped under **day-divider headers**. Whenever the local calendar date changes, a header line
formatted `%a %Y-%m-%d` (for example `Sat 2026-07-25`) is emitted and the hour lines beneath it are
indented by two spaces. The grouping lives in the shared renderer, so it applies identically to the
GeoSphere and Open-Meteo paths.

**The series starts with the hour already under way**, not the next one: the window floors to the top of
the current hour, so a request at 15:50 leads with the 15:00 line. Both source paths behave this way (the
GeoSphere path needs an hour of lookback to manage it — see [STORM-OUTLOOK.md](STORM-OUTLOOK.md)), matching
the OpenWeatherMap server this one replaces. `hours=N` therefore yields the in-progress hour plus `N - 1`
later ones, and the leading hour's precipitation figure covers the whole hour, including the part of it
that has already elapsed.

Each hour renders as `HH:MM: {temp}°C — {condition}, {precip} mm ({prob}% chance), wind {speed} m/s`.
Precipitation is omitted when zero or unavailable, the probability is omitted when it is absent or zero,
and the whole line degrades to `HH:MM: n/a` when there is no temperature.

When the **GeoSphere** path returns fewer hours than were requested, a trailing note is appended:
`Note: AROME forecast horizon ends {timestamp} (~60 h); use get_daily_forecast for days further ahead.`
The Open-Meteo path never emits this note — a short window there is rendered without explanation. An empty
window on either path renders `No forecast hours available for the requested window.`

### Daily Forecast Output
Plain text, no emoji anywhere — including the attribution, which is `Source: Open-Meteo ({timezone})` with
**no** `📡`. The timezone in parentheses is whatever the response reported (Open-Meteo is queried with
`timezone=auto`, so it reflects the requested coordinates), and it is omitted when the response carries
none.

Each day renders as
`{Day} {YYYY-MM-DD}: {min}–{max}°C — {condition}, {precip} mm ({prob}% chance), wind up to {speed} m/s`,
falling back to the max alone when there is no minimum, and to `n/a` when neither is available. An empty
result renders `No daily forecast available.`

### Storm Outlook Output
Per-field emoji, like current weather. The header carries model, reference time, and source on one line
(`AROME model, reference 2026-08-10 14:00 CEST · Source: GeoSphere (AROME)`).

`💨 Max gust next 1 h` and `💨 Max gust next 12 h` render as `{N} m/s (at {Day} {YYYY-MM-DD} {HH:MM})`, or
`unknown` when the window holds no gust value. `⛈️ Thunderstorm expected next 1 h` is tri-state: `yes`,
`no`, or `unknown (no usable forecast hours)`. `⚡ Next thunderstorm` renders the stamp and that hour's
CAPE, `none in the next {N} h` when the series is readable and calm — naming the horizon actually
scanned, which is ~60 h on AROME but only what remains of three days from local midnight on the fallback —
or
`unknown (no usable forecast hours)` when no hour ahead can be judged at all. `🌡️ Max CAPE next 12 h` is omitted when unavailable. A
`🕐 Timezone` line closes the block.

A trailing note always explains the round-up horizon and the storm-in-progress timestamp. It is identical
on both source paths — the outlook carries no source-specific caveat, because both sources supply
convective inhibition. A series with no hours at
all renders `No forecast hours available for the outlook window.` See
[STORM-OUTLOOK.md](STORM-OUTLOOK.md) for the semantics behind each figure.

### Air Quality Output
Two content lines plus attribution. `🏷️ European AQI` joins the available days with ` · `, each as
`{band} ({label}) {day}` — with `, index {N}` inserted after the label on the Open-Meteo path, which has a
numeric index. A day whose value is unknown is omitted rather than rendered as a gap.
`🌫️ Concentrations ({HH:MM})` joins the four pollutants with ` · `. Then `🕐 Timezone`, a
`📡 Source: ...` line, and a trailing note giving the six EEA band names and stating that these are model
forecasts rather than station measurements. With neither AQI nor concentrations available the body is
`No air-quality data available for this location.` — still followed by the `📡 Source:` line, because which
source drew the blank is what tells the caller whether asking elsewhere is worth anything; the band legend
is dropped there, having no figures left to explain. See [AIR-QUALITY.md](AIR-QUALITY.md).

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
| Any of the above on current / hourly / storm outlook | gains the suffix `— get_daily_forecast still works (Open-Meteo).` |
| Rate limit on air quality | no such suffix: the daily forecast carries no air-quality data |
| Timeout against either upstream API | `⚠️ Timeout fetching weather data` |
| Anything else, including non-timeout network failures | `⚠️ No weather data available` |

## Dependencies
- `server.py` owns argument parsing, validation, and clamping
- `format.py` owns normalization and all five renderers
- Horizon bounds come from `AROME_MAX_HOURS`, `OPENMETEO_MAX_HOURS`, and `OPENMETEO_MAX_DAYS` in `const.py`

## Design Decisions
- **Tool names mirror the OpenWeatherMap server** they replace, so existing agent routing prompts transfer
  unchanged.
- **Markdown rather than JSON**: the output is tuned for voice-oriented assistants reading results aloud,
  which is also why emoji density drops from current weather to the list-shaped hourly and daily output.
- **Silent clamping over rejection**: an over-long horizon returns the best available window instead of an
  error, since a partial forecast is more useful to a caller than a refusal.
- **Explicit date ranges alongside day counts**: models resolve "the weekend" to calendar dates far more
  reliably than to an offset from today.

## Known Risks
- Attribution formatting is inconsistent across the tools (`📡 Source:`, `· Source:`, `Source:`),
  so any consumer parsing the source string must handle all three shapes.
- Silent truncation of an over-long date range is invisible to the caller — the header states the rendered
  day count, but nothing states that the request was shortened.

## Extension Guidelines
- New tool: register it in `server.py`, add orchestration in `weather.py`, add a renderer in `format.py`,
  and document its signature and output shape here.
- New rendered field: normalization must forward it before the renderer can read it — check the normalize
  function first, since several fields are already assembled but dropped.
- New validation rule: return a `⚠️ `-prefixed line and add it to the error table above.
