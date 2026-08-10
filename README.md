# geosphere-mcp-server

<!-- mcp-name: io.github.slettmayer/geosphere-mcp-server -->

[![PyPI](https://img.shields.io/pypi/v/geosphere-mcp-server.svg)](https://pypi.org/project/geosphere-mcp-server/)
[![Python](https://img.shields.io/pypi/pyversions/geosphere-mcp-server.svg)](https://pypi.org/project/geosphere-mcp-server/)
[![License: MIT](https://img.shields.io/badge/License-MIT-blue.svg)](LICENSE)

MCP server for weather: current conditions, hourly forecasts, multi-day outlooks, storm outlooks, and air quality for **any location worldwide**, via the [Model Context Protocol](https://modelcontextprotocol.io).

In **Austria and the Alpine region** it serves high-resolution [GeoSphere Austria](https://www.geosphere.at) data — the AROME numerical forecast, the INCA analysis/nowcast, and the C-LAEF ensemble for precipitation probability. **Everywhere else** it falls back automatically to [Open-Meteo](https://open-meteo.com), so it is a drop-in worldwide weather source. Every response states which source produced it.

Output is compact emoji-markdown with metric units — built for smart-home and voice-assistant LLM pipelines where a terse, readable answer beats a JSON blob. Weather conditions are derived from physical parameters and reported with the Home Assistant condition vocabulary (`sunny`, `partlycloudy`, `rainy`, `snowy`, …).

## Coverage

| Where | `get_current_weather` | `get_hourly_forecast` | `get_daily_forecast` | `get_storm_outlook` | `get_air_quality` |
|-------|-----------------------|-----------------------|----------------------|---------------------|-------------------|
| Austria | GeoSphere INCA + nowcast + AROME | GeoSphere AROME (≤60 h) + C-LAEF probability | Open-Meteo (1–16 days) | GeoSphere AROME, CAPE gated by CIN | GeoSphere WRF-Chem (3 km) |
| Alps (non-AT) | GeoSphere AROME only | GeoSphere AROME (≤60 h) + C-LAEF probability | Open-Meteo (1–16 days) | GeoSphere AROME, CAPE gated by CIN | GeoSphere WRF-Chem (3 km) |
| Rest of world | Open-Meteo | Open-Meteo (≤48 h) | Open-Meteo (1–16 days) | Open-Meteo, CAPE only | Open-Meteo (CAMS) |

Coverage is detected automatically: the server tries GeoSphere first and falls back to Open-Meteo when the point is outside the AROME grid — no bounding box to configure. The daily forecast always uses Open-Meteo (GeoSphere publishes no forecasts beyond ~60 h).

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

Current conditions for a point. GeoSphere (INCA/AROME) inside coverage, Open-Meteo elsewhere.

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

Outside GeoSphere coverage the same tool answers from Open-Meteo (sunrise/sunset and the point's own timezone included):

```
# Current Weather at 38.7223, -9.1393

🌡️ Temperature: 26.1°C (feels like 27.0°C)
🌤️ Condition: cloudy
💧 Humidity: 58%
💨 Wind: 4.5 m/s from 315° (gusts 9 m/s)
📊 Pressure: 1014 hPa
☁️ Cloud cover: 90%
🌅 Sunrise: 06:24
🌇 Sunset: 20:52
🕐 Timezone: Europe/Lisbon (WEST)
📡 Source: Open-Meteo — observed 14:00
```

### `get_hourly_forecast`

Hour-by-hour forecast. GeoSphere AROME (with C-LAEF precipitation probability) up to ~60 h inside coverage; Open-Meteo up to 48 h elsewhere.

| Parameter | Type | Default | Description |
|-----------|------|---------|-------------|
| `latitude` | float | required | Decimal latitude (e.g. `48.2208`) |
| `longitude` | float | required | Decimal longitude (e.g. `16.3738`) |
| `hours` | int | 24 | Forecast hours (clamped to 1–60 on GeoSphere, 1–48 on the fallback) |
| `start` | string | now | Optional ISO 8601 start (e.g. `2026-07-22T15:00`); forecast begins at/after this instant |

```
# 3-Hour Forecast for 48.2208, 16.3738

AROME model, reference 2026-07-22 12:00 CEST · Source: GeoSphere (AROME + C-LAEF ensemble)

Wed 2026-07-22
  15:00: 24.7°C — partlycloudy, wind 3 m/s
  16:00: 23.9°C — rainy, 1.2 mm (70% chance), wind 4 m/s
  17:00: 22.5°C — cloudy, wind 3 m/s
```

The hours are grouped under a day-divider header (`%a %Y-%m-%d` in the point's local timezone) that repeats whenever the local date changes, so a window crossing midnight stays unambiguous. Requesting more hours than the AROME horizon provides appends a note suggesting `get_daily_forecast` for days further ahead. Dry hours omit the precipitation and probability parts.

### `get_daily_forecast`

Multi-day outlook, always from Open-Meteo (worldwide, including Austria).

| Parameter | Type | Default | Description |
|-----------|------|---------|-------------|
| `latitude` | float | required | Decimal latitude (e.g. `48.2208`) |
| `longitude` | float | required | Decimal longitude (e.g. `16.3738`) |
| `days` | int | 7 | Forecast days from today (clamped to 1–16). Ignored when `start_date`/`end_date` are given |
| `start_date` | str | — | Optional first forecast day, ISO `YYYY-MM-DD` |
| `end_date` | str | — | Optional last forecast day (inclusive), ISO `YYYY-MM-DD`; defaults to `start_date`. Span capped at 16 days |

Request either a count of days from today (`days`) or an explicit calendar range (`start_date`/`end_date`). The range is the reliable way to answer a named period — for "the weekend" or "next Tuesday", the caller resolves today's date, then passes the exact dates rather than converting the period into a day count.

```
# 3-Day Forecast for 48.2208, 16.3738

Source: Open-Meteo (Europe/Vienna)

Wed 2026-07-22: 16–27°C — partlycloudy, wind up to 9 m/s
Thu 2026-07-23: 15–22°C — rainy, 4.2 mm (80% chance), wind up to 15 m/s
Fri 2026-07-24: 14–26°C — sunny, wind up to 8 m/s
```

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

A thunderstorm hour is one whose derived condition is `lightning`/`lightning-rainy`, *or* one where CAPE ≥ 1000 J/kg with weak inhibition **and** precipitation is forecast — the second branch catches thundersnow and hours with missing cloud data, and requires precipitation so that a dry high-CAPE afternoon does not raise a signal. `Thunderstorm expected` and `Next thunderstorm` both report `unknown` rather than a confident answer when the forecast holds no usable hour. Both sources supply convective inhibition, so the gate works on either path — Open-Meteo reports it as a positive magnitude and the server normalizes the sign.

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

The AQI is always reported as its 1–6 EEA band (1 good … 6 extremely poor) so both sources read alike. GeoSphere publishes that band directly; Open-Meteo publishes a 0–100+ numeric index instead, which is banded and rendered as `3 (moderate, index 44)`. Open-Meteo also has no daily index, so each day there is the maximum of that day's hourly values. Both sources are **model forecasts, not station measurements** — expect them to track a nearby monitoring station without matching it.

## Data sources & attribution

- **GeoSphere Austria Dataset API** — AROME forecast, INCA analysis/nowcast, C-LAEF ensemble, WRF-Chem air quality. Data licensed under [CC-BY 4.0](https://creativecommons.org/licenses/by/4.0/). © GeoSphere Austria.
- **Open-Meteo** — worldwide forecast and air-quality (CAMS) APIs. Data licensed under [CC-BY 4.0](https://creativecommons.org/licenses/by/4.0/). © Open-Meteo.

Both APIs are keyless and intended for **non-commercial** use. When you redistribute their data, keep the attribution.

## Rate limits

The GeoSphere Dataset API allows **5 requests/second and 240 requests/hour**. Each GeoSphere-path call issues a small burst of concurrent requests (three for current weather, two for hourly and air quality, one for the storm outlook); on an HTTP 429 the server retries once (when the API asks for a short wait) and otherwise returns a rate-limit notice — `get_daily_forecast` keeps working through Open-Meteo in that case. Open-Meteo has its own generous free-tier limits.

## Development

```bash
# Install dependencies (creates .venv from the locked versions)
uv sync

# Lint & format
ruff check .
ruff format .

# Run unit tests
pytest -m "not integration"

# Run integration tests (hits the live GeoSphere + Open-Meteo APIs)
pytest -m integration
```

## License

MIT
