"""Tool registry. `load_tools(cfg)` returns the usable tools and says which were skipped."""

from __future__ import annotations

import os
from typing import Any

from .base import Tool, ToolResult
from .firecrawl import FirecrawlScrape
from .plain import PlainFetch
from .tinyfish import TinyFishAgent, TinyFishFetch

REGISTRY: dict[str, type[Tool]] = {
    cls.name: cls for cls in (PlainFetch, FirecrawlScrape, TinyFishFetch, TinyFishAgent)
}


def load_tools(cfg: dict[str, Any], names: list[str] | None = None) -> tuple[list[Tool], list[str]]:
    """Build enabled tools. Returns (tools, skipped_messages). Never raises on a missing key."""
    names = names or cfg["tools"]["enabled"]
    tools, skipped = [], []
    for name in names:
        cls = REGISTRY.get(name)
        if cls is None:
            skipped.append(f"{name}: unknown tool")
            continue
        opts = {"pdf_max_pages": cfg["tools"].get("pdf_max_pages", 10), **cfg["tools"].get(name, {})}
        key = os.environ.get(cls.env_key) if cls.env_key else None
        tool = cls(api_key=key, **opts)
        if not tool.available:
            skipped.append(f"{name}: {cls.env_key} not set, skipping")
            continue
        tools.append(tool)
    return tools, skipped


__all__ = ["Tool", "ToolResult", "REGISTRY", "load_tools"]
