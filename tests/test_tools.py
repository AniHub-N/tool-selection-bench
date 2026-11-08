"""Mocked tests for the tool wrappers. No network."""

import json

import httpx
import pytest
import respx

from bench.tools import load_tools
from bench.tools.firecrawl import ENDPOINT as FC_URL, FirecrawlScrape
from bench.tools.plain import PlainFetch
from bench.tools.tinyfish import AGENT_ENDPOINT, FETCH_ENDPOINT, TinyFishAgent, TinyFishFetch

URL = "https://example.org/page"
HTML = ("<html><head><title>Example Page</title></head><body><nav>menu</nav>"
        "<article><h1>Hello</h1><p>The answer is forty-two, as everyone knows by now. "
        "This paragraph is long enough for readability to keep it.</p></article></body></html>")


def _make_pdf(pages: list[str]) -> bytes:
    """Build a tiny valid PDF with one text line per page (no extra deps)."""
    objs: list[str | None] = ["<< /Type /Catalog /Pages 2 0 R >>", None,
                              "<< /Type /Font /Subtype /Type1 /BaseFont /Helvetica >>"]
    kids = []
    for text in pages:
        stream = f"BT /F1 12 Tf 72 720 Td ({text}) Tj ET"
        objs.append(f"<< /Length {len(stream)} >>\nstream\n{stream}\nendstream")
        objs.append(f"<< /Type /Page /Parent 2 0 R /MediaBox [0 0 612 792] "
                    f"/Resources << /Font << /F1 3 0 R >> >> /Contents {len(objs)} 0 R >>")
        kids.append(f"{len(objs)} 0 R")
    objs[1] = f"<< /Type /Pages /Kids [{' '.join(kids)}] /Count {len(kids)} >>"
    out, offsets = b"%PDF-1.4\n", []
    for i, o in enumerate(objs, 1):
        offsets.append(len(out))
        out += f"{i} 0 obj\n{o}\nendobj\n".encode()
    xref = len(out)
    out += f"xref\n0 {len(objs) + 1}\n0000000000 65535 f \n".encode()
    out += "".join(f"{o:010d} 00000 n \n" for o in offsets).encode()
    out += f"trailer\n<< /Size {len(objs) + 1} /Root 1 0 R >>\nstartxref\n{xref}\n%%EOF\n".encode()
    return out


# ---- plain fetch -------------------------------------------------------------

@respx.mock
def test_plain_html_to_markdown():
    respx.get(URL).mock(return_value=httpx.Response(200, text=HTML, headers={"content-type": "text/html"}))
    r = PlainFetch().run({"url": URL})
    assert r.ok and r.error is None
    assert "forty-two" in r.content
    assert r.cost_usd == 0.0 and r.latency_ms >= 0


@respx.mock
def test_plain_http_error():
    respx.get(URL).mock(return_value=httpx.Response(404, text="nope", headers={"content-type": "text/html"}))
    r = PlainFetch().run({"url": URL})
    assert not r.ok and r.error == "http_404"


@respx.mock
def test_plain_parses_pdf_with_page_cap():
    pdf = _make_pdf(["alpha page one", "bravo page two", "charlie page three"])
    respx.get(URL).mock(return_value=httpx.Response(200, content=pdf,
                                                    headers={"content-type": "application/pdf"}))
    r = PlainFetch(pdf_max_pages=2).run({"url": URL})
    assert r.ok and "alpha page one" in r.content and "bravo page two" in r.content
    assert "charlie" not in r.content
    assert r.meta["total_pages"] == 3 and r.meta["num_pages"] == 2


@respx.mock
def test_plain_detects_pdf_by_magic_bytes():
    pdf = _make_pdf(["sniffed"])
    respx.get(URL).mock(return_value=httpx.Response(200, content=pdf,
                                                    headers={"content-type": "application/octet-stream"}))
    r = PlainFetch().run({"url": URL})
    assert r.ok and "sniffed" in r.content


@respx.mock
def test_plain_rejects_other_binaries():
    respx.get(URL).mock(return_value=httpx.Response(200, content=b"PNG-not-really",
                                                    headers={"content-type": "image/png"}))
    r = PlainFetch().run({"url": URL})
    assert not r.ok and "unsupported_content_type" in r.error


@respx.mock
def test_plain_network_error_does_not_raise():
    respx.get(URL).mock(side_effect=httpx.ConnectError("boom"))
    r = PlainFetch().run({"url": URL})
    assert not r.ok and "ConnectError" in r.error


@respx.mock
def test_plain_falls_back_when_readability_keeps_too_little():
    sections = "".join(f"<dl><dt>func{i}</dt><dd><p>Docs for function number {i}, "
                       f"which does thing {i} with care and precision.</p></dd></dl>" for i in range(30))
    html = f"<html><head><title>T</title></head><body>{sections}<p>needle-at-end</p></body></html>"
    respx.get(URL).mock(return_value=httpx.Response(200, text=html, headers={"content-type": "text/html"}))
    r = PlainFetch().run({"url": URL})
    assert r.ok and "func29" in r.content and "needle-at-end" in r.content


def test_missing_url():
    assert PlainFetch().run({}).error == "missing url"


# ---- firecrawl ---------------------------------------------------------------

@respx.mock
def test_firecrawl_success_and_auth_header():
    route = respx.post(FC_URL).mock(return_value=httpx.Response(200, json={
        "success": True, "data": {"markdown": "# Hi\n\nforty-two", "metadata": {"statusCode": 200}}}))
    r = FirecrawlScrape(api_key="k", list_usd_per_credit=0.01).run({"url": URL})
    assert r.ok and "forty-two" in r.content
    assert r.cost_usd == pytest.approx(0.01) and r.meta["credits"] == 1
    assert route.calls[0].request.headers["authorization"] == "Bearer k"


@respx.mock
def test_firecrawl_sends_pdf_page_cap():
    route = respx.post(FC_URL).mock(return_value=httpx.Response(200, json={
        "success": True, "data": {"markdown": "x", "metadata": {}}}))
    FirecrawlScrape(api_key="k", pdf_max_pages=7).run({"url": URL})
    assert json.loads(route.calls[0].request.read())["parsers"] == [{"type": "pdf", "maxPages": 7}]


@respx.mock
def test_firecrawl_pdf_cost_uses_pages():
    respx.post(FC_URL).mock(return_value=httpx.Response(200, json={
        "success": True, "data": {"markdown": "pdf text", "metadata": {"statusCode": 200, "numPages": 3}}}))
    r = FirecrawlScrape(api_key="k", list_usd_per_credit=0.01).run({"url": URL})
    assert r.cost_usd == pytest.approx(0.06) and r.meta["credits"] == 6


@respx.mock
def test_firecrawl_prefers_reported_credits():
    respx.post(FC_URL).mock(return_value=httpx.Response(200, json={
        "success": True, "creditsUsed": 4, "data": {"markdown": "x", "metadata": {"numPages": 3}}}))
    r = FirecrawlScrape(api_key="k").run({"url": URL})
    assert r.meta["credits"] == 4


@respx.mock
def test_firecrawl_api_error():
    respx.post(FC_URL).mock(return_value=httpx.Response(402, json={
        "success": False, "error": "Payment required", "code": "INSUFFICIENT_CREDITS"}))
    r = FirecrawlScrape(api_key="k").run({"url": URL})
    assert not r.ok and "INSUFFICIENT_CREDITS" in r.error and r.meta["credits"] == 0


@respx.mock
def test_firecrawl_empty_markdown_is_not_ok():
    respx.post(FC_URL).mock(return_value=httpx.Response(200, json={
        "success": True, "data": {"markdown": "   ", "metadata": {"statusCode": 200}}}))
    r = FirecrawlScrape(api_key="k").run({"url": URL})
    assert not r.ok and r.error == "empty_content"


@respx.mock
def test_firecrawl_retries_429(monkeypatch):
    monkeypatch.setattr("bench.ratelimit.time.sleep", lambda s: None)
    route = respx.post(FC_URL).mock(side_effect=[
        httpx.Response(429, json={"error": "rate limited"}),
        httpx.Response(200, json={"success": True, "data": {"markdown": "ok", "metadata": {}}})])
    r = FirecrawlScrape(api_key="k").run({"url": URL})
    assert r.ok and route.call_count == 2


def test_firecrawl_without_key_is_unavailable():
    r = FirecrawlScrape(api_key=None).run({"url": URL})
    assert not r.ok and "FIRECRAWL_API_KEY" in r.error


# ---- tinyfish ----------------------------------------------------------------

@respx.mock
def test_tinyfish_fetch_success():
    route = respx.post(FETCH_ENDPOINT).mock(return_value=httpx.Response(200, json={
        "results": [{"url": URL, "final_url": URL, "title": "T", "text": "forty-two"}], "errors": []}))
    r = TinyFishFetch(api_key="k").run({"url": URL})
    assert r.ok and "forty-two" in r.content
    assert route.calls[0].request.headers["x-api-key"] == "k"


@respx.mock
def test_tinyfish_fetch_per_url_error():
    respx.post(FETCH_ENDPOINT).mock(return_value=httpx.Response(200, json={
        "results": [], "errors": [{"url": URL, "error": "bot_blocked", "status": 403}]}))
    r = TinyFishFetch(api_key="k").run({"url": URL})
    assert not r.ok and r.error.startswith("bot_blocked")


@respx.mock
def test_tinyfish_agent_completed_cost_from_steps():
    route = respx.post(AGENT_ENDPOINT).mock(return_value=httpx.Response(200, json={
        "run_id": "r1", "status": "COMPLETED", "num_of_steps": 4,
        "result": {"answer": "Albert Einstein"}, "error": None}))
    r = TinyFishAgent(api_key="k", usd_per_step=0.01).run({"url": URL, "goal": "who?"})
    assert r.ok and "Albert Einstein" in r.content
    assert r.cost_usd == pytest.approx(0.04) and r.meta["steps"] == 4
    sent = json.loads(route.calls[0].request.read())
    assert sent["agent_config"]["max_steps"] == 5 and sent["goal"] == "who?"


@respx.mock
def test_tinyfish_agent_failed():
    respx.post(AGENT_ENDPOINT).mock(return_value=httpx.Response(200, json={
        "run_id": "r1", "status": "FAILED", "num_of_steps": 2, "result": None,
        "error": {"code": "BLOCKED", "message": "captcha", "category": "AGENT_FAILURE"}}))
    r = TinyFishAgent(api_key="k", usd_per_step=0.01).run({"url": URL, "goal": "x"})
    assert not r.ok and "captcha" in r.error and r.cost_usd == pytest.approx(0.02)


# ---- registry ----------------------------------------------------------------

def test_load_tools_skips_missing_keys(monkeypatch):
    monkeypatch.delenv("FIRECRAWL_API_KEY", raising=False)
    monkeypatch.setenv("TINYFISH_API_KEY", "k")
    cfg = {"tools": {"enabled": ["plain_fetch", "firecrawl_scrape", "tinyfish_fetch", "nope"],
                     "pdf_max_pages": 4}}
    tools, skipped = load_tools(cfg)
    assert [t.name for t in tools] == ["plain_fetch", "tinyfish_fetch"]
    assert tools[0].opts["pdf_max_pages"] == 4
    assert any("FIRECRAWL_API_KEY" in s for s in skipped) and any("unknown" in s for s in skipped)
