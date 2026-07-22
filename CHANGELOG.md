# Changelog

## 0.1.0 - 2026-07-22

- Initial release
- MCP server with 3 tools: `get_current_weather`, `get_hourly_forecast`, `get_daily_forecast`
- High-resolution weather for Austria and the Alps from the GeoSphere Austria Dataset API: AROME forecast (~60 h hourly), INCA analysis, INCA nowcast, and C-LAEF ensemble precipitation probability
- Automatic worldwide fallback to Open-Meteo when a point is outside GeoSphere coverage (current + hourly tools); daily forecast (1-16 days) always via Open-Meteo
- Location input is plain decimal `latitude`/`longitude` (the calling LLM geocodes place names)
- HA-style condition vocabulary derived physically on the GeoSphere path and mapped from WMO weather codes on the Open-Meteo path
- Compact emoji-markdown output (metric units), each response naming its data source
- First public distribution: published to [PyPI](https://pypi.org/project/geosphere-mcp-server/) (installable via `uvx geosphere-mcp-server`) and listed in the [official MCP Registry](https://registry.modelcontextprotocol.io)
- Tag-driven release pipeline: PyPI Trusted Publishing (OIDC), GitHub Release, and MCP Registry publish on `v*` tags
- Version single-sourced from git tags via `hatch-vcs`; PyPI metadata (authors, URLs, classifiers, keywords) and a `py.typed` marker
