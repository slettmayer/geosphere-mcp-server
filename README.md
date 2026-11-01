# geosphere-mcp-server

<!-- mcp-name: io.github.slettmayer/geosphere-mcp-server -->

[![PyPI](https://img.shields.io/pypi/v/geosphere-mcp-server.svg)](https://pypi.org/project/geosphere-mcp-server/)
[![Python](https://img.shields.io/pypi/pyversions/geosphere-mcp-server.svg)](https://pypi.org/project/geosphere-mcp-server/)
[![License: MIT](https://img.shields.io/badge/License-MIT-blue.svg)](LICENSE)

MCP server for weather: current conditions, hourly forecasts, storm outlooks, and air quality for **Austria and the Alpine region**, via the [Model Context Protocol](https://modelcontextprotocol.io).

It serves high-resolution [GeoSphere Austria](https://www.geosphere.at) data — the 1 km numerical forecast (`nwp-v2-1h-1km`, the deterministic run of the C-LAEF AlpeAdria ensemble, called AROME here), the INCA analysis/nowcast, the C-LAEF ensemble for the precipitation-chance estimate and a thunderstorm signal, and the WRF-Chem air-quality forecast. There is **no worldwide fallback**: a point outside the AROME grid returns an out-of-coverage notice, so pair this with a global weather source if you need one. Every response states which datasets produced it.

Output is compact emoji-markdown with metric units — built for smart-home and voice-assistant LLM pipelines where a terse, readable answer beats a JSON blob. Weather conditions follow GeoSphere's own weather symbol and are reported with the Home Assistant condition vocabulary (`sunny`, `clear-night`, `partlycloudy`, `cloudy`, `fog`, `rainy`, `pouring`, `snowy`, `snowy-rainy`, `lightning-rainy`); for the current condition, observations decide whether and what is falling.

## Coverage

| Where | `get_current_weather` | `get_hourly_forecast` | `get_storm_outlook` | `get_air_quality` |
|-------|-----------------------|-----------------------|---------------------|-------------------|
| Austria | INCA + nowcast + AROME | AROME (≤60 h) + C-LAEF chance estimate | AROME symbol + C-LAEF ensemble | WRF-Chem (3 km) |
| Alps (non-AT) | AROME only | AROME (≤60 h) + C-LAEF chance estimate | AROME symbol + C-LAEF ensemble | WRF-Chem (3 km) |
| Rest of world | *not served* | *not served* | *not served* | *not served* |

Coverage is decided by the API, not by a bounding box you configure: GeoSphere answers HTTP 400 for a point outside the grid, and the server renders that as

```
⚠️ Outside coverage — this server only serves Austria and the Alpine region.
```

That line is deliberately distinct from the transient failures (`⚠️ Timeout…`, `⚠️ No weather data available`): being outside coverage is a permanent property of the location, so retrying will never help. It names no grid on purpose — the forecast tools query the 1 km forecast grid and `get_air_quality` queries WRF-Chem (3 km), so a point can be inside one and outside the other.

**There is no multi-day forecast.** GeoSphere publishes nothing beyond the forecast run's ~60 h, and a run is only available several hours after its start, so the longest answer this server can give is typically 52–55 hours of hourly rows.

## Installation

Pass **decimal latitude/longitude** to every tool. There is no geocoder in the server — the calling LLM geocodes city names to coordinates itself.

### Claude Desktop

Add to your Claude Desktop config (`~/Library/Application Support/Claude/claude_desktop_config.json`):

```json
{
  "mcpServers": {
    "geosphere": {
      "command": "uvx",
      "args": ["geosphere-mcp-server"]
    }
  }
}
```

### Claude Code

```bash
claude mcp add geosphere -- uvx geosphere-mcp-server
```

### From source (development)

```json
{
  "mcpServers": {
    "geosphere": {
      "command": "uvx",
      "args": ["--from", "/path/to/geosphere-mcp-server", "geosphere-mcp-server"]
    }
  }
}
```

### Home Assistant

It is a standard stdio MCP server, so it runs anywhere an stdio MCP server can be hosted — including alongside Home Assistant's Assist pipeline (register it the same way as any other stdio MCP server). Give the voice agent the coordinates of the places it should answer for, or let it geocode names.

## Tools

### `get_current_weather`

Current conditions for a point, from INCA, the 15-minute nowcast, and AROME. The nowcast decides whether and what is falling; the rest of the condition is GeoSphere's weather symbol for the current hour, so with nothing falling under a rain or thunder symbol it reads `cloudy`.

| Parameter | Type | Default | Description |
|-----------|------|---------|-------------|
| `latitude` | float | required | Decimal latitude (e.g. `48.2208`) |
| `longitude` | float | required | Decimal longitude (e.g. `16.3738`) |

```
# Current Weather at 48.2208, 16.3738

🌡️ Temperature: 24.7°C (feels like 24.2°C)
🌤️ Condition: partlycloudy
💧 Humidity: 52%
💨 Wind: 2.9 m/s from 135° (gusts 7 m/s)
🌧️ Precipitation (last hour): 0 mm
📊 Pressure: 1016 hPa
☁️ Cloud cover: 30%
🕐 Timezone: Europe/Vienna (CEST)
📡 Source: GeoSphere (INCA + nowcast + AROME) — observed 15:20
```

### `get_hourly_forecast`

Hour-by-hour forecast from AROME, up to ~60 h. Each hour's condition is GeoSphere's weather symbol; the precipitation chance is an estimate derived from the C-LAEF ensemble percentiles (stepped 0/30/70/95 %), not a probability GeoSphere publishes.

| Parameter | Type | Default | Description |
|-----------|------|---------|-------------|
| `latitude` | float | required | Decimal latitude (e.g. `48.2208`) |
| `longitude` | float | required | Decimal longitude (e.g. `16.3738`) |
| `hours` | int | 24 | Forecast hours (clamped to 1–60) |
| `start` | string | now | Optional ISO 8601 start (e.g. `2026-07-22T15:00`); forecast begins at/after this instant |

```
# 3-Hour Forecast for 48.2208, 16.3738

AROME model, reference 2026-07-22 12:00 CEST · Source: GeoSphere (AROME + C-LAEF ensemble)

Wed 2026-07-22
  15:00: 24.7°C — partlycloudy, wind 3 m/s
  16:00: 23.9°C — rainy, 1.2 mm (70% chance), wind 4 m/s
  17:00: 22.5°C — cloudy, wind 3 m/s

Precipitation chance is an estimate derived from the C-LAEF ensemble percentiles (0/30/70/95 %), not a probability GeoSphere publishes.
```

The hours are grouped under a day-divider header (`%a %Y-%m-%d` in the point's local timezone) that repeats whenever the local date changes, so a window crossing midnight stays unambiguous. Requesting more hours than the AROME horizon provides appends a note naming where the horizon ends — there is nothing further ahead to reach for. Dry hours, and hours whose amount rounds to 0 mm, omit the precipitation and chance parts; the estimate note appears only when a chance is shown.

### `get_storm_outlook`

Severe-weather outlook: peak gusts and thunderstorm timing. Reports figures, never a severity verdict — what counts as dangerous is the caller's judgement.

| Parameter | Type | Default | Description |
|-----------|------|---------|-------------|
| `latitude` | float | required | Decimal latitude (e.g. `48.2208`) |
| `longitude` | float | required | Decimal longitude (e.g. `16.3738`) |

```
# Storm Outlook for 48.2208, 16.3738

AROME model, reference 2026-08-10 14:00 CEST · Source: GeoSphere (AROME + C-LAEF ensemble)

💨 Max gust next 1 h: 12 m/s (at Mon 2026-08-10 17:00)
💨 Max gust next 12 h: 24 m/s (at Mon 2026-08-10 20:00)
⛈️ Thunderstorm expected next 1 h: no
⚡ Next thunderstorm: Mon 2026-08-10 20:00 (signal: symbol)
🌡️ Max CAPE next 12 h: 1800 J/kg
🕐 Timezone: Europe/Vienna (CEST)

Horizons round up to whole hours: the "1 h" window covers the hour already under way plus the next one. A thunderstorm timestamp at or before now means one is already in progress.
A thunderstorm hour is one whose GeoSphere weather symbol is a thunderstorm, or where the C-LAEF ensemble's 90th percentiles reach 1000 J/kg CAPE and 0.1 mm precipitation; `signal` names which fired (symbol, ensemble or both). Max CAPE is a diagnostic and decides nothing.
```

Two behaviours are worth knowing before you build on this:

- **Horizons round up to whole hours.** The window starts at the top of the current hour, so the "next 1 h" figure covers the hour already under way *plus* the next one, and can report an event up to ~2 h out. Compare the returned timestamps yourself if you need a strict 60-minute answer.
- **`Next thunderstorm` can be in the past**, by up to 59 minutes, when the storm hour is the one already under way. That means a storm is in progress — clamp a negative lead time to zero rather than assuming the stamp is in the future.
- **An all-clear names its horizon** (`none in the next 54 h`). The run nominally covers ~60 h, but it is published hours after its start and a stale or truncated run reaches less far, so the span is measured from the rows actually returned. It is not an all-clear beyond the stated span.

A thunderstorm hour is one whose GeoSphere weather symbol is a thunderstorm (codes 26–32), **or** one where the C-LAEF ensemble's 90th percentiles reach 1000 J/kg CAPE **and** 0.1 mm of precipitation — the ensemble counts only while its run is less than 12 h old. Either signal suffices, and every thunderstorm answer names it: `signal: symbol`, `ensemble` or `both`. Without a current ensemble run the outlook says so (`ℹ️ Ensemble signal: unavailable …`) and rests on the symbol alone. `Thunderstorm expected` reads `unknown (no forecast hours in the window)` only when no forecast hour falls in the window; an hour with a missing symbol counts as no storm. `Max CAPE` is the deterministic run's CAPE, a diagnostic that decides nothing.

### `get_air_quality`

Pollutant concentrations now, plus the European Air Quality Index for today, tomorrow and in two days.

| Parameter | Type | Default | Description |
|-----------|------|---------|-------------|
| `latitude` | float | required | Decimal latitude (e.g. `48.2208`) |
| `longitude` | float | required | Decimal longitude (e.g. `16.3738`) |

```
# Air Quality at 48.2208, 16.3738

🏷️ European AQI: 2 (fair) today · 3 (moderate) tomorrow · 2 (fair) in 2 days
🌫️ Concentrations (16:00): NO₂ 18 µg/m³ · O₃ 92 µg/m³ · PM10 21 µg/m³ · PM2.5 12 µg/m³
🕐 Timezone: Europe/Vienna (CEST)
📡 Source: GeoSphere (WRF-Chem + daily AQI, 3 km)
```

The AQI is reported as its 1–6 EEA band (1 good … 6 extremely poor), which is what GeoSphere publishes — there is no underlying numeric index to show alongside it. These are **model forecasts, not station measurements** — expect them to track a nearby monitoring station without matching it.

A point inside the 3 km grid can still come back empty when a WRF-Chem run is stale or incomplete; the response then says `No air-quality data available for this location` and still names the source, which is a different answer from being out of coverage.

### Output changes after 0.6.0

GeoSphere retired the forecast datasets this server used (`nwp-v1-1h-2500m`, `ensemble-v1-1h-2500m`); the server now reads `nwp-v2-1h-1km` and `ensemble-v2-1h-1km`. What an agent reading the tools will notice:

- **Conditions** come from GeoSphere's weather symbol. `windy`, `windy-variant` and dry `lightning` no longer occur; `fog` comes only from GeoSphere's fog symbols and may not occur at all. An hour with a missing or unknown symbol has no condition.
- **Current condition:** observed rain reads `pouring` from 5 mm/h (was 4 mm/h); nothing falling under a rain or thunder symbol reads `cloudy`; outside Austria (no observations) the symbol stands, so rain and thunderstorms can now appear there.
- **`get_storm_outlook`:** the source line includes `C-LAEF ensemble`; `Thunderstorm expected next 1 h: yes` gains `(signal: …)`; `Next thunderstorm` shows `(signal: …)` instead of `(CAPE N J/kg)`; `unknown` now reads `unknown (no forecast hours in the window)` and `Next thunderstorm` no longer reports `unknown`; a new `ℹ️ Ensemble signal: unavailable …` line appears without a current ensemble run; a second trailing note defines a thunderstorm hour. Expect more thunderstorm answers in convective weather.
- **`get_hourly_forecast`:** a trailing note labels the precipitation chance as an estimate; amounts that round to 0 mm are no longer printed.

See [CHANGELOG.md](CHANGELOG.md) for the full list.

## Data sources & attribution

- **GeoSphere Austria Dataset API** — the 1 km forecast (`nwp-v2-1h-1km`) and C-LAEF ensemble (`ensemble-v2-1h-1km`), INCA analysis/nowcast, WRF-Chem air quality. Data licensed under [CC-BY 4.0](https://creativecommons.org/licenses/by/4.0/). © GeoSphere Austria.

The API is keyless and intended for **non-commercial** use. When you redistribute its data, keep the attribution.

## Rate limits

The GeoSphere Dataset API allows **5 requests/second and 240 requests/hour**. Each call issues a small burst of concurrent requests (three for current weather, two each for the hourly forecast, the storm outlook and air quality); on an HTTP 429 the server retries once (when the API asks for a short wait) and otherwise returns a rate-limit notice.

## Development

```bash
# Install dependencies (creates .venv from the locked versions)
uv sync

# Lint & format
ruff check .
ruff format .

# Run unit tests
pytest -m "not integration"

# Run integration tests (hits the live GeoSphere API)
pytest -m integration
```

## License

MIT
