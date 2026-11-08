"""TinyFish Fetch and TinyFish Agent.

Docs (checked 2026-11-08):
- Fetch: https://docs.tinyfish.ai/fetch-api/reference
  POST https://api.fetch.tinyfish.ai, X-API-Key header, body {urls: [...], format}
  Response: {results: [{url, final_url, title, text, ...}], errors: [{url, error, status}]}
- Agent (sync): https://docs.tinyfish.ai/api-reference/automation/run-browser-automation-synchronously
  POST https://agent.tinyfish.ai/v1/automation/run, body {url, goal, browser_profile, agent_config}
  Response: {run_id, status: COMPLETED|FAILED, num_of_steps, result, error}
  The response has no cost field; we estimate cost = steps * usd_per_step.
"""

from __future__ import annotations

import json
from typing import Any

from ..ratelimit import post_with_backoff
from .base import Tool, ToolResult

FETCH_ENDPOINT = "https://api.fetch.tinyfish.ai"
AGENT_ENDPOINT = "https://agent.tinyfish.ai/v1/automation/run"


class TinyFishFetch(Tool):
    name = "tinyfish_fetch"
    env_key = "TINYFISH_API_KEY"

    def _run(self, task_input: dict[str, Any]) -> ToolResult:
        body = {"urls": [task_input["url"]], "format": "markdown",
                "per_url_timeout_ms": self.opts.get("per_url_timeout_ms", 60_000)}
        # Docs: set client timeout >= 150s (120s CDN ceiling).
        r = post_with_backoff(FETCH_ENDPOINT, body, {"X-API-Key": self.api_key}, timeout_s=150)
        try:
            payload = r.json()
        except ValueError:
            return ToolResult(error=f"http_{r.status_code}: non-JSON response")
        if r.status_code != 200:
            err = payload.get("error") if isinstance(payload, dict) else None
            if isinstance(err, dict):
                err = err.get("code") or err.get("message")
            return ToolResult(error=f"http_{r.status_code}: {err or ''}".strip(),
                              meta={"http_status": r.status_code})
        cost = self.opts.get("list_usd_per_url", 0.0)
        results = payload.get("results") or []
        errors = payload.get("errors") or []
        if results:
            res = results[0]
            text = res.get("text")
            if not isinstance(text, str):
                text = json.dumps(text) if text is not None else ""
            title = res.get("title")
            content = (f"# {title}\n\n" if title and text and not text.lstrip().startswith("#") else "") + text
            return ToolResult(content=content if text.strip() else "", cost_usd=cost,
                              meta={"final_url": res.get("final_url"), "title": title})
        if errors:
            e = errors[0]
            status = f" (status {e['status']})" if e.get("status") else ""
            return ToolResult(error=f"{e.get('error', 'unknown_error')}{status}")
        return ToolResult(error="no results and no errors in response")


class TinyFishAgent(Tool):
    """Browser agent: takes a URL and a natural-language goal, can click/type/scroll.
    Much slower and pricier than fetch, so it's opt-in via config."""

    name = "tinyfish_agent"
    env_key = "TINYFISH_API_KEY"
    parameters = {
        "type": "object",
        "properties": {
            "url": {"type": "string", "description": "The URL to start from."},
            "goal": {"type": "string", "description": "What to do or find on the page, in plain language."},
        },
        "required": ["url", "goal"],
    }

    def _run(self, task_input: dict[str, Any]) -> ToolResult:
        goal = task_input.get("goal") or "Return the main text content of this page."
        body = {
            "url": task_input["url"],
            "goal": goal,
            "browser_profile": self.opts.get("browser_profile", "lite"),
            "agent_config": {
                "max_steps": self.opts.get("max_steps", 5),
                "max_duration_seconds": self.opts.get("max_duration_seconds", 120),
            },
        }
        timeout = self.opts.get("max_duration_seconds", 120) + 60
        r = post_with_backoff(AGENT_ENDPOINT, body, {"X-API-Key": self.api_key},
                             timeout_s=timeout, retries=2)  # a 429 means the run never started, so retrying is safe
        try:
            payload = r.json()
        except ValueError:
            return ToolResult(error=f"http_{r.status_code}: non-JSON response")
        steps = payload.get("num_of_steps") or 0
        cost = steps * self.opts.get("usd_per_step", 0.016)
        meta = {"run_id": payload.get("run_id"), "steps": steps, "status": payload.get("status")}
        err = payload.get("error")
        if r.status_code != 200 or payload.get("status") != "COMPLETED":
            if isinstance(err, dict):
                msg = f"{err.get('code', '')}: {err.get('message', '')}".strip(": ")
            else:
                msg = str(err) if err else f"http_{r.status_code}"
            return ToolResult(cost_usd=cost, error=msg or "agent_failed", meta=meta)
        result = payload.get("result")
        content = result if isinstance(result, str) else json.dumps(result, ensure_ascii=False)
        # Docs warn COMPLETED != goal achieved; we keep the content and let scoring decide.
        return ToolResult(content=content if result else "", cost_usd=cost, meta=meta)
