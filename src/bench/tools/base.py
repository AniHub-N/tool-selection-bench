"""Common interface every web tool implements.

`run(task_input) -> ToolResult`, where task_input is a dict with at least `url`
(and optionally `goal`, used only by tools that take an instruction).
"""

from __future__ import annotations

import time
from dataclasses import asdict, dataclass, field
from typing import Any

# Hard cap on returned content so one huge page can't blow up memory or logs.
# The agent applies its own (smaller) truncation before sending content to the LLM.
MAX_CONTENT_CHARS = 200_000


@dataclass
class ToolResult:
    content: str = ""
    ok: bool = False
    latency_ms: int = 0
    cost_usd: float = 0.0
    error: str | None = None
    meta: dict[str, Any] = field(default_factory=dict)

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)


class Tool:
    """Base class. Subclasses set `name`, `env_key`, and implement `_run`."""

    name: str = "tool"
    env_key: str | None = None  # env var that must be set for the tool to be usable
    # JSON-schema for the arguments the LLM supplies. Most tools only need a URL.
    parameters: dict[str, Any] = {
        "type": "object",
        "properties": {"url": {"type": "string", "description": "The URL to fetch."}},
        "required": ["url"],
    }

    def __init__(self, api_key: str | None = None, **opts: Any) -> None:
        self.api_key = api_key
        self.opts = opts

    @property
    def available(self) -> bool:
        return self.env_key is None or bool(self.api_key)

    def run(self, task_input: dict[str, Any]) -> ToolResult:
        """Never raises: any failure becomes ok=False with an error string."""
        if not self.available:
            return ToolResult(error=f"{self.name} unavailable: {self.env_key} not set")
        url = task_input.get("url")
        if not url:
            return ToolResult(error="missing url")
        t0 = time.perf_counter()
        try:
            res = self._run(task_input)
        except Exception as e:  # network errors, bad JSON, parser crashes...
            res = ToolResult(error=f"{type(e).__name__}: {e}")
        res.latency_ms = int((time.perf_counter() - t0) * 1000)
        if len(res.content) > MAX_CONTENT_CHARS:
            res.meta["truncated_from"] = len(res.content)
            res.content = res.content[:MAX_CONTENT_CHARS]
        # ok means "returned non-empty content without an error". It does NOT mean
        # the content answers the question; that is scoring's job.
        if res.error is None and not res.content.strip():
            res.error = "empty_content"
        res.ok = res.error is None
        return res

    def _run(self, task_input: dict[str, Any]) -> ToolResult:  # pragma: no cover
        raise NotImplementedError
