"""GeoSphere MCP Server — Austrian weather for LLMs.

High-resolution GeoSphere Austria data for Austria and the Alpine region,
exposed as MCP tools for LLM voice agents. Points outside that region are not
served; there is no worldwide fallback.
"""

from __future__ import annotations

from importlib.metadata import PackageNotFoundError, version

try:
    __version__ = version("geosphere-mcp-server")
except PackageNotFoundError:  # running from a source tree without an install
    __version__ = "0.0.0+unknown"
