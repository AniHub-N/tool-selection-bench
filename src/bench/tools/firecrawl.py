"""Firecrawl v2 scrape.

Docs (checked 2026-11-08): https://docs.firecrawl.dev/api-reference/endpoint/scrape
POST https://api.firecrawl.dev/v2/scrape, Authorization: Bearer <key>
Response: {success, data: {markdown, metadata: {statusCode, numPages, totalPages, ...}}}
Errors: {success: false, error, code?}; 429 returns only {error}.
"""

from __future__ import annotations

from typing import Any

from ..ratelimit import post_with_backoff
from .base import Tool, ToolResult

ENDPOINT = "https://api.firecrawl.dev/v2/scrape"


class FirecrawlScrape(Tool):
    name = "firecrawl_scrape"
    env_key = "FIRECRAWL_API_KEY"

    def _run(self, task_input: dict[str, Any]) -> ToolResult:
        body = {
            "url": task_input["url"],
            "formats": ["markdown"],
            "onlyMainContent": True,
            "timeout": self.opts.get("timeout_ms", 60_000),
            # PDFs bill per page; cap pages (same cap as plain fetch). Image OCR off.
            "parsers": [{"type": "pdf", "maxPages": self.opts.get("pdf_max_pages", 10)}],
        }
        headers = {"Authorization": f"Bearer {self.api_key}"}
        r = post_with_backoff(ENDPOINT, body, headers, timeout_s=90)
        try:
            payload = r.json()
        except ValueError:
            return ToolResult(error=f"http_{r.status_code}: non-JSON response")
        if r.status_code != 200 or not payload.get("success"):
            code = payload.get("code") or f"http_{r.status_code}"
            return ToolResult(error=f"{code}: {payload.get('error', '')}".strip(),
                              meta={"http_status": r.status_code, "credits": 0})
        data = payload.get("data") or {}
        md = data.get("markdown") or ""
        meta = data.get("metadata") or {}
        credits = _credits(payload, meta)
        cost = credits * self.opts.get("list_usd_per_credit", 0.005)
        out_meta = {"status_code": meta.get("statusCode"), "final_url": meta.get("url"),
                    "content_type": meta.get("contentType"), "credits": credits,
                    "num_pages": meta.get("numPages"), "total_pages": meta.get("totalPages")}
        status = meta.get("statusCode")
        if isinstance(status, int) and status >= 400:
            return ToolResult(content=md, cost_usd=cost, error=f"target_http_{status}", meta=out_meta)
        return ToolResult(content=md, cost_usd=cost, meta=out_meta)


def _credits(payload: dict, meta: dict) -> float:
    """Use creditsUsed if the API reports it, otherwise estimate from the docs:
    1 credit per page; PDFs are billed per page (docs disagree on whether that's
    1 or 2 per page, see NOTES.md; we assume the higher figure, 2)."""
    for v in (payload.get("creditsUsed"), meta.get("creditsUsed")):
        if isinstance(v, (int, float)):
            return float(v)
    pages = meta.get("numPages")
    if isinstance(pages, int) and pages > 0:
        return 2.0 * pages
    return 1.0
