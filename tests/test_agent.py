"""Agent loop with a scripted fake LLM and fake tools. No network."""

import json
from types import SimpleNamespace

import pytest

from bench.agent import LIMIT_MSG, run_task, user_message
from bench.config import load_config
from bench.quota import Quota, QuotaPaused
from bench.tools.base import Tool, ToolResult

TASK = {"id": "t", "category": "static", "url": "https://e.org/a", "question": "What is X?"}


@pytest.fixture
def cfg():
    c = load_config()
    c["agent"]["max_tool_calls"] = 3
    c["agent"]["max_content_chars"] = 10
    for k, v in c["quota"].items():
        if isinstance(v, dict) and "rpm" in v:
            v["rpm"] = 0
    for v in c["quota"]["gemini"].values():
        v["rpm"] = 0
    return c


class Echo(Tool):
    name = "plain_fetch"

    def _run(self, task_input):
        return ToolResult(content=f"page {task_input['url']} " + "x" * 50)


def tc(i, url):
    return SimpleNamespace(id=f"c{i}", function=SimpleNamespace(name="fetch", arguments=json.dumps({"url": url})))


def scripted(*replies):
    """Each reply: str (final answer) or list of tool-call urls."""
    seen = []
    it = iter(replies)

    def complete(model, cfg, quota, messages, tools=None, tool_choice=None):
        seen.append({"n_messages": len(messages), "tools": tools, "tool_choice": tool_choice,
                     "last": messages[-1]})
        r = next(it)
        msg = (SimpleNamespace(content=r, tool_calls=None) if isinstance(r, str)
               else SimpleNamespace(content=None, tool_calls=[tc(i, u) for i, u in enumerate(r)]))
        return {"message": msg, "usage": {"in": 10, "out": 2}, "list_usd": 0.001}
    return complete, seen


def test_no_tools_single_request(cfg, tmp_path):
    complete, seen = scripted("X is 42.")
    rec = run_task(TASK, "flash", cfg, Quota(cfg, tmp_path / "q.json"), complete=complete)
    assert rec["final_answer"] == "X is 42." and rec["llm_requests"] == 1 and rec["n_tool_calls"] == 0
    assert seen[0]["tools"] is None
    assert seen[0]["last"]["content"] == user_message(TASK) and "URL: https://e.org/a" in user_message(TASK)


def test_tool_loop_truncates_and_logs(cfg, tmp_path):
    complete, seen = scripted(["https://e.org/a"], "done")
    spec = [{"type": "function", "function": {"name": "fetch"}}]
    rec = run_task(TASK, "flash", cfg, Quota(cfg, tmp_path / "q.json"), {"fetch": Echo()}, spec, complete)
    assert rec["final_answer"] == "done" and rec["n_tool_calls"] == 1
    c = rec["tool_calls"][0]
    assert c["tool"] == "plain_fetch" and c["shown_name"] == "fetch" and c["truncated"]
    assert len(seen[1]["last"]["content"]) == 10  # truncated to max_content_chars


def test_call_cap_enforced(cfg, tmp_path):
    # Model asks for 2 calls, then 2 more (only 1 allowed), then is forced to answer.
    complete, seen = scripted(["https://e.org/1", "https://e.org/2"], ["https://e.org/3", "https://e.org/4"], "final")
    spec = [{"type": "function", "function": {"name": "fetch"}}]
    rec = run_task(TASK, "flash", cfg, Quota(cfg, tmp_path / "q.json"), {"fetch": Echo()}, spec, complete)
    assert rec["n_tool_calls"] == 3
    assert [r["skipped"] for r in rec["rejected_calls"]] == ["limit"]
    assert seen[-1]["tool_choice"] == "none" and rec["final_answer"] == "final"


def test_unknown_tool_rejected(cfg, tmp_path):
    def complete(model, cfg, quota, messages, tools=None, tool_choice=None):
        if len(messages) == 2:
            bad = SimpleNamespace(id="c0", function=SimpleNamespace(name="nope", arguments="{}"))
            return {"message": SimpleNamespace(content=None, tool_calls=[bad]), "usage": {}, "list_usd": 0}
        return {"message": SimpleNamespace(content="ok", tool_calls=None), "usage": {}, "list_usd": 0}
    spec = [{"type": "function", "function": {"name": "fetch"}}]
    rec = run_task(TASK, "flash", cfg, Quota(cfg, tmp_path / "q.json"), {"fetch": Echo()}, spec, complete)
    assert rec["n_tool_calls"] == 0 and rec["rejected_calls"][0]["skipped"] == "unknown_tool"


def test_quota_pause_propagates(cfg, tmp_path):
    def complete(*a, **k):
        raise QuotaPaused("daily")
    with pytest.raises(QuotaPaused):
        run_task(TASK, "flash", cfg, Quota(cfg, tmp_path / "q.json"), complete=complete)


def test_other_errors_are_logged(cfg, tmp_path):
    def complete(*a, **k):
        raise RuntimeError("boom")
    rec = run_task(TASK, "flash", cfg, Quota(cfg, tmp_path / "q.json"), complete=complete)
    assert rec["final_answer"] is None and "boom" in rec["error"]


def test_no_tools_uses_its_own_system_prompt(cfg, tmp_path):
    seen = []

    def complete(model, cfg_, quota, messages, tools=None, tool_choice=None):
        seen.append(messages[0]["content"])
        return {"message": SimpleNamespace(content="a", tool_calls=None), "usage": {}, "list_usd": 0}
    q = Quota(cfg, tmp_path / "q.json")
    run_task(TASK, "flash", cfg, q, complete=complete)
    run_task(TASK, "flash", cfg, q, {"fetch": Echo()}, [{"type": "function", "function": {"name": "fetch"}}], complete)
    assert seen == [cfg["agent"]["system_prompt_no_tools"], cfg["agent"]["system_prompt"]]
