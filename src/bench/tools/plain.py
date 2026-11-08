"""Baseline: httpx GET + readability + markdownify, with a pypdf fallback for PDFs.
No JavaScript execution, no interaction."""

from __future__ import annotations

import io
from typing import Any

import httpx
import lxml.html
from markdownify import markdownify
from pypdf import PdfReader
from readability import Document

from .base import Tool, ToolResult

USER_AGENT = "tool-selection-bench/0.1 (+research benchmark; httpx)"
FALLBACK_RATIO = 0.25


def _body_markdown(html: str) -> str:
    tree = lxml.html.fromstring(html)
    for bad in tree.xpath("//script|//style|//noscript|//nav|//header|//footer"):
        bad.drop_tree()
    body = tree.find("body")
    return markdownify(lxml.html.tostring(body if body is not None else tree, encoding="unicode"),
                       heading_style="ATX").strip()


def _pdf_text(data: bytes, max_pages: int) -> tuple[str, dict[str, Any]]:
    reader = PdfReader(io.BytesIO(data))
    total = len(reader.pages)
    n = min(total, max_pages)
    parts = []
    for i in range(n):
        text = (reader.pages[i].extract_text() or "").strip()
        if text:
            parts.append(f"## Page {i + 1}\n\n{text}")
    return "\n\n".join(parts), {"num_pages": n, "total_pages": total}


class PlainFetch(Tool):
    name = "plain_fetch"
    env_key = None

    def _run(self, task_input: dict[str, Any]) -> ToolResult:
        timeout = self.opts.get("timeout_s", 30)
        with httpx.Client(follow_redirects=True, timeout=timeout,
                          headers={"User-Agent": USER_AGENT}) as client:
            r = client.get(task_input["url"])
        meta: dict[str, Any] = {"status_code": r.status_code, "final_url": str(r.url),
                                "content_type": r.headers.get("content-type", "")}
        if r.status_code >= 400:
            return ToolResult(error=f"http_{r.status_code}", meta=meta)
        ctype = meta["content_type"].lower()

        if "pdf" in ctype or r.content[:5] == b"%PDF-":
            text, pdf_meta = _pdf_text(r.content, self.opts.get("pdf_max_pages", 10))
            meta.update(pdf_meta, extractor="pypdf")
            return ToolResult(content=text, meta=meta)

        if "html" not in ctype and "xml" not in ctype:
            if ctype.startswith("text/") or "json" in ctype:
                return ToolResult(content=r.text, meta=meta)
            return ToolResult(error=f"unsupported_content_type: {ctype or 'unknown'}", meta=meta)

        doc = Document(r.text)
        md = markdownify(doc.summary(html_partial=True), heading_style="ATX")
        title = doc.short_title()
        meta["title"] = title
        # Readability sometimes keeps one section of a multi-section docs page
        # (seen on docs.python.org). If it kept <25% of the body text, fall back
        # to the whole body. See NOTES.md.
        body_md = _body_markdown(r.text)
        if len(md.strip()) < FALLBACK_RATIO * len(body_md):
            md = body_md
            meta["extractor"] = "body_fallback"
        else:
            meta["extractor"] = "readability"
        content = (f"# {title}\n\n" if title else "") + md.strip() if md.strip() else ""
        return ToolResult(content=content, meta=meta)
