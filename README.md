# geosphere-mcp-server

<!-- mcp-name: io.github.slettmayer/geosphere-mcp-server -->

[![PyPI](https://img.shields.io/pypi/v/geosphere-mcp-server.svg)](https://pypi.org/project/geosphere-mcp-server/)
[![Python](https://img.shields.io/pypi/pyversions/geosphere-mcp-server.svg)](https://pypi.org/project/geosphere-mcp-server/)
[![License: MIT](https://img.shields.io/badge/License-MIT-blue.svg)](LICENSE)

MCP server for weather: current conditions, hourly forecasts, storm outlooks, and air quality for **Austria and the Alpine region**, via the [Model Context Protocol](https://modelcontextprotocol.io).

It serves high-resolution [GeoSphere Austria](https://www.geosphere.at) data — the AROME numerical forecast, the INCA analysis/nowcast, the C-LAEF ensemble for precipitation probability, and the WRF-Chem air-quality forecast. There is **no worldwide fallback**: a point outside the AROME grid returns an out-of-coverage notice, so pair this with a global weather source if you need one. Every response states which datasets produced it.

Output is compact emoji-markdown with metric units — built for smart-home and voice-assistant LLM pipelines where a terse, readable answer beats a JSON blob. Weather conditions are derived from physical parameters and reported with the Home Assistant condition vocabulary (`sunny`, `partlycloudy`, `rainy`, `snowy`, …).

## Coverage

| Where | `get_current_weather` | `get_hourly_forecast` | `get_storm_outlook` | `get_air_quality` |
|-------|-----------------------|-----------------------|---------------------|-------------------|
| Austria | INCA + nowcast + AROME | AROME (≤60 h) + C-LAEF probability | AROME, CAPE gated by CIN | WRF-Chem (3 km) |
| Alps (non-AT) | AROME only | AROME (≤60 h) + C-LAEF probability | AROME, CAPE gated by CIN | WRF-Chem (3 km) |
| Rest of world | *not served* | *not served* | *not served* | *not served* |

Coverage is decided by the API, not by a bounding box you configure: GeoSphere answers HTTP 400 for a point outside the AROME grid, and the server renders that as

```
⚠️ Outside coverage — this server only serves Austria and the Alpine region (the GeoSphere AROME grid).
```

That line is deliberately distinct from the transient failures (`⚠️ Timeout…`, `⚠️ No weather data available`): being outside coverage is a permanent property of the location, so retrying will never help.

**There is no multi-day forecast.** GeoSphere publishes nothing beyond AROME's ~60 h, so the longest answer this server can give is roughly two and a half days of hourly rows.

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

Current conditions for a point, from INCA, the 15-minute nowcast, and AROME.

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

Hour-by-hour forecast from AROME, with C-LAEF precipitation probability, up to ~60 h.

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
```

The hours are grouped under a day-divider header (`%a %Y-%m-%d` in the point's local timezone) that repeats whenever the local date changes, so a window crossing midnight stays unambiguous. Requesting more hours than the AROME horizon provides appends a note naming where the horizon ends — there is nothing further ahead to reach for. Dry hours omit the precipitation and probability parts.

### `get_storm_outlook`

Severe-weather outlook: peak gusts and thunderstorm timing. Reports figures, never a severity verdict — what counts as dangerous is the caller's judgement.

| Parameter | Type | Default | Description |
|-----------|------|---------|-------------|
| `latitude` | float | required | Decimal latitude (e.g. `48.2208`) |
| `longitude` | float | required | Decimal longitude (e.g. `16.3738`) |

```
# Storm Outlook for 48.2208, 16.3738

AROME model, reference 2026-08-10 14:00 CEST · Source: GeoSphere (AROME)

💨 Max gust next 1 h: 12 m/s (at Mon 2026-08-10 17:00)
💨 Max gust next 12 h: 24 m/s (at Mon 2026-08-10 20:00)
⛈️ Thunderstorm expected next 1 h: no
⚡ Next thunderstorm: Mon 2026-08-10 20:00 (CAPE 1800 J/kg)
🌡️ Max CAPE next 12 h: 1800 J/kg
🕐 Timezone: Europe/Vienna (CEST)
```

Two behaviours are worth knowing before you build on this:

- **Horizons round up to whole hours.** The window starts at the top of the current hour, so the "next 1 h" figure covers the hour already under way *plus* the next one, and can report an event up to ~2 h out. Compare the returned timestamps yourself if you need a strict 60-minute answer.
- **`Next thunderstorm` can be in the past**, by up to 59 minutes, when the storm hour is the one already under way. That means a storm is in progress — clamp a negative lead time to zero rather than assuming the stamp is in the future.
- **An all-clear names its horizon** (`none in the next 54 h`). AROME nominally runs ~60 h, but a stale or truncated run reaches less far, so the span is measured from the rows actually returned. It is not an all-clear beyond the stated span.

A thunderstorm hour is one whose derived condition is `lightning`/`lightning-rainy`, *or* one where CAPE ≥ 1000 J/kg with weak inhibition **and** precipitation is forecast — the second branch catches thundersnow and hours with missing cloud data, and requires precipitation so that a dry high-CAPE afternoon does not raise a signal. `Thunderstorm expected` and `Next thunderstorm` both report `unknown` rather than a confident answer when the forecast holds no usable hour.

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

## Data sources & attribution

- **GeoSphere Austria Dataset API** — AROME forecast, INCA analysis/nowcast, C-LAEF ensemble, WRF-Chem air quality. Data licensed under [CC-BY 4.0](https://creativecommons.org/licenses/by/4.0/). © GeoSphere Austria.

The API is keyless and intended for **non-commercial** use. When you redistribute its data, keep the attribution.

## Rate limits

The GeoSphere Dataset API allows **5 requests/second and 240 requests/hour**. Each call issues a small burst of concurrent requests (three for current weather, two for hourly and air quality, one for the storm outlook); on an HTTP 429 the server retries once (when the API asks for a short wait) and otherwise returns a rate-limit notice.

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
