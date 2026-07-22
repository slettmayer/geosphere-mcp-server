"""GeoSphere MCP Server — Austrian weather for LLMs.

GeoSphere Austria data where available (Austria and the Alps), Open-Meteo
worldwide, exposed as MCP tools for LLM voice agents.
"""

from __future__ import annotations

from importlib.metadata import PackageNotFoundError, version

try:
    __version__ = version("geosphere-mcp-server")
except PackageNotFoundError:  # running from a source tree without an install
    __version__ = "0.0.0+unknown"
