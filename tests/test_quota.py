"""Quota, rate limiter, backoff and resume. No network."""

import json

import pytest

import bench.quota as quota_mod
from bench.config import load_config
from bench.quota import Quota, QuotaPaused
from bench.ratelimit import TokenBucket, backoff_delay, retry_call
from bench.tools.base import Tool, ToolResult


@pytest.fixture
def cfg():
    c = load_config()
    c["quota"]["gemini"]["flash"].update(rpd=10, stop_frac=0.9)
    c["quota"]["firecrawl_scrape"].update(stop_at=5, free_credits=10)
    c["quota"]["tinyfish_agent"].update(cap_usd=0.10, min_wallet_usd=0.5)
    for k in c["quota"]:
        if isinstance(c["quota"][k], dict) and "rpm" in c["quota"][k]:
            c["quota"][k]["rpm"] = 0  # no sleeping in tests
    for k in c["quota"]["gemini"].values():
        k["rpm"] = 0
    return c


class FakeTool(Tool):
    def __init__(self, name, result):
        super().__init__()
        self.name, self._result = name, result

    def _run(self, task_input):
        return ToolResult(content=self._result.content, cost_usd=self._result.cost_usd,
                          meta=dict(self._result.meta))


# ---- gemini RPD -------------------------------------------------------------

def test_gemini_pauses_at_stop_frac(cfg, tmp_path):
    q = Quota(cfg, tmp_path / "q.json")
    for _ in range(9):          # 90% of 10
        q.check("gemini:flash", 1)
        q.record("gemini:flash", 1, 0.0)
    with pytest.raises(QuotaPaused, match="midnight Pacific"):
        q.check("gemini:flash", 1)


def test_gemini_resets_on_new_pacific_day(cfg, tmp_path, monkeypatch):
    q = Quota(cfg, tmp_path / "q.json")
    for _ in range(9):
        q.record("gemini:flash", 1, 0.0)
    monkeypatch.setattr(quota_mod, "pt_day", lambda: "2099-01-01")
    q.check("gemini:flash", 1)  # no raise
    assert q.used("gemini:flash") == 0


def test_ledger_persists(cfg, tmp_path):
    p = tmp_path / "q.json"
    q = Quota(cfg, p)
    q.record("firecrawl_scrape", 3, 0.015)
    q2 = Quota(cfg, p)
    assert q2.used("firecrawl_scrape") == 3
    assert json.loads(p.read_text())["list_usd_total"] == pytest.approx(0.015)


# ---- firecrawl ---------------------------------------------------------------

def test_firecrawl_local_stop(cfg, tmp_path):
    q = Quota(cfg, tmp_path / "q.json")
    q.record("firecrawl_scrape", 5, 0.0)
    with pytest.raises(QuotaPaused):
        q.check("firecrawl_scrape", 1)


def test_firecrawl_live_reserve(cfg, tmp_path):
    q = Quota(cfg, tmp_path / "q.json")
    q.live["firecrawl_remaining"] = 6     # reserve = 10 - 5 = 5
    q.check("firecrawl_scrape", 1)       # 6 - 1 = 5 >= 5, fine
    with pytest.raises(QuotaPaused, match="reserve"):
        q.check("firecrawl_scrape", 2)


def test_firecrawl_pdf_estimate_is_pessimistic(cfg, tmp_path):
    q = Quota(cfg, tmp_path / "q.json")
    assert q.estimate_tool_units("firecrawl_scrape", "pdf") == 2 * cfg["tools"]["pdf_max_pages"]
    assert q.estimate_tool_units("firecrawl_scrape", "static") == 1


# ---- tinyfish agent ----------------------------------------------------------

def test_agent_cap_checks_worst_case(cfg, tmp_path):
    q = Quota(cfg, tmp_path / "q.json")
    worst = q.estimate_tool_units("tinyfish_agent")   # 5 steps * 0.016 = 0.08
    assert worst == pytest.approx(0.08)
    q.check("tinyfish_agent", worst)                    # 0 + 0.08 <= 0.10
    q.record("tinyfish_agent", 0.03, 0.03, {"steps": 2})
    with pytest.raises(QuotaPaused, match="does not reset"):
        q.check("tinyfish_agent", worst)                # 0.03 + 0.08 > 0.10


def test_agent_live_wallet_floor(cfg, tmp_path):
    cfg["quota"]["tinyfish_agent"]["cap_usd"] = 100
    q = Quota(cfg, tmp_path / "q.json")
    q.live["tinyfish_wallet"] = 0.60
    q.check("tinyfish_agent", 0.08)          # 0.60 - 0.08 = 0.52 >= 0.50 floor
    q.session["tinyfish_agent"] = 0.05       # spent this session
    with pytest.raises(QuotaPaused, match="wallet"):
        q.check("tinyfish_agent", 0.08)      # 0.55 - 0.08 = 0.47 < 0.50


def test_run_tool_records_agent_cost_from_steps(cfg, tmp_path):
    q = Quota(cfg, tmp_path / "q.json")
    q.agent_usd_per_step = 0.02   # as if the live wallet rate differed
    tool = FakeTool("tinyfish_agent", ToolResult(content="x", meta={"steps": 3}))
    res = q.run_tool(tool, {"url": "https://e.org"})
    assert res.cost_usd == pytest.approx(0.06)
    assert q.used("tinyfish_agent") == pytest.approx(0.06)
    assert q.data["tinyfish_agent"]["steps"] == 3


def test_run_tool_refuses_before_calling(cfg, tmp_path):
    q = Quota(cfg, tmp_path / "q.json")
    q.record("firecrawl_scrape", 5, 0.0)
    called = []

    class Boom(FakeTool):
        def _run(self, task_input):
            called.append(1)
            return super()._run(task_input)

    with pytest.raises(QuotaPaused):
        q.run_tool(Boom("firecrawl_scrape", ToolResult(content="x")), {"url": "https://e.org"})
    assert not called


# ---- rate limiter + backoff ----------------------------------------------

def test_token_bucket_spaces_calls():
    t = [0.0]
    slept = []

    def sleep(s):
        slept.append(s)
        t[0] += s

    b = TokenBucket(per_minute=30, clock=lambda: t[0], sleep=sleep)
    b.acquire()                # first is free
    b.acquire()                # must wait 2s (30/min)
    assert slept == [pytest.approx(2.0)]
    t[0] += 5                  # idle; tokens refill to cap=1 only
    b.acquire()
    assert len(slept) == 1


def test_backoff_delay():
    assert backoff_delay(0) == 2 and backoff_delay(3) == 16
    assert backoff_delay(10) == 60
    assert backoff_delay(0, retry_after=7) == 7


def test_retry_call_retries_then_succeeds():
    attempts = []

    def fn():
        attempts.append(1)
        if len(attempts) < 3:
            raise ValueError("429")
        return "ok"

    assert retry_call(fn, lambda e: 0.0, sleep=lambda s: None) == "ok"
    assert len(attempts) == 3


def test_retry_call_non_retryable_propagates():
    with pytest.raises(KeyError):
        retry_call(lambda: {}["x"], lambda e: None, sleep=lambda s: None)


# ---- NVIDIA trial credits ----------------------------------------------------

def test_nvidia_credits_shared_all_time_and_pause_at_stop(cfg, tmp_path):
    from bench.quota import llm_resource
    cfg["quota"]["nvidia"].update(stop_at=3)
    assert llm_resource(cfg, "nemotron") == "nvidia"
    assert llm_resource(cfg, "flash-lite") == "gemini:flash-lite"
    ledger = tmp_path / "q.json"
    q = Quota(cfg, ledger)
    for _ in range(3):
        q.check("nvidia", 1)
        q.record("nvidia", 1, 0.0)
    q.record("nvidia", 0, 0.001, {"tokens_in": 10, "tokens_out": 2})
    with pytest.raises(QuotaPaused, match="do not reset"):
        q.check("nvidia", 1)
    # all-time: a fresh process (new day or not) still sees the spent credits
    q2 = Quota(cfg, ledger)
    assert q2.used("nvidia") == 3 and json.loads(ledger.read_text())["nvidia"]["tokens_in"] == 10


def test_nvidia_provider_kwargs_are_explicit(monkeypatch):
    from bench.llm import api_key, provider_kwargs
    cfg = load_config()
    m = cfg["models"]["nemotron"]
    monkeypatch.delenv("NVIDIA_API_KEY", raising=False)
    monkeypatch.setenv("NVIDIA_KEY", "k")
    assert api_key(m) == "k"
    kw = provider_kwargs(m)
    assert kw == {"api_base": "https://integrate.api.nvidia.com/v1", "api_key": "k"}
    assert provider_kwargs(cfg["models"]["flash-lite"]) == {}
