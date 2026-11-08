"""Regression test: capture what LiteLLM actually sends to Gemini (via a local fake
endpoint, no network/quota) and assert no built-in Gemini tools are ever attached.
If this fails, Gemini no_tools results are invalid (the model could fetch pages itself)."""

import json
import os
import threading
from http.server import BaseHTTPRequestHandler, HTTPServer

import pytest

os.environ.setdefault("LITELLM_LOCAL_MODEL_COST_MAP", "True")
import litellm  # noqa: E402

BUILTIN = ("url_context", "urlcontext", "google_search", "googlesearch", "code_execution", "retrieval")
TOOL = [{"type": "function", "function": {"name": "fetch", "description": "d", "parameters": {
    "type": "object", "properties": {"url": {"type": "string"}}, "required": ["url"]}}}]


@pytest.fixture(scope="module")
def fake_gemini():
    captured = []

    class H(BaseHTTPRequestHandler):
        def do_POST(self):
            captured.append(json.loads(self.rfile.read(int(self.headers["Content-Length"]))))
            body = json.dumps({"candidates": [{"content": {"parts": [{"text": "ok"}], "role": "model"},
                                               "finishReason": "STOP"}],
                               "usageMetadata": {"promptTokenCount": 1, "candidatesTokenCount": 1,
                                                 "totalTokenCount": 2}}).encode()
            self.send_response(200)
            self.send_header("Content-Type", "application/json")
            self.send_header("Content-Length", str(len(body)))
            self.end_headers()
            self.wfile.write(body)

        def log_message(self, *a):
            pass

    srv = HTTPServer(("127.0.0.1", 0), H)
    threading.Thread(target=srv.serve_forever, daemon=True).start()
    yield f"http://127.0.0.1:{srv.server_port}", captured
    srv.shutdown()


@pytest.mark.parametrize("model", ["gemini/gemini-3.8-flash", "gemini/gemini-3.5-flash-lite"])
@pytest.mark.parametrize("kw", [{}, {"tools": TOOL}, {"tools": TOOL, "tool_choice": "none"}],
                         ids=["no_tools", "with_tools", "tool_choice_none"])
def test_no_builtin_gemini_tools(fake_gemini, model, kw):
    base, captured = fake_gemini
    captured.clear()
    litellm.completion(model=model, messages=[{"role": "user", "content": "u"}], api_base=base,
                       api_key="fake", temperature=0.0, drop_params=True, **kw)
    body = captured[0]
    flat = json.dumps(body).lower()
    assert not [k for k in BUILTIN if k in flat]
    if not kw:
        assert "tools" not in body
    else:
        names = [f["name"] for t in body["tools"] for f in t.get("function_declarations", [])]
        assert names == ["fetch"]
